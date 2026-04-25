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
QCEW_CACHE = ROOT / "data" / "qcew_cache"

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


@st.cache_data(show_spinner="Loading sector data…")
def load_sector_data(state_fips: str):
    county_file = OUTPUT_DIR / f"sector_projections_s{state_fips}.parquet"
    state_file  = OUTPUT_DIR / f"state_sector_projection_s{state_fips}.parquet"
    if not county_file.exists() or not state_file.exists():
        return None, None
    return pd.read_parquet(county_file), pd.read_parquet(state_file)


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
        run_sectors=False,   # cohort only; sectors run separately
    )
    st.cache_data.clear()


def run_sector_forecast_for_state(state_fips: str):
    """Fetch QCEW and run sector model for a state that already has cohort data."""
    from fetch_qcew   import fetch_state_qcew
    from sector_model import run_all_sectors

    proj_file    = OUTPUT_DIR / f"projections_s{state_fips}.parquet"
    summary_file = OUTPUT_DIR / f"county_summary_s{state_fips}.csv"
    proj_df      = pd.read_parquet(proj_file)
    summary      = pd.read_csv(summary_file)

    county_fips3 = summary["county_fips"].astype(str).str.zfill(3).tolist()
    county_qcew, state_qcew, state_totals = fetch_state_qcew(
        state_fips=state_fips,
        county_fips3_list=county_fips3,
        cache_dir=QCEW_CACHE,
    )
    county_sector_df, state_sector_df = run_all_sectors(
        county_qcew  = county_qcew,
        state_qcew   = state_qcew,
        state_totals = state_totals,
        cohort_proj  = proj_df,
        state_fips   = state_fips,
    )
    county_sector_df.to_parquet(
        OUTPUT_DIR / f"sector_projections_s{state_fips}.parquet", index=False)
    state_sector_df.to_parquet(
        OUTPUT_DIR / f"state_sector_projection_s{state_fips}.parquet", index=False)
    st.cache_data.clear()


def sector_data_exists(state_fips: str) -> bool:
    return all((OUTPUT_DIR / f"{stem}_s{state_fips}.parquet").exists()
               for stem in ["sector_projections", "state_sector_projection"])


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
        colorbar=dict(title=dict(text="% Change", side="right", font=dict(color="black")),
                      tickformat="+.0f", thickness=15,
                      tickfont=dict(color="black")),
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
    tab_overview, tab_county, tab_sector, tab_table, tab_method = st.tabs(
        ["State Overview", "County Explorer", "Industry Forecast", "Data Table", "Methodology"]
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
            xaxis=dict(title="Year", tickmode="linear", dtick=1,
                       title_font=dict(color="black"), tickfont=dict(color="black")),
            yaxis=dict(title="Working-Age Population (18–64)", tickformat=",",
                       title_font=dict(color="black"), tickfont=dict(color="black")),
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
    # TAB 3 — INDUSTRY FORECAST
    # ═════════════════════════════════════════════════════════════════════
    with tab_sector:
        from fetch_qcew import SECTOR_COLORS, SECTORS

        if not sector_data_exists(state_fips):
            st.markdown(
                f'<div class="generate-box">'
                f'<h3 style="margin-top:0">Industry Forecast not yet generated</h3>'
                f'<p>Click below to fetch BLS QCEW employment data and run the sector model.<br>'
                f'This takes <strong>10–20 minutes</strong> on first run '
                f'(county QCEW files are cached after that).</p>'
                f'</div>',
                unsafe_allow_html=True,
            )
            col_btn2 = st.columns([1, 2, 1])[1]
            if col_btn2.button("Generate Industry Forecast",
                               type="primary", use_container_width=True):
                with st.spinner("Fetching QCEW data and running sector model…"):
                    try:
                        run_sector_forecast_for_state(state_fips)
                        st.success("Industry forecast complete!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Sector forecast failed: {e}")
        else:
            county_sector_df, state_sector_df = load_sector_data(state_fips)
            if county_sector_df is None:
                st.error("Sector data files exist but could not be loaded.")
                st.stop()

            sec_start = int(county_sector_df["year"].min())
            sec_end   = int(county_sector_df["year"].max())

            # ── Pre-compute sector stats ──────────────────────────────────
            sector_stats: dict = {}
            total_jobs_2023 = 0
            total_jobs_end  = 0
            for sector in SECTORS:
                s_rows   = state_sector_df[state_sector_df["sector"] == sector]
                base     = float(s_rows["emp_2023"].iloc[0]) if len(s_rows) and not pd.isna(s_rows["emp_2023"].iloc[0]) else None
                end_rows = s_rows[s_rows["year"] == sec_end]
                proj     = float(end_rows["emp_proj"].iloc[0])   if len(end_rows) else None
                ci_lo    = float(end_rows["emp_ci_lo"].iloc[0])  if len(end_rows) else None
                ci_hi    = float(end_rows["emp_ci_hi"].iloc[0])  if len(end_rows) else None
                delta    = (proj - base) if (base and proj) else None
                pct      = (delta / base * 100) if (base and delta is not None) else None
                sector_stats[sector] = dict(base=base, proj=proj, ci_lo=ci_lo, ci_hi=ci_hi,
                                            delta=delta, pct=pct)
                if base:
                    total_jobs_2023 += base
                if proj:
                    total_jobs_end  += proj

            total_delta = total_jobs_end - total_jobs_2023
            wf_supply_2023 = float(summary["workforce_base"].sum())
            wf_supply_end  = float(summary["wf_end_p50"].sum())
            wf_supply_pct  = (wf_supply_end - wf_supply_2023) / wf_supply_2023 * 100

            # ── Section header ────────────────────────────────────────────
            st.markdown(f"""
<div style="background:linear-gradient(135deg,{C_BLUE} 0%,#005BB5 100%);
            color:white;padding:1rem 1.5rem;border-radius:8px;margin-bottom:1rem;">
  <strong style="font-size:1.05rem;">
    {selected_state} — Sector Workforce: Need vs. Supply &nbsp;·&nbsp; 2023 → {sec_end}
  </strong><br>
  <span style="opacity:0.85;font-size:0.88rem;">
    Demand = projected jobs by sector (BLS QCEW) &nbsp;·&nbsp;
    Supply = working-age population 18–64 (ACS cohort model)
  </span>
</div>""", unsafe_allow_html=True)

            # ── State-level summary KPI row ───────────────────────────────
            kpi_cols = st.columns(4)
            supply_arrow = "growing" if wf_supply_pct >= 0 else "declining"
            demand_arrow = "growing" if total_delta >= 0 else "declining"
            kpi_cols[0].markdown(metric_card(
                "Total Workers Available (2023)",
                _fmt(wf_supply_2023),
                f'<span class="{supply_arrow}">{wf_supply_pct:+.1f}% by {sec_end}</span>',
            ), unsafe_allow_html=True)
            kpi_cols[1].markdown(metric_card(
                f"Projected Workers Available ({sec_end})",
                _fmt(wf_supply_end),
                f'<span class="{supply_arrow}">{_fmt(wf_supply_end - wf_supply_2023)} net change</span>',
            ), unsafe_allow_html=True)
            kpi_cols[2].markdown(metric_card(
                "Total Sector Jobs (2023)",
                _fmt(total_jobs_2023),
                "",
            ), unsafe_allow_html=True)
            kpi_cols[3].markdown(metric_card(
                f"Total Sector Jobs Projected ({sec_end})",
                _fmt(total_jobs_end),
                f'<span class="{demand_arrow}">{total_delta:+,.0f} net new jobs</span>',
            ), unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # ── Grouped gap bar chart: Jobs Today vs Jobs Needed ─────────
            st.markdown("#### Jobs Today vs. Jobs Needed by Sector")
            st.caption(
                "Each sector shows 2023 actual employment alongside the projected "
                f"{sec_end} need. The label on each projected bar shows the net change "
                "in workers required."
            )

            gap_sectors  = []
            gap_base     = []
            gap_proj     = []
            gap_delta    = []
            gap_ci_lo    = []
            gap_ci_hi    = []
            gap_colors   = []

            for sector in SECTORS:
                st_s = sector_stats[sector]
                if st_s["base"] is None or st_s["proj"] is None:
                    continue
                gap_sectors.append(sector)
                gap_base.append(st_s["base"])
                gap_proj.append(st_s["proj"])
                gap_delta.append(st_s["delta"] or 0)
                gap_ci_lo.append(st_s["ci_lo"] or st_s["proj"])
                gap_ci_hi.append(st_s["ci_hi"] or st_s["proj"])
                gap_colors.append(SECTOR_COLORS[sector])

            fig_gap = go.Figure()

            # 2023 baseline bars (solid)
            fig_gap.add_trace(go.Bar(
                name="2023 Actual Jobs",
                x=gap_sectors,
                y=gap_base,
                marker_color=[c + "CC" for c in gap_colors],
                marker_line_color=[c for c in gap_colors],
                marker_line_width=1.5,
                text=[_fmt(v) for v in gap_base],
                textposition="outside",
                hovertemplate="<b>%{x}</b><br>2023 Jobs: %{y:,.0f}<extra></extra>",
            ))

            # Projected bars (hatched via opacity + pattern)
            fig_gap.add_trace(go.Bar(
                name=f"Projected Jobs ({sec_end})",
                x=gap_sectors,
                y=gap_proj,
                marker_color=["rgba(255,255,255,0)" for _ in gap_colors],
                marker_line_color=gap_colors,
                marker_line_width=2.5,
                marker_pattern_shape="/",
                marker_pattern_fgcolor=gap_colors,
                marker_pattern_bgcolor=["rgba(255,255,255,0.6)" for _ in gap_colors],
                text=[
                    f"{_fmt(v)}<br><b>{d:+,.0f}</b>"
                    for v, d in zip(gap_proj, gap_delta)
                ],
                textposition="outside",
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    f"Projected ({sec_end}): %{{y:,.0f}}<br>"
                    "Net change: %{text}<extra></extra>"
                ),
                error_y=dict(
                    type="data",
                    symmetric=False,
                    array=[hi - p for hi, p in zip(gap_ci_hi, gap_proj)],
                    arrayminus=[p - lo for lo, p in zip(gap_ci_lo, gap_proj)],
                    color=C_NEUTRAL,
                    thickness=1.5,
                    width=6,
                ),
            ))

            # Horizontal line: working-age population as supply context
            fig_gap.add_hline(
                y=wf_supply_end,
                line_dash="dot",
                line_color=C_GOLD,
                line_width=2,
                annotation_text=f"Total workforce supply {sec_end}: {_fmt(wf_supply_end)}",
                annotation_position="top left",
                annotation_font_color=C_GOLD,
                annotation_font_size=11,
            )

            fig_gap.update_layout(
                barmode="group",
                bargap=0.25,
                bargroupgap=0.08,
                title=dict(
                    text=(f"{selected_state} — Sector Employment: 2023 Actual vs. "
                          f"{sec_end} Projected Need"),
                    font=dict(size=15, color=C_BLUE),
                ),
                xaxis=dict(title="Sector", title_font=dict(color="black"),
                           tickfont=dict(color="black")),
                yaxis=dict(title="Workers", tickformat=",",
                           title_font=dict(color="black"), tickfont=dict(color="black")),
                legend=dict(orientation="h", yanchor="bottom", y=1.02,
                            xanchor="right", x=1, font=dict(color="black")),
                plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                margin=dict(t=80, b=60, l=70, r=30),
                uniformtext_minsize=9, uniformtext_mode="hide",
            )
            st.plotly_chart(fig_gap, use_container_width=True)

            # ── Supply vs. Total Demand overlay (line chart) ──────────────
            st.markdown("#### Workforce Supply vs. Total Sector Demand Over Time")
            st.caption(
                "Supply (left axis) = state working-age population 18–64. "
                "Demand (right axis) = sum of projected employment across all five sectors. "
                "Diverging trends signal growing competition for workers."
            )

            demand_by_year = (
                state_sector_df.groupby("year")["emp_proj"].sum().reset_index()
            )
            demand_lo_by_year = (
                state_sector_df.groupby("year")["emp_ci_lo"].sum().reset_index()
            )
            demand_hi_by_year = (
                state_sector_df.groupby("year")["emp_ci_hi"].sum().reset_index()
            )

            fig_svd = go.Figure()

            # Supply: CI band + median line
            fig_svd.add_trace(go.Scatter(
                x=pd.concat([state_proj["year"], state_proj["year"].iloc[::-1]]),
                y=pd.concat([state_proj["p90"], state_proj["p10"].iloc[::-1]]),
                fill="toself", fillcolor="rgba(0,63,135,0.10)",
                line=dict(color="rgba(0,0,0,0)"),
                name="Supply 80% CI", hoverinfo="skip", yaxis="y1",
            ))
            fig_svd.add_trace(go.Scatter(
                x=state_proj["year"], y=state_proj["p50"],
                mode="lines+markers", name="Workforce Supply (18–64)",
                line=dict(color=C_BLUE, width=2.5),
                marker=dict(size=5),
                yaxis="y1",
                hovertemplate="<b>%{x}</b><br>Supply: %{y:,.0f}<extra></extra>",
            ))
            # Supply 2023 anchor
            fig_svd.add_trace(go.Scatter(
                x=[2023], y=[wf_supply_2023],
                mode="markers", name="2023 Supply (ACS)",
                marker=dict(color=C_GOLD, size=10, symbol="diamond"),
                yaxis="y1",
                hovertemplate=f"<b>2023 Baseline</b><br>%{{y:,.0f}}<extra></extra>",
            ))

            # Demand: CI band + line
            fig_svd.add_trace(go.Scatter(
                x=pd.concat([demand_hi_by_year["year"],
                             demand_lo_by_year["year"].iloc[::-1]]),
                y=pd.concat([demand_hi_by_year["emp_ci_hi"],
                             demand_lo_by_year["emp_ci_lo"].iloc[::-1]]),
                fill="toself", fillcolor="rgba(245,166,35,0.12)",
                line=dict(color="rgba(0,0,0,0)"),
                name="Demand 80% CI", hoverinfo="skip", yaxis="y2",
            ))
            fig_svd.add_trace(go.Scatter(
                x=demand_by_year["year"], y=demand_by_year["emp_proj"],
                mode="lines+markers", name="Total Sector Demand",
                line=dict(color=C_GOLD, width=2.5, dash="dash"),
                marker=dict(size=5),
                yaxis="y2",
                hovertemplate="<b>%{x}</b><br>Demand: %{y:,.0f}<extra></extra>",
            ))
            # Demand 2023 anchor
            fig_svd.add_trace(go.Scatter(
                x=[2023], y=[total_jobs_2023],
                mode="markers", name="2023 Demand (QCEW)",
                marker=dict(color=C_GOLD, size=10, symbol="circle",
                            line=dict(color=C_BLUE, width=2)),
                yaxis="y2",
                hovertemplate=f"<b>2023 Demand Baseline</b><br>%{{y:,.0f}}<extra></extra>",
            ))

            fig_svd.update_layout(
                title=dict(
                    text=(f"{selected_state} — Workforce Supply vs. Sector Demand "
                          f"{sec_start}–{sec_end}"),
                    font=dict(size=15, color=C_BLUE),
                ),
                xaxis=dict(title="Year", tickmode="linear", dtick=1,
                           title_font=dict(color="black"), tickfont=dict(color="black")),
                yaxis=dict(
                    title="Working-Age Population (Supply)",
                    tickformat=",", side="left",
                    title_font=dict(color=C_BLUE), tickfont=dict(color=C_BLUE),
                ),
                yaxis2=dict(
                    title="Total Sector Employment (Demand)",
                    tickformat=",", side="right", overlaying="y",
                    title_font=dict(color="#B8860B"), tickfont=dict(color="#B8860B"),
                    showgrid=False,
                ),
                legend=dict(orientation="h", yanchor="bottom", y=1.02,
                            xanchor="right", x=1, font=dict(color="black")),
                plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                margin=dict(t=80, b=40, l=70, r=80), hovermode="x unified",
            )
            st.plotly_chart(fig_svd, use_container_width=True)

            # ── Per-sector KPI cards (detailed numbers) ───────────────────
            st.markdown("#### Sector Detail — State Level")
            kpi_sec_cols = st.columns(len(SECTORS))
            for col, sector in zip(kpi_sec_cols, SECTORS):
                st_s = sector_stats[sector]
                if st_s["base"] and st_s["proj"] and st_s["delta"] is not None:
                    sign     = "+" if st_s["delta"] >= 0 else ""
                    cls_name = "growing" if st_s["delta"] >= 0 else "declining"
                    delta_lbl = (
                        f'<span class="{cls_name}">{sign}{_fmt(st_s["delta"])} jobs</span><br>'
                        f'<span style="font-size:0.8rem;color:{C_NEUTRAL};">'
                        f'2023: {_fmt(st_s["base"])}</span>'
                    )
                else:
                    delta_lbl = ""
                col.markdown(
                    metric_card(
                        f"{sector}",
                        _fmt(st_s["proj"]) if st_s["proj"] else "—",
                        delta_lbl,
                    ),
                    unsafe_allow_html=True,
                )

            st.markdown("<br>", unsafe_allow_html=True)

            # ── Sector trend chart (all sectors over time) ────────────────
            with st.expander("Sector employment trends over time (line chart)", expanded=False):
                fig_state_sec = go.Figure()
                for sector in SECTORS:
                    color  = SECTOR_COLORS[sector]
                    s_rows = state_sector_df[state_sector_df["sector"] == sector].sort_values("year")
                    if s_rows.empty:
                        continue
                    rgb = f"{int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)}"
                    fig_state_sec.add_trace(go.Scatter(
                        x=pd.concat([s_rows["year"], s_rows["year"].iloc[::-1]]),
                        y=pd.concat([s_rows["emp_ci_hi"], s_rows["emp_ci_lo"].iloc[::-1]]),
                        fill="toself", fillcolor=f"rgba({rgb},0.12)",
                        line=dict(color="rgba(255,255,255,0)"),
                        name=f"{sector} 80% CI", hoverinfo="skip", showlegend=False,
                    ))
                    fig_state_sec.add_trace(go.Scatter(
                        x=s_rows["year"], y=s_rows["emp_proj"],
                        mode="lines+markers", name=sector,
                        line=dict(color=color, width=2.5, dash="dash"),
                        marker=dict(size=5),
                        hovertemplate=f"<b>{sector}</b> %{{x}}<br>Projected: %{{y:,.0f}}<extra></extra>",
                    ))
                fig_state_sec.update_layout(
                    title=dict(
                        text=f"{selected_state} — Sector Employment Projections {sec_start}–{sec_end}",
                        font=dict(size=15, color=C_BLUE),
                    ),
                    xaxis=dict(title="Year", tickmode="linear", dtick=1,
                               title_font=dict(color="black"), tickfont=dict(color="black")),
                    yaxis=dict(title="Average Annual Employment", tickformat=",",
                               title_font=dict(color="black"), tickfont=dict(color="black")),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                xanchor="right", x=1, font=dict(color="black")),
                    plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                    margin=dict(t=80, b=40, l=60, r=30), hovermode="x unified",
                )
                st.plotly_chart(fig_state_sec, use_container_width=True)

            st.markdown("---")

            # ═════════════════════════════════════════════════════════════
            # COUNTY SECTION
            # ═════════════════════════════════════════════════════════════
            st.markdown(f"### {selected_county} — Sector Detail")

            c_sec = county_sector_df[
                county_sector_df["county_name"] == selected_county
            ].copy()

            if c_sec.empty:
                st.info("No sector data available for this county.")
            else:
                # ── County workforce supply stats ─────────────────────────
                c_sum     = summary[summary["county_name"] == selected_county].iloc[0]
                c_wf_2023 = float(c_sum["workforce_base"])
                c_wf_end  = float(c_sum["wf_end_p50"])
                c_wf_pct  = float(c_sum["pct_change_end"])

                # ── County KPI row ────────────────────────────────────────
                c_kpi_cols = st.columns(4)
                c_supply_cls = "growing" if c_wf_pct >= 0 else "declining"

                # Total sector jobs for this county
                c_end_rows  = c_sec[c_sec["year"] == sec_end]
                c_jobs_2023 = c_sec.drop_duplicates("sector")["emp_2023"].dropna().sum()
                c_jobs_end  = float(c_end_rows["emp_proj"].sum()) if not c_end_rows.empty else 0
                c_jobs_delta = c_jobs_end - c_jobs_2023
                c_jobs_cls  = "growing" if c_jobs_delta >= 0 else "declining"

                c_kpi_cols[0].markdown(metric_card(
                    "County Workers Available (2023)",
                    _fmt(c_wf_2023),
                    f'<span class="{c_supply_cls}">{c_wf_pct:+.1f}% by {sec_end}</span>',
                ), unsafe_allow_html=True)
                c_kpi_cols[1].markdown(metric_card(
                    f"Projected Workers Available ({sec_end})",
                    _fmt(c_wf_end),
                    f'<span class="{c_supply_cls}">{_fmt(c_wf_end - c_wf_2023)} net change</span>',
                ), unsafe_allow_html=True)
                c_kpi_cols[2].markdown(metric_card(
                    "County Sector Jobs (2023)",
                    _fmt(c_jobs_2023),
                    "",
                ), unsafe_allow_html=True)
                c_kpi_cols[3].markdown(metric_card(
                    f"County Sector Jobs Projected ({sec_end})",
                    _fmt(c_jobs_end),
                    f'<span class="{c_jobs_cls}">{c_jobs_delta:+,.0f} net new jobs</span>',
                ), unsafe_allow_html=True)

                st.markdown("<br>", unsafe_allow_html=True)

                # ── County gap bar chart ──────────────────────────────────
                st.markdown(f"#### {selected_county} — Jobs Today vs. Jobs Needed by Sector")

                c_gap_sectors = []
                c_gap_base    = []
                c_gap_proj    = []
                c_gap_delta   = []
                c_gap_ci_lo   = []
                c_gap_ci_hi   = []
                c_gap_colors  = []

                for sector in SECTORS:
                    s_row   = c_sec[c_sec["sector"] == sector]
                    end_row = s_row[s_row["year"] == sec_end]
                    if s_row.empty or end_row.empty:
                        continue
                    b23 = s_row["emp_2023"].iloc[0]
                    epr = float(end_row["emp_proj"].iloc[0])
                    elo = float(end_row["emp_ci_lo"].iloc[0])
                    ehi = float(end_row["emp_ci_hi"].iloc[0])
                    c_gap_sectors.append(sector)
                    c_gap_base.append(b23 if not pd.isna(b23) else 0)
                    c_gap_proj.append(epr)
                    c_gap_delta.append(epr - (b23 if not pd.isna(b23) else 0))
                    c_gap_ci_lo.append(elo)
                    c_gap_ci_hi.append(ehi)
                    c_gap_colors.append(SECTOR_COLORS[sector])

                fig_c_gap = go.Figure()
                fig_c_gap.add_trace(go.Bar(
                    name="2023 Actual Jobs",
                    x=c_gap_sectors,
                    y=c_gap_base,
                    marker_color=[c + "CC" for c in c_gap_colors],
                    marker_line_color=c_gap_colors,
                    marker_line_width=1.5,
                    text=[_fmt(v) for v in c_gap_base],
                    textposition="outside",
                    hovertemplate="<b>%{x}</b><br>2023 Jobs: %{y:,.0f}<extra></extra>",
                ))
                fig_c_gap.add_trace(go.Bar(
                    name=f"Projected Jobs ({sec_end})",
                    x=c_gap_sectors,
                    y=c_gap_proj,
                    marker_color=["rgba(255,255,255,0)" for _ in c_gap_colors],
                    marker_line_color=c_gap_colors,
                    marker_line_width=2.5,
                    marker_pattern_shape="/",
                    marker_pattern_fgcolor=c_gap_colors,
                    marker_pattern_bgcolor=["rgba(255,255,255,0.6)" for _ in c_gap_colors],
                    text=[
                        f"{_fmt(v)}<br><b>{d:+,.0f}</b>"
                        for v, d in zip(c_gap_proj, c_gap_delta)
                    ],
                    textposition="outside",
                    hovertemplate=(
                        "<b>%{x}</b><br>"
                        f"Projected ({sec_end}): %{{y:,.0f}}<extra></extra>"
                    ),
                    error_y=dict(
                        type="data",
                        symmetric=False,
                        array=[hi - p for hi, p in zip(c_gap_ci_hi, c_gap_proj)],
                        arrayminus=[p - lo for lo, p in zip(c_gap_ci_lo, c_gap_proj)],
                        color=C_NEUTRAL,
                        thickness=1.5,
                        width=6,
                    ),
                ))
                # County supply reference line
                fig_c_gap.add_hline(
                    y=c_wf_end,
                    line_dash="dot",
                    line_color=C_GOLD,
                    line_width=2,
                    annotation_text=f"County workforce supply {sec_end}: {_fmt(c_wf_end)}",
                    annotation_position="top left",
                    annotation_font_color=C_GOLD,
                    annotation_font_size=11,
                )
                fig_c_gap.update_layout(
                    barmode="group",
                    bargap=0.25,
                    bargroupgap=0.08,
                    title=dict(
                        text=(f"{selected_county} — Sector Employment: 2023 Actual vs. "
                              f"{sec_end} Projected Need"),
                        font=dict(size=15, color=C_BLUE),
                    ),
                    xaxis=dict(title="Sector", title_font=dict(color="black"),
                               tickfont=dict(color="black")),
                    yaxis=dict(title="Workers", tickformat=",",
                               title_font=dict(color="black"), tickfont=dict(color="black")),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                xanchor="right", x=1, font=dict(color="black")),
                    plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                    margin=dict(t=80, b=60, l=70, r=30),
                    uniformtext_minsize=9, uniformtext_mode="hide",
                )
                st.plotly_chart(fig_c_gap, use_container_width=True)

                st.markdown("---")

                # ── Sector-detail CI chart ────────────────────────────────
                st.markdown("#### Sector Deep-Dive")
                sec_choice = st.radio(
                    "Select sector for detailed trend view",
                    SECTORS,
                    horizontal=True,
                )

                one     = c_sec[c_sec["sector"] == sec_choice].sort_values("year")
                color_c = SECTOR_COLORS[sec_choice]

                if one.empty:
                    st.warning(f"No projection data for {sec_choice} in {selected_county}.")
                else:
                    method_val = one["method"].iloc[0]
                    sig_val    = bool(one["significant"].iloc[0])
                    note_val   = one["note"].iloc[0]
                    emp_2023   = one["emp_2023"].iloc[0]
                    emp_end_c  = float(one[one["year"] == sec_end]["emp_proj"].iloc[0]) \
                                 if len(one[one["year"] == sec_end]) else None

                    method_labels = {
                        "option_b":          (
                            "✔ Option B — Independent County OLS Trend"
                            + (" (significant)" if sig_val else " (trend uncertain — see CI)"),
                            C_GREEN if sig_val else C_GOLD),
                        "option_a_fallback": ("⚠ Option A — State Share (legacy fallback)", C_GOLD),
                        "option_a":          ("ℹ Option A — State Share Model", C_NEUTRAL),
                        "no_data":           ("✗ No Data — Estimate Only", C_RED),
                    }
                    badge_text, badge_color = method_labels.get(
                        method_val, (method_val, C_NEUTRAL))
                    st.markdown(
                        f'<div style="background:{badge_color}18; border-left:4px solid {badge_color}; '
                        f'padding:0.5rem 0.8rem; border-radius:0 6px 6px 0; '
                        f'font-size:0.85rem; margin-bottom:0.5rem;">'
                        f'<strong>{badge_text}</strong><br>'
                        f'<span style="color:#555">{note_val}</span></div>',
                        unsafe_allow_html=True,
                    )

                    fig_sec_c = go.Figure()
                    fig_sec_c.add_trace(go.Scatter(
                        x=pd.concat([one["year"], one["year"].iloc[::-1]]),
                        y=pd.concat([one["emp_ci_hi"], one["emp_ci_lo"].iloc[::-1]]),
                        fill="toself",
                        fillcolor=(f"rgba({int(color_c[1:3],16)},"
                                   f"{int(color_c[3:5],16)},"
                                   f"{int(color_c[5:7],16)},0.15)"),
                        line=dict(color="rgba(255,255,255,0)"),
                        name="80% CI", hoverinfo="skip",
                    ))
                    fig_sec_c.add_trace(go.Scatter(
                        x=one["year"], y=one["emp_proj"],
                        mode="lines+markers", name="Projected",
                        line=dict(color=color_c, width=2.5, dash="dash"),
                        marker=dict(size=6),
                        hovertemplate="<b>%{x}</b><br>Projected: %{y:,.0f}<extra></extra>",
                    ))
                    if emp_2023 and not pd.isna(emp_2023):
                        fig_sec_c.add_trace(go.Scatter(
                            x=[2023], y=[emp_2023], mode="markers",
                            name="2023 QCEW Baseline",
                            marker=dict(color=C_GOLD, size=12, symbol="diamond"),
                            hovertemplate=f"<b>2023 Baseline</b><br>%{{y:,.0f}}<extra></extra>",
                        ))

                    pct_lbl = ""
                    if emp_2023 and emp_end_c and emp_2023 > 0:
                        pct_lbl = f" ({(emp_end_c - emp_2023) / emp_2023 * 100:+.1f}% vs 2023)"

                    fig_sec_c.update_layout(
                        title=dict(
                            text=f"{selected_county} — {sec_choice} Employment{pct_lbl}",
                            font=dict(size=15, color=C_BLUE),
                        ),
                        xaxis=dict(title="Year", tickmode="linear", dtick=1,
                                   title_font=dict(color="black"), tickfont=dict(color="black")),
                        yaxis=dict(title="Average Annual Employment", tickformat=",",
                                   title_font=dict(color="black"), tickfont=dict(color="black")),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                    xanchor="right", x=1, font=dict(color="black")),
                        plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                        margin=dict(t=80, b=40, l=60, r=30), hovermode="x unified",
                    )
                    st.plotly_chart(fig_sec_c, use_container_width=True)

                # ── All-sector summary table ──────────────────────────────
                st.markdown("#### All Sectors — Summary Table")
                tbl_cols = st.columns(2)

                with tbl_cols[0]:
                    st.markdown("**Employment: Need vs. Available Workers**")
                    emp_rows = []
                    for sector in SECTORS:
                        s_row   = c_sec[c_sec["sector"] == sector]
                        end_row = s_row[s_row["year"] == sec_end]
                        if s_row.empty:
                            continue
                        emp_23  = s_row["emp_2023"].iloc[0]
                        emp_e   = float(end_row["emp_proj"].iloc[0])  if len(end_row) else None
                        ci_lo   = float(end_row["emp_ci_lo"].iloc[0]) if len(end_row) else None
                        ci_hi   = float(end_row["emp_ci_hi"].iloc[0]) if len(end_row) else None
                        meth    = s_row["method"].iloc[0]
                        net_new = (emp_e - emp_23) if (emp_e and emp_23 and not pd.isna(emp_23)) else None
                        pct_chg = (net_new / emp_23 * 100) if (net_new is not None and emp_23 > 0) else None
                        emp_rows.append({
                            "Sector":             sector,
                            "2023 Jobs":          _fmt(emp_23) if not pd.isna(emp_23) else "—",
                            f"Jobs Needed {sec_end}": _fmt(emp_e) if emp_e else "—",
                            "Net New Jobs":       f"{net_new:+,.0f}" if net_new is not None else "—",
                            "80% CI":             (f"{_fmt(ci_lo)} – {_fmt(ci_hi)}"
                                                   if ci_lo and ci_hi else "—"),
                            "% Change":           f"{pct_chg:+.1f}%" if pct_chg is not None else "—",
                            "Model":              "B" if meth == "option_b" else "A*",
                        })
                    st.dataframe(pd.DataFrame(emp_rows), hide_index=True,
                                 use_container_width=True)
                    st.caption("Model: B = independent OLS trend  |  A* = state share model")

                with tbl_cols[1]:
                    st.markdown("**Projected Avg Annual Wages**")
                    wage_rows = []
                    for sector in SECTORS:
                        s_row     = c_sec[c_sec["sector"] == sector]
                        end_row   = s_row[s_row["year"] == sec_end]
                        start_row = s_row[s_row["year"] == sec_start]
                        if s_row.empty:
                            continue
                        wage_s = float(start_row["wage_proj"].iloc[0]) if len(start_row) else None
                        wage_e = float(end_row["wage_proj"].iloc[0])   if len(end_row)   else None
                        pct_w  = ((wage_e - wage_s) / wage_s * 100
                                  if wage_s and wage_e and wage_s > 0 else None)
                        wage_rows.append({
                            "Sector":              sector,
                            f"{sec_start} Proj":   f"${_fmt(wage_s)}" if wage_s else "—",
                            f"{sec_end} Proj":     f"${_fmt(wage_e)}" if wage_e else "—",
                            "% Change":            f"{pct_w:+.1f}%"   if pct_w  else "—",
                        })
                    st.dataframe(pd.DataFrame(wage_rows), hide_index=True,
                                 use_container_width=True)

            # ── Download ──────────────────────────────────────────────────
            csv_sec = c_sec.to_csv(index=False).encode("utf-8") \
                      if not c_sec.empty else b""
            if csv_sec:
                st.download_button(
                    label=f"Download {selected_county} Sector Data (CSV)",
                    data=csv_sec,
                    file_name=(f"{selected_county.lower().replace(' ', '_')}_"
                               f"sector_forecast_{sec_start}_{sec_end}.csv"),
                    mime="text/csv",
                )

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
| BLS Quarterly Census of Employment & Wages (QCEW) | County annual employment and wages by NAICS sector, 2015–2023 |

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

### Industry Sector Forecast
County-level employment and wage projections for five sectors using BLS QCEW 2015–2023
annual averages. NAICS code groupings:

| Sector | NAICS Codes |
|--------|------------|
| Healthcare | 62 — Health Care & Social Assistance |
| Manufacturing | 31–33 — Manufacturing (incl. production workers) |
| Hospitality & Entertainment | 71 + 72 — Arts/Recreation + Accommodation/Food Services |
| IT/Computer Services | 51 + 54 — Information + Professional/Scientific/Technical Services |
| Skilled Trades | 22 + 23 + 81 — Utilities + Construction/HVAC/Heavy Equipment + Auto/Diesel Repair |

**Option B** (independent county OLS trend) — used when 2023 county employment ≥ **500**
AND at least 3 historical observations exist. Employment is fit with a **log-linear**
OLS regression (fit on `log(employment)`, project, exponentiate back). Confidence
intervals are 80% prediction intervals from the regression, back-transformed to the
employment scale.

**Option A** (state share model) — used only when employment < 500, fewer than 3
observations, or data is suppressed. Projects the county's historical share of state
sector employment multiplied by the state-level OLS projection. Where no county history
exists, the county's share of state working-age population serves as the denominator
proxy.

Wage projections use county-level linear OLS where ≥3 observations are available; fall
back to state-level wage trend otherwise.

#### 2026-04-25 model revision — why these changes

The previous model used Option B only when the OLS slope was statistically significant
at p < 0.05 AND 2023 employment was ≥ 2,000. In practice this caused **100% of Kansas
county-sector pairs to fall back to Option A**, including Johnson County Healthcare
(~53,500 workers) and Sedgwick County Manufacturing (~46,100 workers) — the actual
county-level trends were never being shown. Three changes were made:

1. **Significance gate removed.** The 80% prediction interval already widens
   appropriately when the trend is uncertain, so a hard p < 0.05 cutoff was the wrong
   instrument. With only 9 years of data (2015–2023) and COVID disruption in 2020–2021,
   real trends often fail the test at p < 0.05 but still convey useful direction. The
   `significant` flag is preserved and shown in the badge as informational context.
2. **MIN_OPT_B lowered from 2,000 → 500.** Kansas is dominated by small counties; the
   higher threshold excluded ~740 county-sector pairs whose trends are well-defined.
3. **Log-linear OLS** replaces straight linear OLS for employment fitting. Employment
   evolves multiplicatively (% growth per year), so log-space fitting produces more
   stable projections, prevents negative values, and yields long-run paths that match
   historical compounding behavior. Linear OLS is used automatically if any historical
   value is zero (log undefined), and remains the default for wage projections.

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
