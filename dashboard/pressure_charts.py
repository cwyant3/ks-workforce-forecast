"""Presentation views over the adopted observations; no source or model mutation."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from textwrap import wrap

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_contracts import KEY, latest_observations
from geography import WICHITA_OEWS_COUNTIES, inbound_labor_shed

BLUE = "#315E88"
TEAL = "#36816B"
GOLD = "#BA793B"
GRAY = "#B8C1C9"
INDUSTRIES = {"22":"Utilities", "23":"Construction", "31-33":"Manufacturing",
              "51":"Information", "54":"Professional services", "62":"Health care and social assistance",
              "71":"Arts, entertainment and recreation", "72":"Accommodation and food services", "81":"Other services"}


@lru_cache(maxsize=1)
def program_titles():
    """Official NCES CIP 2020 titles, bundled for offline presentation."""
    lookup = pd.read_csv(Path(__file__).resolve().parents[1] / "crosswalks" / "CIPCode2020.csv",
                         usecols=["CIPCode", "CIPTitle"], dtype=str, keep_default_na=False)
    codes = lookup.CIPCode.str.replace('="', '', regex=False).str.replace('"', '', regex=False)
    return dict(zip(codes, lookup.CIPTitle.str.rstrip(".")))


def series(frame, source, measure, county=None, industry=None, occupation=None):
    rows = frame[frame.source_id.eq(source) & frame.measure_id.eq(measure)].copy()
    if county is not None:
        rows = rows[rows.geography_id.eq(county)]
    if industry is not None:
        rows = rows[rows.industry_code.eq(industry)]
    if occupation is not None:
        rows = rows[rows.occupation_code.eq(occupation)]
    return rows.sort_values(["period_end", "period_start"])


def current(rows):
    if rows.empty:
        return None
    latest = latest_observations(rows)
    # A card must describe exactly one native series, never collapse universes.
    if len(latest) != 1:
        raise ValueError("Select one geography and universe for a chart card")
    return latest.iloc[0]


def available(row):
    return row is not None and row.availability_status == "published" and pd.notna(row.estimate)


def number(value, unit=""):
    if value is None or pd.isna(value):
        return "Unavailable"
    if "percent" in unit:
        return f"{value:,.1f}%"
    if unit.startswith("USD"):
        return f"${value:,.0f}"
    if "per 100" in unit:
        return f"{value:,.1f}"
    return f"{value:,.0f}"


def confidence_label(value):
    if pd.isna(value):
        return "confidence level unspecified"
    percent=float(value)*100 if 0<float(value)<=1 else float(value)
    return f"{percent:g}% confidence"


def peers(frame, row):
    """Exact-period and exact-universe peers, including the selected county."""
    if row is None or row.geography_type != "county":
        return frame.iloc[:0].copy()
    mask = pd.Series(True, index=frame.index)
    for col in KEY:
        if col != "geography_id":
            mask &= frame[col].fillna("").eq(row[col] if pd.notna(row[col]) else "")
    return frame[mask & frame.availability_status.eq("published") & frame.estimate.notna()].copy()


def style(fig, *, height=300, x_title=None):
    fig.update_layout(height=height, margin=dict(l=8,r=18,t=22,b=30),
                      font=dict(family="Arial, sans-serif",size=13),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis_title=x_title, hoverlabel=dict(font_size=13),
                      legend=dict(orientation="h",y=1.15,x=0),
                      separators=".,")
    fig.update_xaxes(showgrid=False,zeroline=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)",zeroline=False)
    return fig


def comparison_figure(values, selected):
    values = pd.to_numeric(pd.Series(values),errors="coerce").dropna()
    q1, median, q3 = values.quantile([.25,.5,.75])
    lo, hi = values.min(), values.max()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[lo,hi],y=[0,0],mode="lines",line=dict(color=GRAY,width=3),hoverinfo="skip",showlegend=False))
    fig.add_trace(go.Scatter(x=[q1,q3],y=[0,0],mode="lines",line=dict(color=GRAY,width=12),name="Middle half of counties",hoverinfo="skip",showlegend=False))
    fig.add_trace(go.Scatter(x=[median],y=[0],mode="markers",marker=dict(symbol="line-ns",size=20,color="#7F8C8D",line_width=2),name="Median county",hovertemplate="Median county: %{x:,.2f}<extra></extra>",showlegend=False))
    fig.add_trace(go.Scatter(x=[selected],y=[0],mode="markers",marker=dict(size=12,color=BLUE,line=dict(color="white",width=2)),name="Selected county",hovertemplate="Selected county: %{x:,.2f}<extra></extra>",showlegend=False))
    spread=max(hi-lo, abs(hi)*.05,1)
    fig.update_layout(height=78,margin=dict(l=12,r=12,t=10,b=25),paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(range=[lo-spread*.1,hi+spread*.1],tickvals=[lo,hi] if lo!=hi else [lo],tickformat=",.1f",showgrid=False,zeroline=False),
                      yaxis=dict(visible=False,range=[-1,1]))
    return fig


def draw(fig, key):
    st.plotly_chart(fig, width="stretch",key=key,config={"displaylogo":False,"toImageButtonOptions":{"format":"png","scale":2}})


def metric_card(frame, rows, title, key, *, peer_label=None, color=BLUE):
    row = current(rows)
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.markdown(f"## {number(row.estimate,row.unit) if available(row) else 'Unavailable'}")
        if row is None:
            st.caption("No adopted observations for this selection.")
            return
        st.caption(f"{row.reference_label or row.period_end} · {row.unit}")
        if not available(row):
            st.caption(row.availability_status.replace("_"," ").capitalize()+". No zero is substituted.")
            return
        if pd.notna(row.get("moe")):
            moe_unit="percentage points" if row.unit=="percent" else row.unit
            st.caption(f"±{row.moe:,.1f} {moe_unit} margin of error ({confidence_label(row.moe_confidence_level)})")
        if str(row.get("preliminary",False)).lower() == "true":
            st.caption("Preliminary estimate")
        if (rows.availability_status.eq("published") & rows.estimate.notna()).sum()>1:
            trend=rows.copy()
            trend.loc[~trend.availability_status.eq("published"),"estimate"]=np.nan
            fig=go.Figure(go.Scatter(x=trend.period_end,y=trend.estimate,mode="lines+markers",connectgaps=False,
                                    line=dict(color=color,width=2),marker_size=5,
                                    customdata=trend.reference_label,
                                    hovertemplate="%{customdata}<br>%{y:,.2f}<extra></extra>"))
            style(fig,height=105)
            fig.update_layout(margin=dict(l=0,r=6,t=6,b=20),showlegend=False)
            fig.update_yaxes(visible=False)
            fig.update_xaxes(tickvals=[trend.period_end.iloc[0],trend.period_end.iloc[-1]],tickformat="%b %Y")
            draw(fig,key+"_spark")
        peer=peers(frame,row)
        if peer_label and len(peer)>1:
            draw(comparison_figure(peer.estimate,row.estimate),key+"_peers")
            st.caption(f"Dot: selected county · tick: median {number(peer.estimate.median(),row.unit)} · band: middle half. {len(peer)} published {peer_label}; same period and universe. Unweighted county comparison.")


def horizontal(values, labels, title, *, color=BLUE, unit="", height=None):
    fig=go.Figure(go.Bar(x=values,y=labels,orientation="h",marker_color=color,
                        text=[number(v,unit) for v in values],textposition="auto",
                        hovertemplate="%{y}<br>%{x:,.2f}<extra></extra>"))
    style(fig,height=height or max(220,len(labels)*35+65),x_title=title)
    fig.update_yaxes(autorange="reversed",showgrid=False,automargin=True)
    fig.update_xaxes(rangemode="tozero")
    return fig


def evidence(rows, label="Sources and underlying observations"):
    with st.expander(label):
        if rows.empty:
            st.caption("No adopted observations for this selection.")
        else:
            cols=[c for c in ["source_id","measure_id","geography_id","institution_name","program_code","credential_level","completions","response_flag_status","imputation_status","industry_code","occupation_code","estimate","unit","reference_label","availability_status","moe","rse","source_url","coverage_note"] if c in rows]
            st.dataframe(rows[cols],hide_index=True,width="stretch")


def workforce(frame,county,key,annual_laus=None):
    st.markdown("### Workforce")
    st.write("Resident labor-force size, participation and unemployment describe different parts of the workforce picture.")
    specs=[("laus","labor_force","Residents in the labor force"),
           ("acs_s2301","labor_force_participation_rate_16_plus","Labor-force participation"),
           ("laus","unemployment_rate","Unemployment rate")]
    for col,(src,measure,title) in zip(st.columns(3),specs):
        with col:
            metric_card(frame,series(frame,src,measure,county),title,key+measure,
                        peer_label="Kansas counties" if src.startswith("acs") else "pilot counties",color=TEAL)
    if annual_laus is not None:
        from dashboard.demographic_chart import render_demographic_pipeline
        render_demographic_pipeline(county,key+"resident_pipeline_")
    st.markdown("#### Residents age 16 and older")
    labels=[("civilian_employed_16_plus","Employed civilians"),("civilian_unemployed_16_plus","Unemployed civilians"),
            ("armed_forces_16_plus","Armed forces"),("not_in_labor_force_16_plus","Outside the labor force")]
    obs=[current(series(frame,"acs_b23025",m,county)) for m,_ in labels]
    if all(available(r) for r in obs):
        fig=horizontal([r.estimate for r in obs],[label for _,label in labels],"Residents · ACS 2020–2024",color=[TEAL,GOLD,GRAY,BLUE])
        fig.update_traces(error_x=dict(type="data",array=[r.moe for r in obs],visible=True))
        draw(fig,key+"composition")
        st.caption("Error bars show published 90% margins of error. Outside the labor force includes people who may not want or be able to work; it is not a recruiting pool.")
    evidence(frame[frame.geography_id.eq(county)&frame.source_id.str.startswith(("acs","laus"))])


def demand(frame,county,industry,names,key):
    st.markdown("### Employment and hiring")
    st.caption(f"{INDUSTRIES.get(industry,industry)} · private-sector workplace measures. QCEW and QWI retain separate job definitions and periods.")
    specs=[("qcew","qcew_employment","Workplace jobs"),("qcew","qcew_employment_yoy_pct","Employment change from a year earlier"),
           ("qcew","qcew_weekly_wage","Average weekly pay")]
    for col,(src,m,title) in zip(st.columns(3),specs):
        with col:
            metric_card(frame,series(frame,src,m,county,industry),title,key+m,peer_label="pilot counties")
    row=current(series(frame,"qcew","qcew_employment_yoy_pct",county,industry))
    matches=peers(frame,row)
    if not matches.empty:
        matches=matches.sort_values("estimate",ascending=False)
        st.markdown("#### Employment growth across the pilot counties")
        draw(horizontal(matches.estimate.tolist(),[names.get(f,f) for f in matches.geography_id],
                        "Change from the same quarter a year earlier (%)",unit="percent",color=[BLUE if f==county else GRAY for f in matches.geography_id]),key+"growth")
        st.caption(f"{row.reference_label} · {len(matches)} counties with published matched-period values. Suppressed comparisons are omitted, not ranked at zero.")
    if industry=="31-33":
        st.markdown("#### Manufacturing hiring activity")
        for col,m,title in zip(st.columns(2),["qwi_hires","qwi_hires_per_100_beginning_jobs"],["Quarterly hires","Hires per 100 beginning-quarter jobs"]):
            with col: metric_card(frame,series(frame,"qwi",m,county,industry),title,key+m,peer_label="pilot counties")
        st.caption("Hires count hiring events, not open vacancies. Four-quarter totals and some separation/earnings measures remain unavailable because their required history or source estimates are incomplete.")
    evidence(frame[frame.geography_id.eq(county)&frame.industry_code.eq(industry)&frame.source_id.isin(["qcew","qwi"])])


def pay(frame,county,key):
    st.markdown("### Occupation pay in the surrounding area")
    if county not in WICHITA_OEWS_COUNTIES:
        st.info("Nonmetropolitan OEWS occupation pay has not been adopted for this county. The industry wage card above remains a separate county measure.")
        return
    rows=frame[frame.source_id.eq("oews")&frame.geography_id.eq("48620")]
    if rows.empty:
        return
    titles=rows.drop_duplicates("occupation_code").set_index("occupation_code").occupation_title.to_dict()
    soc=st.selectbox("Occupation pay",sorted(titles),format_func=lambda x:f"{titles[x]} ({x})",key=key+"soc")
    rows=rows[rows.occupation_code.eq(soc)]
    cols=st.columns(2)
    for col,m,title in zip(cols,["oews_median_annual_wage","oews_mean_annual_wage"],["Median annual pay","Mean annual pay"]):
        with col: metric_card(frame,rows[rows.measure_id.eq(m)],title,key+m)
    wage=rows[rows.measure_id.isin(["oews_mean_annual_wage","oews_median_annual_wage"])&rows.availability_status.eq("published")]
    if not wage.empty:
        draw(horizontal(wage.estimate.tolist(),["Mean" if "mean" in m else "Median" for m in wage.measure_id],"Annual pay (USD)",unit="USD/year"),key+"wages")
    st.caption("Wichita area only: Butler, Harvey, Sedgwick and Sumner. May 2025 OEWS; four reviewed occupation groups. Area pay is not a county estimate. Source details retain employment and mean-wage RSE; median wages do not inherit mean-wage RSE.")
    evidence(rows)


def _industry_weights():
    from dashboard.award_industries import load_industry_weights
    try:
        return load_industry_weights()
    except (OSError,ValueError):
        return None,None


def training(frame,detail,county,key,industry=None):
    st.markdown("### Entry and training")
    st.caption("Credentials awarded at institutions in the county · academic year 2024–2025 · provisional IPEDS collection 2025.")
    selected=detail[detail.geography_id.eq(county)].copy() if not detail.empty else detail
    weights,year=_industry_weights() if industry else (None,None)
    focus=industry is not None and weights is not None and not selected.empty
    if focus:
        from dashboard.award_industries import program_industry_share
        from dashboard.training_pathways import load_crosswalk
        share=program_industry_share(selected.program_code,load_crosswalk(),weights,industry)
        selected["industry_awards"]=pd.to_numeric(selected.completions,errors="coerce")*selected.program_code.map(share).fillna(0)
    total_col,focus_col=st.columns(2) if focus else (st.container(),None)
    with total_col:
        metric_card(frame,series(frame,"ipeds","credentials_awarded",county),"Reported credential awards · all programs",key+"total")
    if selected.empty:
        st.info("No reporting institution observations for this county in the adopted collection. This does not establish that no training providers exist.")
        return
    if industry and weights is None:
        st.info("The national occupation-by-industry employment file is unavailable, so awards cannot be tied to the selected industry. All reported awards are shown.")
    provider=st.selectbox("Training institution",["All reported institutions",*sorted(selected.institution_name.unique())],key=key+"provider")
    if provider!="All reported institutions":selected=selected[selected.institution_name.eq(provider)]
    name=INDUSTRIES.get(industry,industry)
    if focus:
        with focus_col:
            st.metric(f"Estimated awards related to {name}",f"{selected.industry_awards.sum():,.0f}",
                      help="Estimate. Each award is split across its program's related occupations, then by each occupation's national share of employment in this industry.")
        scope=st.radio("Awards shown",[f"Related to {name} (estimate)","All reported awards"],horizontal=True,key=key+"scope")
        focus=scope!="All reported awards"
    measure,unit_label="industry_awards" if focus else "completions",f"Estimated {name} awards" if focus else "Awards"
    groups=selected.groupby("credential_level",dropna=False)[measure].sum(min_count=1).dropna()
    groups=groups[groups.round(0)>0].sort_values(ascending=False)
    if not groups.empty:
        st.markdown("#### Awards by credential level" + (f" · related to {name}" if focus else ""))
        draw(horizontal(groups.round(0).tolist(),[str(x).replace("_"," ").capitalize() for x in groups.index],unit_label,color=TEAL),key+"credentials")
    top=selected.groupby("program_code")[measure].sum(min_count=1).dropna()
    top=top[top.round(0)>0].sort_values(ascending=False).head(10)
    if not top.empty:
        st.markdown("#### Largest reported programs" + (f" · related to {name}" if focus else ""))
        titles = program_titles()
        labels = [titles.get(str(code), f"Title unavailable (CIP {code})") for code in top.index]
        wrapped = ["<br>".join(wrap(label, width=34)) for label in labels]
        fig = horizontal(top.round(0).tolist(), top.index.tolist(), f"{unit_label} · top 10 programs", color=BLUE,
                         height=max(420, sum(max(1, label.count("<br>")+1)*20+16 for label in wrapped)+65))
        fig.update_yaxes(type="category", tickmode="array", tickvals=top.index.tolist(), ticktext=wrapped)
        fig.update_traces(customdata=list(zip(labels, top.index)),
                          hovertemplate="%{customdata[0]}<br>CIP %{customdata[1]}<br>%{x:,.0f} "+("estimated awards" if focus else "awards")+"<extra></extra>")
        draw(fig,key+"programs")
    elif focus:
        st.info(f"No reported programs here relate to {name} occupations through the crosswalk.")
    if focus:
        st.caption(f"ESTIMATE · Awards are tied to {name} by splitting each award equally across its program's related occupations (NCES CIP 2020–SOC 2018), "
                   f"then keeping each occupation's national share of employment in {name} (BLS OEWS {year}). "
                   "A program with 100 awards whose occupations are 40% employed in this industry contributes 40. "
                   "This reflects the national industry mix of related occupations, not where graduates work or local hiring.")
    st.caption("Awards may exceed the number of people. They do not establish local placements or new workers. Program titles use the NCES CIP 2020 classification; hover for the CIP code. The ranking is award volume, not employment demand or program quality.")
    st.caption("Program title source: [NCES Classification of Instructional Programs](https://nces.ed.gov/ipeds/cipcode/Files/CIPCode2020.csv).")
    award_industry_chart(selected,key+"industries",industry)
    evidence(selected,"Institution totals, reporting flags and program records")


def award_industry_chart(selected,key,industry=None):
    """Estimated industry mix of awards; an allocation, never a placement record."""
    from dashboard.award_industries import allocate_awards, OTHER_INDUSTRIES
    from dashboard.training_pathways import load_crosswalk
    weights,year=_industry_weights()
    if weights is None:
        st.info("The national occupation-by-industry employment file is unavailable, so awards are not broken down by industry.")
        return
    mix=allocate_awards(selected,load_crosswalk(),weights)
    if mix.empty:return
    labels=[f"{INDUSTRIES[c]} (NAICS {c})" if c in INDUSTRIES else c for c in mix.index]
    colors=[GOLD if c==industry else "#C9CED6" for c in mix.index] if industry in mix.index else GOLD
    st.markdown("#### Estimated awards by related industry" + (f" · {INDUSTRIES.get(industry,industry)} highlighted" if industry in mix.index else ""))
    fig=horizontal(mix.round(0).tolist(),labels,"Estimated awards",color=colors,height=max(320,len(mix)*34+80))
    fig.update_traces(hovertemplate="%{y}<br>%{x:,.0f} estimated awards<extra></extra>")
    draw(fig,key)
    other=mix.get(OTHER_INDUSTRIES,0)/mix.sum()
    st.caption(f"ESTIMATE · Each award is split equally across the occupations its program relates to (NCES CIP 2020–SOC 2018), "
               f"then across industries by each occupation's national employment mix (BLS OEWS {year}). Totals match reported awards. "
               f"Only the nine dashboard industries are shown separately; {other:.0%} of awards fall in all other industries "
               "(for example education, retail, finance and public administration). This is the national industry mix of related occupations, "
               "not where graduates work, local hiring or worker supply.")


def movement(frame,flows,county,names,key):
    st.markdown("### Where workers commute from")
    st.caption("LODES 2023 primary jobs · existing home-to-work connections, not migration or extra available workers.")
    total=current(series(frame,"lodes","total_jobs_at_worksite",county))
    inbound=current(series(frame,"lodes","jobs_workers_imported",county))
    for col,m,title in zip(st.columns(3),["total_jobs_at_worksite","jobs_workers_imported","pct_workers_imported"],["Primary jobs at workplaces","Jobs held by incoming commuters","Incoming commuter share"]):
        with col:metric_card(frame,series(frame,"lodes",m,county),title,key+m)
    if available(total) and available(inbound):
        fig=go.Figure()
        for label,value,color in [("Same-county residents",total.estimate-inbound.estimate,BLUE),("Incoming commuters",inbound.estimate,TEAL)]:
            fig.add_trace(go.Bar(x=[value],y=["Primary jobs"],orientation="h",name=label,marker_color=color,text=[number(value)],textposition="auto"))
        style(fig,height=190,x_title="Primary jobs at workplaces");fig.update_layout(barmode="stack")
        draw(fig,key+"split")
    if flows is not None and not flows.empty:
        share=st.select_slider("Share of existing jobs covered by the labor shed",options=[70,80,90],value=80,key=key+"share")
        shed=inbound_labor_shed(flows,[county],share/100)
        if not shed.empty:
            st.markdown(f"#### Home counties accounting for at least {share}% of primary jobs")
            values=shed.jobs.tolist();labels=[names.get(f,"County FIPS "+f) for f in shed.h_county_fips]
            if len(values)>12:
                values=values[:11]+[sum(values[11:])];labels=labels[:11]+["Other counties in this labor shed"]
            draw(horizontal(values,labels,"Primary jobs",color=TEAL),key+"origins")
            st.caption(f"Shown origins account for {shed.cumulative_share.iloc[-1]:.1%} of observed workplace primary jobs. The county crossing the chosen threshold is included. Complete inbound coverage includes interstate origins; complete outbound coverage is not available.")
    evidence(frame[frame.source_id.eq("lodes")&frame.geography_id.eq(county)])


def regional_view(regional,county,key):
    from geography import SOUTH_CENTRAL_COUNTIES
    st.markdown("### Regional growth and openings")
    if county not in SOUTH_CENTRAL_COUNTIES or regional.empty:
        st.info("Regional occupation charts in this pilot cover the seven-county South Central region.")
        return
    rows=regional[regional.geography_id.eq("KS-SC")]
    titles=rows.drop_duplicates("occupation_code").set_index("occupation_code").occupation_title.to_dict()
    soc=st.selectbox("Projected occupation",sorted(titles),format_func=lambda x:f"{titles[x]} ({x})",key=key+"soc")
    rows=rows[rows.occupation_code.eq(soc)]
    st.caption("South Central Kansas · published 2022–2032 projections. The same regional estimate applies to all seven member counties; it is not allocated to the selected county.")
    values={r.measure_id:r for _,r in rows.iterrows()}
    labels={"ks_occ_annual_exits":"Labor-force exits", "ks_occ_annual_transfers":"Occupation transfers", "ks_occ_annual_change":"Employment growth"}
    available_rows=[(label,values[m]) for m,label in labels.items() if m in values and available(values[m])]
    cols=st.columns(2)
    with cols[0]:
        metric_card(rows,rows[rows.measure_id.eq("ks_occ_annual_openings")],"Projected annual openings",key+"openings")
        if available_rows:draw(horizontal([r.estimate for _,r in available_rows],[l for l,_ in available_rows],"Annual components of projected openings",color=[GRAY,BLUE,TEAL][:len(available_rows)]),key+"components")
    with cols[1]:
        emp=[values.get("ks_occ_base_emp"),values.get("ks_occ_proj_emp")]
        if all(available(r) for r in emp):
            st.markdown("#### Employment outlook")
            draw(horizontal([r.estimate for r in emp],["2022 baseline","2032 projection"],"Jobs",color=[GRAY,BLUE]),key+"outlook")
    st.caption("Projected openings combine growth, occupational transfers and labor-force exits. They are annual projections, not current vacancies; exits are not measured retirements.")
    evidence(rows)


def render_county_brief(frame,detail,flows,regional,county,county_name,names,annual_laus=None):
    st.markdown("<div style='font-size:.8rem;letter-spacing:.12em;color:#BA793B;font-weight:700'>COUNTY WORKFORCE BRIEF</div>",unsafe_allow_html=True)
    st.title(county_name)
    st.write("A visual view of workforce participation, employer activity, training and commuting.")
    industry=st.selectbox("Industry focus",list(INDUSTRIES),index=list(INDUSTRIES).index("31-33"),format_func=lambda x:INDUSTRIES[x],key="brief_industry")
    st.caption("Industry selection applies to employment and county industry pay. Resident workforce, credential awards and commuting use their full published county universes; occupation pay and outlook have separate selectors.")
    tabs=st.tabs(["Overview","Workforce","Employment & pay","Training","Movement","Regional outlook"])
    with tabs[0]:
        st.markdown("### Four signals for the conversation")
        specs=[("qcew","qcew_employment_yoy_pct","Employment growth",industry,"pilot counties"),
               ("acs_s2301","labor_force_participation_rate_16_plus","Resident participation",None,"Kansas counties"),
               ("ipeds","credentials_awarded","Credentials awarded",None,None),
               ("lodes","pct_workers_imported","Incoming commuter share",None,"pilot counties")]
        for col,(src,m,title,ind,peer) in zip(st.columns(4),specs):
            with col:metric_card(frame,series(frame,src,m,county,ind),title,"overview_"+m,peer_label=peer)
        st.caption("Each signal keeps its own period and unit. Comparison strips use only counties with published values in the same period and universe. These are observed measures, not a composite shortage index.")
        st.markdown("#### What the signals can help you discuss")
        st.markdown("**Employment:** Is the selected industry's employment expanding?  \n**Participation:** How many residents are engaged in the labor force?  \n**Training:** What credentials are institutions awarding?  \n**Movement:** How widely do employers already draw workers?")
        with st.expander("Coverage and source notes"):
            st.write("QCEW/QWI: workplace jobs and hiring. ACS/LAUS: residents. IPEDS: institution awards. LODES: primary-job commuting. Regional projections and OEWS retain their native geography. Missing data are never plotted as zero.")
            st.write("Demand and credential totals are a seven-county pilot. ACS covers all 105 Kansas counties. Some QWI histories, complete outbound commuting, apprenticeship and nonmetropolitan occupation pay remain unavailable.")
            st.markdown("Presentation reference: [America's Skilled Trades Dashboard](https://skilledtradesreport.com/KS/all). Charts here use this dashboard's verified public observations and definitions.")
    with tabs[1]:workforce(frame,county,"brief_workforce_",annual_laus)
    with tabs[2]:
        demand(frame,county,industry,names,"brief_demand_")
        pay(frame,county,"brief_pay_")
    with tabs[3]:training(frame,detail,county,"brief_training_",industry)
    with tabs[4]:movement(frame,flows,county,names,"brief_movement_")
    with tabs[5]:regional_view(regional,county,"brief_regional_")
    st.download_button("Download county observations with sources",frame[frame.geography_id.eq(county)].to_csv(index=False).encode(),f"{county}_workforce_observations.csv","text/csv",key="brief_source_download")
