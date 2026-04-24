"""
US State Workforce Forecast Dashboard
Streamlit + Plotly interactive dashboard.

Run locally:
    cd ks_workforce_forecast
    streamlit run dashboard/app.py

Deploy to Streamlit Community Cloud:
    1. Push this project to a GitHub repo.
    2. Go to share.streamlit.io → New app → point to dashboard/app.py.
    3. Add CENSUS_API_KEY to the app's Secrets settings.
"""

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Allow imports from project root regardless of working directory
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT_DIR = ROOT / "data" / "outputs"

# ── All US states + DC: name → 2-digit FIPS ─────────────────────────────────
STATE_FIPS: dict[str, str] = {
    "Alabama": "01", "Alaska": "02", "Arizona": "04", "Arkansas": "05",
    "California": "06", "Colorado": "08", "Connecticut": "09", "Delaware": "10",
    "District of Columbia": "11", "Florida": "12", "Georgia": "13", "Hawaii": "15",
    "Idaho": "16", "Illinois": "17", "Indiana": "18", "Iowa": "19",
    "Kansas": "20", "Kentucky": "21", "Louisiana": "22", "Maine": "23",
    "Maryland": "24", "Massachusetts": "25", "Michigan": "26", "Minnesota": "27",
    "Mississippi": "28", "Missouri": "29", "Montana": "30", "Nebraska": "31",
    "Nevada": "32", "New Hampshire": "33", "New Jersey": "34", "New Mexico": "35",
    "New York": "36", "North Carolina": "37", "North Dakota": "38", "Ohio": "39",
    "Oklahoma": "40", "Oregon": "41", "Pennsylvania": "42", "Rhode Island": "44",
    "South Carolina": "45", "South Dakota": "46", "Tennessee": "47", "Texas": "48",
    "Utah": "49", "Vermont": "50", "Virginia": "51", "Washington": "53",
    "West Virginia": "54", "Wisconsin": "55", "Wyoming": "56",
}
FIPS_STATE = {v: k for k, v in STATE_FIPS.items()}

# ── Page config (static — must be first Streamlit call) ─────────────────────
st.set_page_config(
    page_title="US Workforce Forecast",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Brand colors ─────────────────────────────────────────────────────────────
C_BLUE    = "#003F87"
C_GOLD    = "#F5A623"
C_GREEN   = "#2E8B57"
C_RED     = "#C0392B"
C_LIGHT   = "#F5F7FA"
C_NEUTRAL = "#7F8C8D"

# ── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
    .main-header {{
        background: linear-gradient(135deg, {C_BLUE} 0%, #005BB5 100%);
        color: white; padding: 1.5rem 2rem;
        border-radius: 8px; margin-bottom: 1.5rem;
    }}
    .main-header h1 {{ margin: 0; font-size: 1.8rem; }}
    .main-header p  {{ margin: 0.3rem 0 0; opacity: 0.85; font-size: 0.95rem; }}
    .metric-card {{
        background: white; border: 1px solid #E0E4EA;
        border-radius: 8px; padding: 1rem 1.2rem;
        text-align: center; box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    }}
    .metric-card .label {{ font-size: 0.78rem; color: {C_NEUTRAL}; font-weight: 600; text-transform: uppercase; }}
    .metric-card .value {{ font-size: 1.6rem; font-weight: 700; color: {C_BLUE}; margin: 0.2rem 0; }}
    .metric-card .delta {{ font-size: 0.9rem; font-weight: 600; }}
    .growing   {{ color: {C_GREEN}; }}
    .declining {{ color: {C_RED}; }}
    .stTabs [data-baseweb="tab-list"] {{ gap: 1rem; }}
    .stTabs [data-baseweb="tab"] {{ font-size: 0.95rem; font-weight: 600; }}
    .note-box {{
        background: #EAF2FF; border-left: 4px solid {C_BLUE};
        padding: 0.7rem 1rem; border-radius: 0 6px 6px 0;
        font-size: 0.88rem; color: #1a1a2e;
    }}
    .generate-box {{
        background: #FFF8E1; border: 2px dashed {C_GOLD};
        border-radius: 10px; padding: 2rem;
        text-align: center; margin: 2rem auto; max-width: 520px;
    }}
</style>
""", unsafe_allow_html=True)


# ── Census API key ────────────────────────────────────────────────────────────
def _census_api_key() -> str | None:
    try:
        return st.secrets["CENSUS_API_KEY"]
    except Exception:
        pass
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("CENSUS_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("CENSUS_API_KEY")


# ── Data loading ──────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Loading forecast data…")
def load_data(state_fips: str):
    proj_file    = OUTPUT_DIR / f"projections_s{state_fips}.parquet"
    summary_file = OUTPUT_DIR / f"county_summary_s{state_fips}.csv"
    state_file   = OUTPUT_DIR / f"state_projection_s{state_fips}.parquet"
    if any(not f.exists() for f in [proj_file, summary_file, state_file]):
        return None, None, None
    return (pd.read_parquet(proj_file),
            pd.read_csv(summary_file),
            pd.read_parquet(state_file))


def data_exists(state_fips: str) -> bool:
    return all((OUTPUT_DIR / f"{stem}_s{state_fips}.{ext}").exists()
               for stem, ext in [("projections", "parquet"),
                                  ("county_summary", "csv"),
                                  ("state_projection", "parquet")])


def run_forecast_for_state(state_fips: str):
    """Import and run the forecast pipeline for a new state."""
    from run_forecast import main as forecast_main
    forecast_main(
        state_fips=state_fips,
        api_key=_census_api_key(),
        n_sim=2000,
        start_year=2026,
        end_year=2035,
    )
    st.cache_data.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _fmt(n: float, decimals: int = 0) -> str:
    if pd.isna(n):
        return "—"
    return f"{n:,.{decimals}f}"


def _delta_html(pct: float) -> str:
    cls  = "growing" if pct >= 0 else "declining"
    sign = "+" if pct >= 0 else ""
    return f'<span class="{cls}">{sign}{pct:.1f}%</span>'


def metric_card(label: str, value: str, delta_html: str = "") -> str:
    return f"""<div class="metric-card">
        <div class="label">{label}</div>
        <div class="value">{value}</div>
        <div class="delta">{delta_html}</div>
    </div>"""


# ── Charts ────────────────────────────────────────────────────────────────────
def ci_chart(df: pd.DataFrame, title: str,
             baseline: float | None = None,
             base_year: int = 2023) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pd.concat([df["year"], df["year"].iloc[::-1]]),
        y=pd.concat([df["p95"], df["p5"].iloc[::-1]]),
        fill="toself", fillcolor="rgba(0,63,135,0.10)",
        line=dict(color="rgba(255,255,255,0)"), name="90% CI", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=pd.concat([df["year"], df["year"].iloc[::-1]]),
        y=pd.concat([df["p90"], df["p10"].iloc[::-1]]),
        fill="toself", fillcolor="rgba(0,63,135,0.17)",
        line=dict(color="rgba(255,255,255,0)"), name="80% CI", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=pd.concat([df["year"], df["year"].iloc[::-1]]),
        y=pd.concat([df["p75"], df["p25"].iloc[::-1]]),
        fill="toself", fillcolor="rgba(0,63,135,0.28)",
        line=dict(color="rgba(255,255,255,0)"), name="50% CI (IQR)", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=df["year"], y=df["p50"],
        mode="lines+markers", name="Median projection",
        line=dict(color=C_BLUE, width=2.5), marker=dict(size=5),
        hovertemplate="<b>%{x}</b><br>Median: %{y:,.0f}<extra></extra>",
    ))
    if baseline is not None:
        fig.add_trace(go.Scatter(
            x=[base_year], y=[baseline], mode="markers",
            name=f"{base_year} ACS baseline",
            marker=dict(color=C_GOLD, size=10, symbol="diamond"),
            hovertemplate=f"<b>{base_year} Baseline</b><br>%{{y:,.0f}}<extra></extra>",
        ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color=C_BLUE)),
        xaxis=dict(title="Year", tickmode="linear", dtick=1,
                   title_font=dict(color="black"), tickfont=dict(color="black")),
        yaxis=dict(title="Working-Age Population (18–64)", tickformat=",",
                   title_font=dict(color="black"), tickfont=dict(color="black")),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(color="black")),
        plot_bgcolor=C_LIGHT, paper_bgcolor="white",
        margin=dict(t=80, b=40, l=60, r=30), hovermode="x unified",
    )
    return fig


def state_choropleth(summary: pd.DataFrame, state_fips: str,
                     end_year: int, metric: str = "pct_change_end") -> go.Figure:
    df = summary.copy()
    df["fips5"] = state_fips.zfill(2) + df["county_fips"].astype(str).str.zfill(3)
    df["label"] = df["county_name"] + "<br>" + df[metric].map(lambda x: f"{x:+.1f}%")
    z_max = max(abs(df[metric].min()), abs(df[metric].max()), 1)
    state_name = FIPS_STATE.get(state_fips, state_fips)

    fig = go.Figure(go.Choropleth(
        geojson="https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json",
        locations=df["fips5"], z=df[metric], text=df["label"],
        hoverinfo="text",
        colorscale=[
            [0.0, "#C0392B"], [0.35, "#E8A09A"], [0.5, "#F5F5F5"],
            [0.65, "#9EC8B9"], [1.0, "#2E8B57"],
        ],
        zmin=-z_max, zmax=z_max,
        colorbar=dict(title=dict(text="% Change", side="right"),
                      tickformat="+.0f", thickness=15),
        marker_line_color="white", marker_line_width=0.5,
    ))
    fig.update_geos(scope="usa", fitbounds="locations", visible=False)
    fig.update_layout(
        title=dict(
            text=f"{state_name} — County Working-Age Population Change: 2023 → {end_year} (Median %)",
            font=dict(size=15, color=C_BLUE), x=0.5, xanchor="center",
        ),
        height=420, margin=dict(t=60, b=10, l=0, r=0), paper_bgcolor="white",
    )
    return fig


# ── Main app ──────────────────────────────────────────────────────────────────
def main():

    # ── Sidebar — state selector at the very top ──────────────────────────
    with st.sidebar:
        st.image("https://www.wsutech.edu/images/logo-wsutech.png",
                 use_container_width=True)
        st.markdown("---")
        st.markdown("### State")
        state_names = sorted(STATE_FIPS.keys())
        default_idx = state_names.index("Kansas")
        selected_state = st.selectbox("Select state", state_names, index=default_idx)
        state_fips     = STATE_FIPS[selected_state]

        # Show which states already have data pre-computed
        available = [FIPS_STATE[f.stem.split("_s")[1]]
                     for f in OUTPUT_DIR.glob("projections_s*.parquet")
                     if f.stem.split("_s")[1] in FIPS_STATE]
        if available:
            st.caption(f"Data ready: {', '.join(sorted(available))}")

        st.markdown("---")

        # County selector — only shown after data loads
        county_selector_placeholder = st.empty()

        st.markdown("### Chart options")
        show_90ci = st.checkbox("Show 90% CI band", value=True)
        show_50ci = st.checkbox("Show 50% CI (IQR) band", value=True)
        st.markdown("---")
        st.markdown("### Map filter")
        min_pop = st.slider("Min county pop (2023)", 0, 50000, 0, step=1000)
        st.markdown("---")
        st.markdown(
            "**Data:** U.S. Census Bureau ACS 5-Year  \n"
            "**Model:** Annual cohort-component  \n"
            "**CI:** Monte Carlo AR(1) migration"
        )
        st.markdown(
            '<div class="note-box">Forecast is for planning purposes. '
            'Actual outcomes depend on economic conditions, policy, '
            'and factors outside this model.</div>',
            unsafe_allow_html=True,
        )

    # ── Check for data; offer to generate if missing ──────────────────────
    if not data_exists(state_fips):
        st.markdown(f"""
        <div class="main-header">
            <h1>US Workforce Forecast — {selected_state}</h1>
            <p>Cohort-component model &nbsp;·&nbsp; ACS 5-Year Estimates &nbsp;·&nbsp;
               2,000 Monte Carlo simulations per county</p>
        </div>""", unsafe_allow_html=True)

        st.markdown(
            f'<div class="generate-box">'
            f'<h3 style="margin-top:0">No forecast data yet for {selected_state}</h3>'
            f'<p>Click below to fetch ACS data and run the cohort-component model.<br>'
            f'This takes <strong>3–8 minutes</strong> depending on county count.</p>'
            f'</div>',
            unsafe_allow_html=True,
        )
        col_btn = st.columns([1, 2, 1])[1]
        if col_btn.button(f"Generate Forecast for {selected_state}",
                          type="primary", use_container_width=True):
            with st.spinner(f"Fetching ACS data and running model for {selected_state}…"):
                try:
                    run_forecast_for_state(state_fips)
                    st.success(f"Forecast complete for {selected_state}!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Forecast failed: {e}")
        return

    # ── Load data ─────────────────────────────────────────────────────────
    proj, summary, state_proj = load_data(state_fips)
    if proj is None:
        st.error("Data files exist but could not be loaded. Try deleting them and regenerating.")
        st.stop()

    start_year   = int(proj["year"].min())
    end_year     = int(proj["year"].max())
    counties     = sorted(summary["county_name"].unique())
    n_counties   = len(counties)

    # Default county = largest workforce
    default_county = summary.loc[summary["workforce_base"].idxmax(), "county_name"]
    default_idx_c  = counties.index(default_county) if default_county in counties else 0

    with county_selector_placeholder:
        selected_county = st.selectbox("County Explorer", counties, index=default_idx_c)

    # ── Header ────────────────────────────────────────────────────────────
    st.markdown(f"""
    <div class="main-header">
        <h1>{selected_state} Workforce Forecast &nbsp; {start_year}–{end_year}</h1>
        <p>Cohort-component model &nbsp;·&nbsp; ACS 5-Year Estimates (2015–2023) &nbsp;·&nbsp;
           2,000 Monte Carlo simulations per county &nbsp;·&nbsp;
           Working-age population 18–64 &nbsp;·&nbsp; {n_counties} counties</p>
    </div>""", unsafe_allow_html=True)

    # ── Tabs ──────────────────────────────────────────────────────────────
    tab_overview, tab_county, tab_table, tab_method = st.tabs(
        ["State Overview", "County Explorer", "Data Table", "Methodology"]
    )

    # ═════════════════════════════════════════════════════════════════════
    # TAB 1 — STATE OVERVIEW
    # ═════════════════════════════════════════════════════════════════════
    with tab_overview:
        total_base = summary["workforce_base"].sum()
        total_end  = summary["wf_end_p50"].sum()
        net_chg    = total_end - total_base
        pct_chg    = net_chg / total_base * 100
        growing    = (summary["pct_change_end"] > 0).sum()
        declining  = (summary["pct_change_end"] <= 0).sum()
        total_ret  = summary["annual_retirements_end"].sum()

        cols = st.columns(5)
        kpis = [
            ("2023 Baseline WF",              _fmt(total_base), ""),
            (f"Projected {end_year} (Median)", _fmt(total_end),  _delta_html(pct_chg)),
            ("Net Change",                    _fmt(net_chg),     _delta_html(pct_chg)),
            ("Counties Growing",              str(growing),      f"<span>{declining} declining</span>"),
            (f"Annual Retirements ({end_year})", _fmt(total_ret), "state total"),
        ]
        for col, (lbl, val, dlt) in zip(cols, kpis):
            col.markdown(metric_card(lbl, val, dlt), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        st.plotly_chart(
            ci_chart(state_proj,
                     f"{selected_state} Working-Age Population (18–64), {start_year}–{end_year}",
                     baseline=total_base),
            use_container_width=True,
        )

        map_data = summary[summary["pop_total_base"] >= min_pop].copy()
        st.plotly_chart(
            state_choropleth(map_data, state_fips, end_year),
            use_container_width=True,
        )

        col_left, col_right = st.columns(2)
        disp_cols = ["county_name", "workforce_base", "wf_end_p50", "pct_change_end"]
        rename    = {"county_name": "County", "workforce_base": "Baseline 2023",
                     "wf_end_p50": f"Projected {end_year}", "pct_change_end": "% Change"}
        with col_left:
            st.markdown(f"**Top 10 Growing Counties ({end_year} median)**")
            top = summary.nlargest(10, "pct_change_end")[disp_cols].rename(columns=rename)
            top["% Change"]          = top["% Change"].map(lambda x: f"{x:+.1f}%")
            top["Baseline 2023"]     = top["Baseline 2023"].map(_fmt)
            top[f"Projected {end_year}"] = top[f"Projected {end_year}"].map(_fmt)
            st.dataframe(top, hide_index=True, use_container_width=True)
        with col_right:
            st.markdown(f"**Top 10 Declining Counties ({end_year} median)**")
            bot = summary.nsmallest(10, "pct_change_end")[disp_cols].rename(columns=rename)
            bot["% Change"]          = bot["% Change"].map(lambda x: f"{x:+.1f}%")
            bot["Baseline 2023"]     = bot["Baseline 2023"].map(_fmt)
            bot[f"Projected {end_year}"] = bot[f"Projected {end_year}"].map(_fmt)
            st.dataframe(bot, hide_index=True, use_container_width=True)

    # ═════════════════════════════════════════════════════════════════════
    # TAB 2 — COUNTY EXPLORER
    # ═════════════════════════════════════════════════════════════════════
    with tab_county:
        county_proj = proj[proj["county_name"] == selected_county].sort_values("year")
        county_sum  = summary[summary["county_name"] == selected_county].iloc[0]

        wf_base   = county_sum["workforce_base"]
        wf_end    = county_sum["wf_end_p50"]
        wf_end_lo = county_sum["wf_end_p10"]
        wf_end_hi = county_sum["wf_end_p90"]
        pct_end   = county_sum["pct_change_end"]
        mig_rate  = county_sum["mig_mean_pct"]
        ann_ret   = county_sum["annual_retirements_end"]
        ann_ent   = county_sum["annual_entries_end"]

        ccols = st.columns(5)
        ckpis = [
            ("2023 Working-Age",               _fmt(wf_base), ""),
            (f"Projected {end_year} (Median)", _fmt(wf_end),  _delta_html(pct_end)),
            (f"80% CI ({end_year})",           f"{_fmt(wf_end_lo)} – {_fmt(wf_end_hi)}", ""),
            ("Est. Annual Migration Rate",     f"{mig_rate:+.2f}%", "historical avg"),
            (f"Annual Retirements ({end_year})", _fmt(ann_ret), f"entries: {_fmt(ann_ent)}"),
        ]
        for col, (lbl, val, dlt) in zip(ccols, ckpis):
            col.markdown(metric_card(lbl, val, dlt), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        fig_c = go.Figure()
        if show_90ci:
            fig_c.add_trace(go.Scatter(
                x=pd.concat([county_proj["year"], county_proj["year"].iloc[::-1]]),
                y=pd.concat([county_proj["p95"], county_proj["p5"].iloc[::-1]]),
                fill="toself", fillcolor="rgba(0,63,135,0.10)",
                line=dict(color="rgba(0,0,0,0)"), name="90% CI", hoverinfo="skip",
            ))
        fig_c.add_trace(go.Scatter(
            x=pd.concat([county_proj["year"], county_proj["year"].iloc[::-1]]),
            y=pd.concat([county_proj["p90"], county_proj["p10"].iloc[::-1]]),
            fill="toself", fillcolor="rgba(0,63,135,0.18)",
            line=dict(color="rgba(0,0,0,0)"), name="80% CI", hoverinfo="skip",
        ))
        if show_50ci:
            fig_c.add_trace(go.Scatter(
                x=pd.concat([county_proj["year"], county_proj["year"].iloc[::-1]]),
                y=pd.concat([county_proj["p75"], county_proj["p25"].iloc[::-1]]),
                fill="toself", fillcolor="rgba(0,63,135,0.28)",
                line=dict(color="rgba(0,0,0,0)"), name="50% CI (IQR)", hoverinfo="skip",
            ))
        fig_c.add_trace(go.Scatter(
            x=county_proj["year"], y=county_proj["p50"],
            mode="lines+markers", name="Median",
            line=dict(color=C_BLUE, width=2.5), marker=dict(size=6),
            hovertemplate="<b>%{x}</b><br>Median: %{y:,.0f}<extra></extra>",
        ))
        fig_c.add_trace(go.Scatter(
            x=[2023], y=[wf_base], mode="markers", name="2023 Baseline",
            marker=dict(color=C_GOLD, size=12, symbol="diamond"),
        ))
        fig_c.update_layout(
            title=dict(text=f"{selected_county}, {selected_state} — Working-Age Population Forecast",
                       font=dict(size=15, color=C_BLUE)),
            xaxis=dict(title="Year", tickmode="linear", dtick=1),
            yaxis=dict(title="Working-Age Population (18–64)", tickformat=","),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                        font=dict(color="black")),
            plot_bgcolor=C_LIGHT, paper_bgcolor="white",
            margin=dict(t=80, b=40, l=60, r=30), hovermode="x unified",
        )
        st.plotly_chart(fig_c, use_container_width=True)

        st.markdown("#### Annual Projections")
        tbl = county_proj[["year", "p10", "p25", "p50", "p75", "p90",
                            "retirements_p50", "entries_p50", "pct_change_p50"]].copy()
        tbl.columns = ["Year", "P10 (80% lo)", "P25 (50% lo)", "Median",
                       "P75 (50% hi)", "P90 (80% hi)",
                       "Annual Retirements", "Annual Entries", "% vs 2023"]
        for c in ["P10 (80% lo)", "P25 (50% lo)", "Median",
                  "P75 (50% hi)", "P90 (80% hi)", "Annual Retirements", "Annual Entries"]:
            tbl[c] = tbl[c].map(_fmt)
        tbl["% vs 2023"] = tbl["% vs 2023"].map(lambda x: f"{x:+.1f}%")
        st.dataframe(tbl, hide_index=True, use_container_width=True)

    # ═════════════════════════════════════════════════════════════════════
    # TAB 3 — DATA TABLE
    # ═════════════════════════════════════════════════════════════════════
    with tab_table:
        st.markdown(f"#### County Summary — All {n_counties} {selected_state} Counties")

        fc1, fc2 = st.columns(2)
        with fc1:
            trend_filter = st.multiselect(
                "Trend filter",
                ["Growing (>0%)", "Declining (<0%)", "Stable (±2%)"],
                default=["Growing (>0%)", "Declining (<0%)", "Stable (±2%)"],
            )
        with fc2:
            sort_by = st.selectbox(
                "Sort by",
                ["% Change (worst first)", "% Change (best first)",
                 "County Name", "Baseline Workforce (largest first)"],
            )

        disp = summary[[
            "county_name", "workforce_base", "wf_end_p50",
            "wf_end_p10", "wf_end_p90",
            "pct_change_end", "annual_retirements_end",
            "annual_entries_end", "mig_mean_pct",
        ]].copy()
        disp.columns = [
            "County", "Baseline 2023", f"Median {end_year}",
            f"P10 {end_year}", f"P90 {end_year}",
            "% Change", "Annual Retirements", "Annual Entries", "Net Mig Rate (%)",
        ]

        mask = pd.Series([False] * len(disp), index=disp.index)
        if "Growing (>0%)" in trend_filter:
            mask |= disp["% Change"] > 0
        if "Declining (<0%)" in trend_filter:
            mask |= disp["% Change"] < 0
        if "Stable (±2%)" in trend_filter:
            mask |= disp["% Change"].abs() <= 2
        disp = disp[mask]

        sort_map = {
            "% Change (worst first)":             ("% Change", True),
            "% Change (best first)":              ("% Change", False),
            "County Name":                        ("County", False),
            "Baseline Workforce (largest first)": ("Baseline 2023", True),
        }
        sort_col, sort_asc = sort_map.get(sort_by, ("% Change", True))
        disp = disp.sort_values(sort_col, ascending=sort_asc)

        for c in ["Baseline 2023", f"Median {end_year}",
                  f"P10 {end_year}", f"P90 {end_year}",
                  "Annual Retirements", "Annual Entries"]:
            disp[c] = disp[c].map(_fmt)
        disp["% Change"]        = disp["% Change"].map(lambda x: f"{x:+.1f}%")
        disp["Net Mig Rate (%)"] = disp["Net Mig Rate (%)"].map(lambda x: f"{x:+.2f}%")

        st.dataframe(disp, hide_index=True, use_container_width=True, height=500)

        csv = summary.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download Full Dataset (CSV)",
            data=csv,
            file_name=f"{selected_state.lower().replace(' ', '_')}_workforce_forecast_{start_year}_{end_year}.csv",
            mime="text/csv",
        )

    # ═════════════════════════════════════════════════════════════════════
    # TAB 4 — METHODOLOGY
    # ═════════════════════════════════════════════════════════════════════
    with tab_method:
        st.markdown(f"""
## Methodology

### Model Type
Annual **cohort-component** model tracking the working-age population (18–64) in each
county of **{selected_state}** from a 2023 ACS baseline through {end_year}.

### Data Sources
| Source | Description |
|--------|-------------|
| U.S. Census Bureau ACS 5-Year Estimates | Age-by-sex population (Table B01001) for 2015, 2019, 2021, 2023 |
| CDC 2021 National Life Tables | Age-specific annual survival probabilities |

### Components Modeled Each Year
1. **Survival** — Age-specific mortality applied to each 5-year cohort (CDC 2021 life tables)
2. **Aging** — Each year, 1/cohort-width of each age group advances to the next cohort
3. **Workforce entry** — The 15–17 cohort ages into the 18–24 workforce cohort (1/3 per year)
4. **Retirement exits** — The 60–64 cohort ages into 65+ (1/5 per year)
5. **Net migration** — Annual net migration rate applied proportionally across all working-age cohorts

### Migration Estimation
County net migration rates are estimated using the **cohort-survival residual method**:
- Historical working-age population change is observed from ACS 5-year snapshots (2015 → 2019 → 2021 → 2023)
- The expected change from mortality alone is subtracted, leaving the migration residual
- The mean and standard deviation form the county's migration distribution

### Confidence Intervals
2,000 Monte Carlo simulations per county. Each draws an annual migration-rate sequence
from an **AR(1) process** (φ = 0.3) reflecting migration persistence year-over-year.

| Band | Percentiles | Interpretation |
|------|-------------|----------------|
| 50% CI (IQR) | P25–P75 | Core range — outcomes in this band half the time |
| 80% CI | P10–P90 | Most likely range |
| 90% CI | P5–P95 | Near-full uncertainty envelope |

### Limitations
- National survival rates used; state-specific mortality may differ
- Small counties (pop < 2,000) will have very wide confidence intervals
- Birth-rate pipeline — children born after 2023 won't enter workforce until 2041+
- Model tracks **population**, not employed workers (labor force participation not modeled)
- Economic shocks (plant closures, employer relocations) are not captured

### Adding Another State
Select any state from the sidebar — if no forecast exists yet, click **Generate Forecast**
to fetch and model it automatically (requires Census API key for best results).
        """)


if __name__ == "__main__":
    main()
