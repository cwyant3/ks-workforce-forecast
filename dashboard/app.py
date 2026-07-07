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

import json
import os
import sys
import urllib.request
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
GEO_CACHE  = ROOT / "data" / "geo" / "geojson-counties-fips.json"
LOGO_PATH  = Path(__file__).parent / "wsu-tech-logo.png"

# County-boundary GeoJSON. Passing a URL to Plotly makes the *browser* fetch it
# at render time — which silently fails on networks that block
# raw.githubusercontent.com (e.g. WSU Tech), leaving a blank map. We instead
# load the geometry server-side and embed it in the figure so no client-side
# fetch is needed. jsDelivr mirrors the same file and is usually reachable when
# raw.githubusercontent.com is not.
_GEO_URLS = [
    "https://cdn.jsdelivr.net/gh/plotly/datasets@master/geojson-counties-fips.json",
    "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json",
]

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
    page_title="Workforce Funnel Dashboard",
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
    .exec-grid {{
        display: grid; grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 0.75rem; margin: 0.7rem 0 1.2rem;
    }}
    .exec-card {{
        background: white; border: 1px solid #DCE3EC; border-radius: 8px;
        padding: 0.9rem 1rem; min-height: 128px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.04);
    }}
    .exec-card .eyebrow {{
        color: {C_GOLD}; font-size: 0.74rem; font-weight: 800;
        letter-spacing: 0; text-transform: uppercase;
    }}
    .exec-card .headline {{
        color: {C_BLUE}; font-size: 1.05rem; font-weight: 800;
        margin-top: 0.2rem;
    }}
    .exec-card .detail {{
        color: #34495E; font-size: 0.82rem; line-height: 1.35;
        margin-top: 0.35rem;
    }}
    .funnel-strip {{
        display: grid; grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 0.65rem; margin: 0.35rem 0 1.25rem;
    }}
    .funnel-stage {{
        background: white; border: 1px solid #DCE3EC; border-radius: 8px;
        padding: 0.75rem 0.85rem; min-height: 92px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.04);
    }}
    .funnel-stage .step {{
        color: {C_GOLD}; font-size: 0.75rem; font-weight: 800;
        letter-spacing: 0; text-transform: uppercase;
    }}
    .funnel-stage .title {{
        color: {C_BLUE}; font-size: 0.98rem; font-weight: 800;
        margin-top: 0.18rem;
    }}
    .funnel-stage .copy {{
        color: #34495E; font-size: 0.78rem; line-height: 1.25;
        margin-top: 0.25rem;
    }}
    @media (max-width: 900px) {{
        .exec-grid {{ grid-template-columns: 1fr; }}
        .funnel-strip {{ grid-template-columns: 1fr; }}
        .funnel-stage {{ min-height: auto; }}
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


@st.cache_data(show_spinner="Loading total-employment data…")
def load_total_employment(state_fips: str):
    """True all-industries (QCEW naics=10) total-employment projection.

    Returns None when the file is absent so the Sector Exposure chart can fall
    back to the focus-sector line only (states forecast before this layer was
    added won't have the file until they're regenerated / backfilled)."""
    f = OUTPUT_DIR / f"state_total_projection_s{state_fips}.parquet"
    return pd.read_parquet(f) if f.exists() else None


def data_exists(state_fips: str) -> bool:
    return all((OUTPUT_DIR / f"{stem}_s{state_fips}.{ext}").exists()
               for stem, ext in [("projections", "parquet"),
                                  ("county_summary", "csv"),
                                  ("state_projection", "parquet")])


def run_forecast_for_state(state_fips: str, force: bool = False):
    """Bootstrap a cohort forecast for a state that has NO data yet.

    This is a fast, cohort-only build — it does NOT fetch the LAUS, SSA, sector,
    IPEDS, LODES, OES, CBP, or projection layers, so the participation model it
    writes is base-only (ACS+ACS_LFPR). Full-parity builds come from the CLI
    (`run_forecast.py --all --state X`) or the monthly refresh_dashboard task.

    GUARD: it refuses to run when the state already has data (unless force=True).
    The "Generate Forecast" button is gated by `not data_exists()` at RENDER time,
    but Streamlit reruns / double-clicks can fire this after data exists — without
    this execution-time guard, that silently OVERWRITES a state's full per-state
    outputs with the cohort-only versions, stripping the SSA/LAUS participation
    layers. (This regression hit CO/NE/KS during the 2026-06 multi-state build.)
    """
    if data_exists(state_fips) and not force:
        raise RuntimeError(
            f"{FIPS_STATE.get(state_fips, state_fips)} already has forecast data — "
            f"refusing to regenerate. This cohort-only build would overwrite the full "
            f"per-state outputs and drop the SSA/LAUS participation layers. To rebuild "
            f"from scratch, run `python run_forecast.py --all --state {state_fips}` "
            f"from the CLI, then commit the regenerated outputs."
        )
    api_key = _census_api_key()
    if not api_key:
        raise RuntimeError(
            "No Census API key found. Live forecast generation needs one. "
            "Set CENSUS_API_KEY in ks_workforce_forecast/.env (local) or in the "
            "Streamlit app's Secrets (cloud). If you launched from a git worktree, "
            "relaunch from the main project directory where .env lives. "
            "Free key: https://api.census.gov/data/key_signup.html"
        )
    from run_forecast import main as forecast_main
    forecast_main(
        state_fips=state_fips,
        api_key=api_key,
        n_sim=2000,
        start_year=2026,
        end_year=2035,
        run_sectors=False,   # cohort only; sectors run separately
    )
    st.cache_data.clear()


def run_sector_forecast_for_state(state_fips: str):
    """Fetch QCEW and run sector model for a state that already has cohort data."""
    from fetch_qcew   import fetch_state_qcew
    from sector_model import run_all_sectors, project_total_employment

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
    state_total_df = project_total_employment(
        state_totals = state_totals,
        cohort_proj  = proj_df,
    )
    county_sector_df.to_parquet(
        OUTPUT_DIR / f"sector_projections_s{state_fips}.parquet", index=False)
    state_sector_df.to_parquet(
        OUTPUT_DIR / f"state_sector_projection_s{state_fips}.parquet", index=False)
    state_total_df.to_parquet(
        OUTPUT_DIR / f"state_total_projection_s{state_fips}.parquet", index=False)
    st.cache_data.clear()


def sector_data_exists(state_fips: str) -> bool:
    return all((OUTPUT_DIR / f"{stem}_s{state_fips}.parquet").exists()
               for stem in ["sector_projections", "state_sector_projection"])


@st.cache_data(show_spinner="Loading training pipeline data…")
def load_ipeds(state_fips: str):
    f = OUTPUT_DIR / f"ipeds_by_sector_s{state_fips}.parquet"
    return pd.read_parquet(f) if f.exists() else None


@st.cache_data(show_spinner="Loading commute data…")
def load_commute(state_fips: str):
    f = OUTPUT_DIR / f"commute_snapshot_s{state_fips}.parquet"
    return pd.read_parquet(f) if f.exists() else None


@st.cache_data(show_spinner="Loading JOLTS vacancy data…")
def load_jolts():
    f = OUTPUT_DIR / "jolts_vacancy_rates.parquet"
    return pd.read_parquet(f) if f.exists() else None


@st.cache_data(show_spinner="Loading KDOL labor market pulse…")
def load_kdol():
    f = OUTPUT_DIR / "kdol_sector_pulse.parquet"
    return pd.read_parquet(f) if f.exists() else None


@st.cache_data(show_spinner="Loading participation model data…")
def load_participation(state_fips: str):
    f = OUTPUT_DIR / f"participation_s{state_fips}.parquet"
    return pd.read_parquet(f) if f.exists() else None


@st.cache_data(show_spinner="Loading BLS projections…")
def load_bls_outlook():
    f = OUTPUT_DIR / "bls_proj_sector_outlook.parquet"
    return pd.read_parquet(f) if f.exists() else None


@st.cache_data(show_spinner="Loading KS occupational projections…")
def load_ks_occ_in_demand(state_fips: str):
    f = OUTPUT_DIR / f"ks_occ_in_demand_top_s{state_fips}.parquet"
    return pd.read_parquet(f) if f.exists() else None


@st.cache_data(show_spinner="Loading KS sector outlook…")
def load_ks_occ_by_sector(state_fips: str):
    f = OUTPUT_DIR / f"ks_occ_by_sector_s{state_fips}.parquet"
    return pd.read_parquet(f) if f.exists() else None


@st.cache_data(show_spinner="Loading KDOL labor force state…")
def load_kdol_labforce_state(state_fips: str):
    f = OUTPUT_DIR / f"kdol_labforce_state_s{state_fips}.parquet"
    return pd.read_parquet(f) if f.exists() else None


@st.cache_data(show_spinner="Loading KDOL labor force county-recent…")
def load_kdol_labforce_county_recent(state_fips: str):
    f = OUTPUT_DIR / f"kdol_labforce_county_recent_s{state_fips}.parquet"
    return pd.read_parquet(f) if f.exists() else None


@st.cache_data(show_spinner="Loading CBP establishment trends…")
def load_cbp_estab_trends(state_fips: str):
    f = OUTPUT_DIR / f"cbp_estab_trends_s{state_fips}.parquet"
    return pd.read_parquet(f) if f.exists() else None


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


def quality_badges(*labels: str) -> str:
    chips = "".join(
        f'<span style="display:inline-block;background:#EEF2F7;border:1px solid #D6DEE8;'
        f'border-radius:999px;padding:0.18rem 0.55rem;margin:0.15rem 0.25rem 0.15rem 0;'
        f'font-size:0.78rem;color:#2C3E50;">{label}</span>'
        for label in labels if label
    )
    return f'<div style="margin:0.4rem 0 0.8rem;">{chips}</div>' if chips else ""


def funnel_strip(selected_state: str) -> str:
    stages = [
        ("01", "Population", f"How many working-age residents of {selected_state} are in the baseline and forecast?"),
        ("02", "Available Workforce", "How much of that population is plausibly available to work?"),
        ("03", "Demand Pressure", "Where do openings, claims, and projections suggest pressure?"),
        ("04", "Sector Exposure", "Which broad sectors create the biggest planning exposure?"),
        ("05", "Local Action", "Where can training, commuting, and completions change yield?"),
    ]
    cards = "".join(
        f"""<div class="funnel-stage">
            <div class="step">{step}</div>
            <div class="title">{title}</div>
            <div class="copy">{copy}</div>
        </div>"""
        for step, title, copy in stages
    )
    return f'<div class="funnel-strip">{cards}</div>'


def executive_county_default(summary: pd.DataFrame, selected_state: str) -> str:
    """Use WSU-Tech's home labor market as the Kansas narrative default."""
    counties = set(summary["county_name"].astype(str))
    if selected_state == "Kansas" and "Sedgwick County" in counties:
        return "Sedgwick County"
    return summary.loc[summary["workforce_base"].idxmax(), "county_name"]


def executive_card(step: str, headline: str, detail: str) -> str:
    return f"""<div class="exec-card">
        <div class="eyebrow">{step}</div>
        <div class="headline">{headline}</div>
        <div class="detail">{detail}</div>
    </div>"""


def executive_grid(cards: list[tuple[str, str, str]]) -> str:
    body = "".join(executive_card(step, headline, detail) for step, headline, detail in cards)
    return f'<div class="exec-grid">{body}</div>'


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return "\n".join([header, divider, body]) if rows else "\n".join([header, divider])


def build_narrative_handoff(
    selected_state: str,
    spotlight_county: str,
    base_year: int,
    end_year: int,
    summary: pd.DataFrame,
    state_sector_df: pd.DataFrame | None,
    part_df: pd.DataFrame | None,
    jolts_df: pd.DataFrame | None,
    bls_df: pd.DataFrame | None,
    kdol_df: pd.DataFrame | None,
    ipeds_df: pd.DataFrame | None,
    commute_df: pd.DataFrame | None,
) -> str:
    """Create speaker-ready notes that mirror the executive funnel view."""
    from fetch_qcew import SECTOR_DISPLAY_NAMES

    total_base = float(summary["workforce_base"].sum())
    total_end = float(summary["wf_end_p50"].sum())
    net_chg = total_end - total_base
    pct_chg = net_chg / total_base * 100 if total_base else 0
    declining = int((summary["pct_change_end"] < 0).sum())
    annual_flow = float(summary["annual_entries_end"].sum() - summary["annual_retirements_end"].sum())
    spotlight = summary[summary["county_name"] == spotlight_county].iloc[0]

    has_real_participation = (
        part_df is not None and not part_df.empty
        and "layers_used" in part_df.columns
        and (part_df["layers_used"].astype(str) != "ACS_only").any()
    )
    availability_note = (
        "ACS labor-force-status participation is populated for at least part of the state."
        if has_real_participation
        else "Do not describe this as effective labor force yet; current participation output has no LFPR layer."
    )

    demand_sources = []
    if jolts_df is not None and not jolts_df.empty:
        demand_sources.append("JOLTS vacancy rates")
    if bls_df is not None and not bls_df.empty:
        demand_sources.append("BLS employment projections")
    if kdol_df is not None and not kdol_df.empty:
        demand_sources.append("KDOL UI claims")
    demand_note = (
        ", ".join(demand_sources) + " are loaded as directional demand context."
        if demand_sources
        else "Do not make vacancy/openings claims; JOLTS, BLS, and KDOL demand layers are not populated or validated."
    )

    sector_note = "Sector exposure layer is not available."
    sector_rows: list[list[str]] = []
    sec_base = base_year
    if state_sector_df is not None and not state_sector_df.empty:
        sector_end_year = int(state_sector_df["year"].max())
        if "base_year" in state_sector_df.columns:
            sec_base = int(state_sector_df["base_year"].iloc[0])
        sector_end = state_sector_df[state_sector_df["year"] == sector_end_year].copy()
        sector_end["sector_label"] = sector_end["sector"].map(
            lambda s: SECTOR_DISPLAY_NAMES.get(s, s)
        )
        sector_end["net_jobs"] = sector_end["emp_proj"] - sector_end["emp_base"]
        sector_end = sector_end.sort_values("net_jobs", key=lambda s: s.abs(), ascending=False)
        top_sector = sector_end.iloc[0]
        sector_note = (
            f"Largest broad sector movement: {top_sector['sector_label']} "
            f"({_fmt(top_sector['net_jobs'])} net jobs, {sec_base} to {sector_end_year}). "
            "Frame this as exposure context, not a vacancies claim."
        )
        sector_rows = [
            [
                row["sector_label"],
                _fmt(row["emp_base"]),
                _fmt(row["emp_proj"]),
                f"{row['net_jobs']:+,.0f}",
            ]
            for _, row in sector_end.iterrows()
        ]

    local_rows = []
    if ipeds_df is not None and not ipeds_df.empty:
        latest_ipeds = int(ipeds_df["year"].max())
        completions = float(ipeds_df[ipeds_df["year"] == latest_ipeds]["completions"].sum())
        local_rows.append(["IPEDS completions", _fmt(completions), f"{latest_ipeds} statewide completions"])
    else:
        local_rows.append(["IPEDS completions", "not loaded", "No training-output claim"])
    if commute_df is not None and not commute_df.empty:
        latest_commute = int(commute_df["year"].max())
        imported = float(commute_df["pct_workers_imported"].mean())
        local_rows.append(["LODES commute flows", f"{imported:.1f}% avg imported workers", f"{latest_commute} snapshot"])
    else:
        local_rows.append(["LODES commute flows", "not loaded", "No labor-shed claim"])

    funnel_rows = [
        [
            "Population",
            f"{selected_state} moves from {_fmt(total_base)} working-age residents in {base_year} "
            f"to {_fmt(total_end)} by {end_year} ({pct_chg:+.1f}%).",
            "ACS cohort model.",
        ],
        ["Available Workforce", availability_note, "Participation is not the same as population."],
        ["Demand Pressure", demand_note, "Demand claims require validated openings, claims, or projection layers."],
        ["Sector Exposure", sector_note, "QCEW sectors are employment context."],
        ["Local Action", "Use completions and commute flows to size training response.", "IPEDS/LODES are action signals."],
    ]

    sector_table = (
        "\n\n## Sector Exposure Detail\n"
        + _markdown_table(["Sector", f"{sec_base} jobs", f"{end_year} projected", "Net change"], sector_rows)
        if sector_rows else ""
    )

    return f"""# {selected_state} Workforce Dashboard - Presentation Handoff

Forecast window: {base_year}-{end_year}

## Executive Takeaway
- Working-age population changes by {pct_chg:+.1f}% statewide, from {_fmt(total_base)} to {_fmt(total_end)}.
- Net statewide change: {_fmt(net_chg)} working-age residents.
- {declining} of {len(summary)} counties decline in the median projection.
- Annual net flow by {end_year}: {_fmt(annual_flow)} entries minus retirements.

## Funnel Talking Points
{_markdown_table(["Stage", "Speaker note", "Guardrail"], funnel_rows)}

## Spotlight County - {spotlight_county}
{_markdown_table(
    ["Signal", "Value", "Read"],
    [
        ["Working-age population", _fmt(spotlight["workforce_base"]), f"{base_year} baseline"],
        ["Median projection", _fmt(spotlight["wf_end_p50"]), f"{spotlight['pct_change_end']:+.1f}% by {end_year}"],
        ["Annual retirements", _fmt(spotlight["annual_retirements_end"]), f"{end_year} exit pressure"],
        ["Annual entries", _fmt(spotlight["annual_entries_end"]), f"{end_year} youth pipeline"],
        ["Migration history", f"{spotlight['mig_mean_pct']:+.2f}%/yr", "ACS cohort residual"],
    ],
)}
{sector_table}

## Local Action Signals
{_markdown_table(["Layer", "Current value", "How to use it"], local_rows)}

## Claims To Avoid
- Do not call working-age population "labor force" unless ACS B23001 LFPR is populated.
- Do not call sector employment change "job openings" or "vacancies."
- Do not treat IPEDS completions as placements or local retention.
- Do not compare residence-based population directly to worksite employment without commute context.
"""


def build_methodology_handoff(
    selected_state: str,
    base_year: int,
    end_year: int,
    part_df: pd.DataFrame | None,
    jolts_df: pd.DataFrame | None,
    bls_df: pd.DataFrame | None,
    kdol_df: pd.DataFrame | None,
    ipeds_df: pd.DataFrame | None,
    commute_df: pd.DataFrame | None,
) -> str:
    layer_rows = [
        ["ACS cohort model", "loaded", "Population baseline and forecast"],
        ["QCEW sector model", "loaded when sector outputs exist", "Employment exposure, not openings"],
        [
            "ACS/SSA participation",
            "loaded" if part_df is not None and not part_df.empty else "not loaded",
            "Effective labor force from ACS B23001 LFPR, with optional SSA adjustment",
        ],
        [
            "JOLTS vacancy rates",
            "loaded" if jolts_df is not None and not jolts_df.empty else "not loaded",
            "National vacancy context",
        ],
        [
            "BLS employment projections",
            "loaded" if bls_df is not None and not bls_df.empty else "not loaded",
            "National structural demand context",
        ],
        [
            "KDOL UI claims",
            "loaded" if kdol_df is not None and not kdol_df.empty else "not loaded",
            "Kansas-only current labor market pulse",
        ],
        [
            "IPEDS completions",
            "loaded" if ipeds_df is not None and not ipeds_df.empty else "not loaded",
            "Training-output proxy",
        ],
        [
            "LODES commute flows",
            "loaded" if commute_df is not None and not commute_df.empty else "not loaded",
            "Labor-shed and in-commuter context",
        ],
    ]

    return f"""# {selected_state} Workforce Dashboard - Methodology Handoff

Forecast window: {base_year}-{end_year}

## Core Model
The dashboard uses an annual cohort-component model for working-age population (18-64)
by county. It starts from ACS 5-year age-by-sex estimates, applies survival and aging,
models entry from the 15-17 cohort into 18-24, models retirement exits from 60-64 into
65+, and applies county migration residuals through Monte Carlo simulation.

## Layer Status
{_markdown_table(["Layer", "Status", "Use"], layer_rows)}

## Presentation Guardrails
- ACS is a residence-based population estimate, not a count of available workers.
- ACS B23001 labor-force-status fields are required before saying "effective labor force."
- QCEW sector projections describe employment exposure; they do not measure vacancies.
- JOLTS and BLS demand layers are national unless specifically regenerated at a state layer.
- KDOL UI claims are Kansas-only and should be framed as a pulse, not a forecast.
- LODES lags by 2-3 years and should be used for labor-shed scale, not real-time commuting.

## Recommended Speaker Framing
Use the dashboard as a funnel: population baseline first, availability second, demand
pressure only where validated, sector exposure as context, and local action through
training output plus commute-shed evidence.
"""


# ── Charts ────────────────────────────────────────────────────────────────────
def ci_chart(df: pd.DataFrame, title: str,
             baseline: float | None = None,
             base_year: int = 2024) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pd.concat([df["year"], df["year"].iloc[::-1]]),
        y=pd.concat([df["p95"], df["p5"].iloc[::-1]]),
        fill="toself", fillcolor="rgba(0,63,135,0.10)",
        line=dict(color="rgba(255,255,255,0)"), name="90% PI", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=pd.concat([df["year"], df["year"].iloc[::-1]]),
        y=pd.concat([df["p90"], df["p10"].iloc[::-1]]),
        fill="toself", fillcolor="rgba(0,63,135,0.17)",
        line=dict(color="rgba(255,255,255,0)"), name="80% PI", hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=pd.concat([df["year"], df["year"].iloc[::-1]]),
        y=pd.concat([df["p75"], df["p25"].iloc[::-1]]),
        fill="toself", fillcolor="rgba(0,63,135,0.28)",
        line=dict(color="rgba(255,255,255,0)"), name="50% PI (IQR)", hoverinfo="skip",
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


@st.cache_data(show_spinner=False)
def load_counties_geojson(state_fips: str | None = None) -> dict | None:
    """County-boundary GeoJSON as a dict for server-side embedding.

    Reads the local cache first; if absent, downloads once (jsDelivr, then the
    plotly raw URL) and writes the cache. When ``state_fips`` is given, only the
    features for that state are returned — this shrinks the figure payload from
    ~3 MB (all 3,221 US counties) to a few KB. Returns None if the geometry
    cannot be obtained, so the caller can show a graceful message."""
    gj = None
    if GEO_CACHE.exists():
        try:
            gj = json.loads(GEO_CACHE.read_text(encoding="utf-8"))
        except Exception:
            gj = None
    if gj is None:
        for url in _GEO_URLS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                data = urllib.request.urlopen(req, timeout=30).read()
                gj = json.loads(data)
                if gj.get("type") == "FeatureCollection" and gj.get("features"):
                    GEO_CACHE.parent.mkdir(parents=True, exist_ok=True)
                    GEO_CACHE.write_bytes(data)
                    break
                gj = None
            except Exception:
                gj = None
    if gj is None:
        return None
    if state_fips:
        sf = state_fips.zfill(2)
        feats = [f for f in gj["features"] if str(f.get("id", "")).startswith(sf)]
        return {"type": "FeatureCollection", "features": feats}
    return gj


def state_choropleth(summary: pd.DataFrame, state_fips: str,
                     end_year: int, metric: str = "pct_change_end",
                     base_year: int = 2024) -> go.Figure:
    df = summary.copy()
    df["fips5"] = state_fips.zfill(2) + df["county_fips"].astype(str).str.zfill(3)
    df["label"] = df["county_name"] + "<br>" + df[metric].map(lambda x: f"{x:+.1f}%")
    z_max = max(abs(df[metric].min()), abs(df[metric].max()), 1)
    state_name = FIPS_STATE.get(state_fips, state_fips)

    counties = load_counties_geojson(state_fips)
    if counties is None:
        # Signal the caller (which wraps this in try/except) to show a warning
        # instead of rendering a blank map.
        raise RuntimeError(
            "County boundary GeoJSON unavailable — could not read the local "
            "cache or download it (network may block the source)."
        )

    fig = go.Figure(go.Choropleth(
        geojson=counties, featureidkey="id",
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
            text=f"{state_name} — County Working-Age Population Change: {base_year} → {end_year} (Median %)",
            font=dict(size=15, color=C_BLUE), x=0.5, xanchor="center",
        ),
        height=420, margin=dict(t=60, b=10, l=0, r=0), paper_bgcolor="white",
    )
    return fig


def state_effective_labor_chart(part_df: pd.DataFrame,
                                state_name: str) -> tuple[go.Figure, dict]:
    """Statewide effective labor force vs. working-age population.

    Aggregates the participation model across all counties for the most
    recent ACS year and renders a waterfall from the raw working-age
    population down to the effective labor force, exposing the two
    structural decrements (SSA disability and ACS non-participation).
    Returns the figure plus a stats dict for KPI cards.
    """
    year = int(part_df["year"].max())
    snap = part_df[part_df["year"] == year]
    wap  = float(snap["working_age_pop"].sum())
    dadj = float(snap["disability_adjusted_pop"].sum())
    elf  = float(snap["effective_labor_force"].sum())

    disability_drop    = dadj - wap   # negative: SSA-determined disability
    participation_drop = elf - dadj   # negative: not in the labor force (ACS LFPR)
    gap     = wap - elf
    gap_pct = gap / wap * 100 if wap else 0

    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=["absolute", "relative", "relative", "total"],
        x=["Working-Age<br>Population (18–64)",
           "Less: disability<br>(SSA)",
           "Less: not in<br>labor force (ACS)",
           "Effective<br>Labor Force"],
        y=[wap, disability_drop, participation_drop, elf],
        text=[_fmt(wap), _fmt(disability_drop), _fmt(participation_drop), _fmt(elf)],
        textposition="outside",
        textfont=dict(color="black"),
        connector=dict(line=dict(color=C_NEUTRAL, width=1)),
        decreasing=dict(marker=dict(color=C_RED)),
        increasing=dict(marker=dict(color=C_GREEN)),
        totals=dict(marker=dict(color=C_BLUE)),
        hovertemplate="<b>%{x}</b><br>%{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(
            text=f"{state_name} — Effective Labor Force vs. Working-Age Population ({year})",
            font=dict(size=15, color=C_BLUE),
        ),
        yaxis=dict(title="People (18–64)", tickformat=",",
                   title_font=dict(color="black"), tickfont=dict(color="black")),
        xaxis=dict(tickfont=dict(color="black", size=11)),
        plot_bgcolor=C_LIGHT, paper_bgcolor="white",
        margin=dict(t=70, b=40, l=70, r=30), showlegend=False,
    )
    stats = dict(year=year, wap=wap, dadj=dadj, elf=elf, gap=gap, gap_pct=gap_pct)
    return fig, stats


# ── Main app ──────────────────────────────────────────────────────────────────
def main():

    # ── Sidebar — state selector at the very top ──────────────────────────
    with st.sidebar:
        st.image(str(LOGO_PATH), use_container_width=True)
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
        st.markdown("### Mode")
        view_mode = st.radio(
            "Dashboard mode",
            ["Executive Narrative", "Full Explorer"],
            index=0,
            label_visibility="collapsed",
        )

        if view_mode == "Executive Narrative":
            show_90ci = True
            show_50ci = False
            min_pop = 0
            st.caption("Curated state view with presentation defaults.")

        # County selector — only shown after data loads
        county_selector_placeholder = st.empty()

        if view_mode == "Full Explorer":
            st.markdown("### Chart options")
            show_90ci = st.checkbox("Show 90% prediction band", value=True)
            show_50ci = st.checkbox("Show 50% prediction band (IQR)", value=True)
            st.markdown("---")
            st.markdown("### Map filter")
            min_pop = st.slider("Min county pop (baseline)", 0, 50000, 0, step=1000)
        st.markdown("---")
        st.markdown(
            "**Data:** U.S. Census Bureau ACS 5-Year  \n"
            "**Model:** Annual cohort-component  \n"
            "**Intervals:** Monte Carlo AR(1) migration"
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
            <h1>{selected_state} Workforce Dashboard</h1>
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
    # ACS base year is stored in the projection output; fall back to 3 years
    # before the forecast start (the warm-up span in cohort_model.project()).
    base_year    = int(proj["base_year"].iloc[0]) if "base_year" in proj.columns else start_year - 3
    counties     = sorted(summary["county_name"].unique())
    n_counties   = len(counties)

    # Sector (QCEW) baseline year — may differ from the ACS cohort base_year.
    # Read it from the sector outputs when present; otherwise mirror base_year.
    sec_base_year = base_year
    if sector_data_exists(state_fips):
        _cs0, _ss0 = load_sector_data(state_fips)
        if _ss0 is not None and not _ss0.empty and "base_year" in _ss0.columns:
            sec_base_year = int(_ss0["base_year"].iloc[0])

    # Default county = largest workforce
    default_county = (
        executive_county_default(summary, selected_state)
        if view_mode == "Executive Narrative"
        else summary.loc[summary["workforce_base"].idxmax(), "county_name"]
    )
    default_idx_c  = counties.index(default_county) if default_county in counties else 0

    if view_mode == "Full Explorer":
        with county_selector_placeholder:
            selected_county = st.selectbox("County", counties, index=default_idx_c)
    else:
        selected_county = counties[default_idx_c]

    # ── Header ────────────────────────────────────────────────────────────
    st.markdown(f"""
    <div class="main-header">
        <h1>{selected_state} Workforce Dashboard &nbsp; {start_year}–{end_year}</h1>
        <p>Population &rarr; Available Workforce &rarr; Demand Pressure &rarr;
           Sector Exposure &rarr; Local Action &nbsp;·&nbsp;
           ACS 5-Year Estimates (2015–2024) &nbsp;·&nbsp; {n_counties} counties</p>
    </div>""", unsafe_allow_html=True)
    st.markdown(funnel_strip(selected_state), unsafe_allow_html=True)

    # ── Tabs ──────────────────────────────────────────────────────────────
    (tab_exec, tab_population, tab_available, tab_demand, tab_sector,
     tab_local, tab_explorer, tab_data, tab_method) = st.tabs([
        "Executive Narrative", "Population", "Available Workforce", "Demand Pressure",
        "Sector Exposure", "Local Action", "Explorer", "Data", "Methodology",
    ])

    # ═════════════════════════════════════════════════════════════════════
    # TAB 0 — EXECUTIVE NARRATIVE
    # ═════════════════════════════════════════════════════════════════════
    with tab_exec:
        from fetch_qcew import SECTOR_COLORS, SECTOR_DISPLAY_NAMES, SECTORS

        total_base = summary["workforce_base"].sum()
        total_end = summary["wf_end_p50"].sum()
        net_chg = total_end - total_base
        pct_chg = net_chg / total_base * 100 if total_base else 0
        declining = int((summary["pct_change_end"] < 0).sum())
        annual_entries = summary["annual_entries_end"].sum()
        annual_retirements = summary["annual_retirements_end"].sum()
        annual_flow = annual_entries - annual_retirements

        spotlight_county = executive_county_default(summary, selected_state)
        spotlight = summary[summary["county_name"] == spotlight_county].iloc[0]

        part_df = load_participation(state_fips)
        has_real_participation = (
            part_df is not None and not part_df.empty
            and "layers_used" in part_df.columns
            and (part_df["layers_used"].astype(str) != "ACS_only").any()
        )
        availability_headline = (
            "ACS participation layer loaded"
            if has_real_participation
            else "Availability still equals ACS population"
        )
        availability_detail = (
            "ACS labor-force-status adjustment is present for at least part of the state."
            if has_real_participation
            else "Current participation output has no LFPR layer, so this view avoids claiming effective labor force until ACS labor-force status is regenerated."
        )

        jolts_df = load_jolts()
        bls_df = load_bls_outlook()
        kdol_df = load_kdol()
        demand_sources = []
        if jolts_df is not None and not jolts_df.empty:
            demand_sources.append("JOLTS vacancy rates")
        if bls_df is not None and not bls_df.empty:
            demand_sources.append("BLS projections")
        if kdol_df is not None and not kdol_df.empty:
            demand_sources.append("KDOL UI claims")
        demand_headline = "Demand layer available" if demand_sources else "Demand pressure not yet validated"
        demand_detail = (
            ", ".join(demand_sources) + " loaded as directional context."
            if demand_sources
            else "Vacancy, claims, and BLS demand outputs are withheld here until populated and validated."
        )

        top_sector_label = "Sector layer unavailable"
        top_sector_detail = "Run the sector forecast to populate broad employment exposure."
        state_sector_df = None
        if sector_data_exists(state_fips):
            _county_sector_df, state_sector_df = load_sector_data(state_fips)
            if state_sector_df is not None and not state_sector_df.empty:
                sector_end_year = int(state_sector_df["year"].max())
                sector_end = state_sector_df[state_sector_df["year"] == sector_end_year].copy()
                sector_end["net_jobs"] = sector_end["emp_proj"] - sector_end["emp_base"]
                top_sector = sector_end.reindex(
                    sector_end["net_jobs"].abs().sort_values(ascending=False).index
                ).iloc[0]
                top_sector_label = SECTOR_DISPLAY_NAMES.get(top_sector["sector"], top_sector["sector"])
                top_sector_detail = (
                    f"{top_sector_label} changes by {top_sector['net_jobs']:+,.0f} jobs "
                    f"from {sec_base_year} to {sector_end_year}; this is exposure context, not a vacancies claim."
                )

        ipeds_df = load_ipeds(state_fips)
        commute_df = load_commute(state_fips)
        local_signals = []
        if ipeds_df is not None and not ipeds_df.empty:
            latest_ipeds = int(ipeds_df["year"].max())
            completions = ipeds_df[ipeds_df["year"] == latest_ipeds]["completions"].sum()
            local_signals.append(f"{_fmt(completions)} completions in {latest_ipeds}")
        if commute_df is not None and not commute_df.empty:
            latest_commute = int(commute_df["year"].max())
            imported = commute_df["pct_workers_imported"].mean()
            local_signals.append(f"{imported:.1f}% avg imported workers in {latest_commute}")

        st.markdown(f"### Executive Narrative — {selected_state}")
        st.markdown(
            quality_badges(
                "curated defaults",
                f"spotlight: {spotlight_county}",
                "full controls in Explorer mode",
            ),
            unsafe_allow_html=True,
        )

        exec_cols = st.columns(5)
        exec_kpis = [
            (f"{base_year} Working-Age Pop", _fmt(total_base), ""),
            (f"Projected {end_year}", _fmt(total_end), _delta_html(pct_chg)),
            ("Net Change", _fmt(net_chg), _delta_html(pct_chg)),
            ("Counties Declining", str(declining), f"of {n_counties} counties"),
            (f"Annual Net Flow ({end_year})", _fmt(annual_flow), "entries minus retirements"),
        ]
        for col, (lbl, val, dlt) in zip(exec_cols, exec_kpis):
            col.markdown(metric_card(lbl, val, dlt), unsafe_allow_html=True)

        st.markdown(
            executive_grid([
                (
                    "01 Population",
                    f"{pct_chg:+.1f}% statewide change",
                    f"{selected_state}'s working-age population moves from {_fmt(total_base)} in {base_year} "
                    f"to {_fmt(total_end)} by {end_year} in the median projection.",
                ),
                (
                    "02 Available Workforce",
                    availability_headline,
                    availability_detail,
                ),
                (
                    "03 Demand Pressure",
                    demand_headline,
                    demand_detail,
                ),
                (
                    "04 Sector Exposure",
                    top_sector_label,
                    top_sector_detail,
                ),
                (
                    "05 Local Action",
                    "Training and commute context",
                    "; ".join(local_signals) if local_signals else "IPEDS and LODES layers are not loaded for this state.",
                ),
            ]),
            unsafe_allow_html=True,
        )

        st.plotly_chart(
            ci_chart(
                state_proj,
                f"{selected_state} Working-Age Population Funnel Baseline",
                baseline=total_base,
                base_year=base_year,
            ),
            use_container_width=True,
        )

        left, right = st.columns([1.1, 0.9])
        with left:
            st.markdown(f"#### {spotlight_county} Spotlight")
            spotlight_rows = pd.DataFrame([
                {"Signal": f"{base_year} working-age population", "Value": _fmt(spotlight["workforce_base"]), "Read": "baseline"},
                {"Signal": f"{end_year} median projection", "Value": _fmt(spotlight["wf_end_p50"]), "Read": f"{spotlight['pct_change_end']:+.1f}% change"},
                {"Signal": f"Annual retirements ({end_year})", "Value": _fmt(spotlight["annual_retirements_end"]), "Read": "exit pressure"},
                {"Signal": f"Annual entries ({end_year})", "Value": _fmt(spotlight["annual_entries_end"]), "Read": "youth pipeline"},
                {"Signal": "Migration history", "Value": f"{spotlight['mig_mean_pct']:+.2f}%/yr", "Read": "ACS cohort residual"},
            ])
            st.dataframe(spotlight_rows, hide_index=True, use_container_width=True)

        with right:
            st.markdown("#### Broad Sector Exposure")
            if state_sector_df is None or state_sector_df.empty:
                st.info("Sector projections are not available for this state.")
            else:
                sector_end_year = int(state_sector_df["year"].max())
                sector_end = state_sector_df[state_sector_df["year"] == sector_end_year].copy()
                sector_end["sector_label"] = sector_end["sector"].map(
                    lambda s: SECTOR_DISPLAY_NAMES.get(s, s)
                )
                sector_end["net_jobs"] = sector_end["emp_proj"] - sector_end["emp_base"]
                sector_end = sector_end.sort_values("net_jobs", ascending=False)
                fig_exec_sector = go.Figure()
                fig_exec_sector.add_trace(go.Bar(
                    x=sector_end["sector_label"],
                    y=sector_end["net_jobs"],
                    marker_color=[
                        SECTOR_COLORS.get(sector, C_BLUE)
                        for sector in sector_end["sector"]
                    ],
                    hovertemplate="<b>%{x}</b><br>Net jobs: %{y:+,.0f}<extra></extra>",
                ))
                fig_exec_sector.update_layout(
                    title=dict(
                        text=f"Projected Employment Change by Sector, {sec_base_year} → {sector_end_year}",
                        font=dict(size=15, color=C_BLUE),
                    ),
                    xaxis=dict(tickangle=-25, title_font=dict(color="black"), tickfont=dict(color="black", size=10)),
                    yaxis=dict(title="Net Jobs", tickformat="+,", title_font=dict(color="black"), tickfont=dict(color="black")),
                    plot_bgcolor=C_LIGHT,
                    paper_bgcolor="white",
                    margin=dict(t=70, b=95, l=55, r=25),
                )
                st.plotly_chart(fig_exec_sector, use_container_width=True)

        st.markdown(
            '<div class="note-box">'
            "Executive Narrative uses curated defaults and suppresses unvalidated demand claims. "
            "Use Full Explorer mode in the sidebar for county selection, chart bands, map filters, and full drilldown."
            "</div>",
            unsafe_allow_html=True,
        )

        narrative_handoff = build_narrative_handoff(
            selected_state=selected_state,
            spotlight_county=spotlight_county,
            base_year=base_year,
            end_year=end_year,
            summary=summary,
            state_sector_df=state_sector_df,
            part_df=part_df,
            jolts_df=jolts_df,
            bls_df=bls_df,
            kdol_df=kdol_df,
            ipeds_df=ipeds_df,
            commute_df=commute_df,
        )
        methodology_handoff = build_methodology_handoff(
            selected_state=selected_state,
            base_year=base_year,
            end_year=end_year,
            part_df=part_df,
            jolts_df=jolts_df,
            bls_df=bls_df,
            kdol_df=kdol_df,
            ipeds_df=ipeds_df,
            commute_df=commute_df,
        )

        st.markdown("#### Presentation Handoff")
        export_cols = st.columns(2)
        export_cols[0].download_button(
            "Download Narrative Notes",
            data=narrative_handoff.encode("utf-8"),
            file_name=f"{selected_state.lower().replace(' ', '_')}_workforce_narrative_handoff.md",
            mime="text/markdown",
            use_container_width=True,
            key="download_narrative_handoff",
        )
        export_cols[1].download_button(
            "Download Methodology Notes",
            data=methodology_handoff.encode("utf-8"),
            file_name=f"{selected_state.lower().replace(' ', '_')}_workforce_methodology_handoff.md",
            mime="text/markdown",
            use_container_width=True,
            key="download_methodology_handoff",
        )

    # ═════════════════════════════════════════════════════════════════════
    # TAB 1 — POPULATION
    # ═════════════════════════════════════════════════════════════════════
    with tab_population:
        total_base = summary["workforce_base"].sum()
        total_end  = summary["wf_end_p50"].sum()
        net_chg    = total_end - total_base
        pct_chg    = net_chg / total_base * 100
        growing    = (summary["pct_change_end"] > 0).sum()
        declining  = (summary["pct_change_end"] <= 0).sum()
        total_ret  = summary["annual_retirements_end"].sum()

        cols = st.columns(5)
        kpis = [
            (f"{base_year} Baseline WF",      _fmt(total_base), ""),
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
                     baseline=total_base,
                     base_year=base_year),
            use_container_width=True,
        )

        map_data = summary[summary["pop_total_base"] >= min_pop].copy()
        try:
            st.plotly_chart(
                state_choropleth(map_data, state_fips, end_year, base_year=base_year),
                use_container_width=True,
            )
        except Exception:
            st.warning(
                "County map could not load — the county boundary file "
                "(`data/geo/geojson-counties-fips.json`) is missing and could not "
                "be downloaded (the network may block the source). Once that file "
                "is present the map renders offline. All other charts and tables "
                "are unaffected."
            )

        col_left, col_right = st.columns(2)
        disp_cols = ["county_name", "workforce_base", "wf_end_p50", "pct_change_end"]
        rename    = {"county_name": "County", "workforce_base": f"Baseline {base_year}",
                     "wf_end_p50": f"Projected {end_year}", "pct_change_end": "% Change"}
        with col_left:
            st.markdown(f"**Top 10 Growing Counties ({end_year} median)**")
            top = summary.nlargest(10, "pct_change_end")[disp_cols].rename(columns=rename)
            top["% Change"]          = top["% Change"].map(lambda x: f"{x:+.1f}%")
            top[f"Baseline {base_year}"]     = top[f"Baseline {base_year}"].map(_fmt)
            top[f"Projected {end_year}"] = top[f"Projected {end_year}"].map(_fmt)
            st.dataframe(top, hide_index=True, use_container_width=True)
        with col_right:
            st.markdown(f"**Top 10 Declining Counties ({end_year} median)**")
            bot = summary.nsmallest(10, "pct_change_end")[disp_cols].rename(columns=rename)
            bot["% Change"]          = bot["% Change"].map(lambda x: f"{x:+.1f}%")
            bot[f"Baseline {base_year}"]     = bot[f"Baseline {base_year}"].map(_fmt)
            bot[f"Projected {end_year}"] = bot[f"Projected {end_year}"].map(_fmt)
            st.dataframe(bot, hide_index=True, use_container_width=True)

    # ═════════════════════════════════════════════════════════════════════
    # TAB 2 — AVAILABLE WORKFORCE
    # ═════════════════════════════════════════════════════════════════════
    with tab_available:
        # ── State anchor: effective labor force vs. working-age population ──
        # This tab drills from the whole state down to one county. Lead with a
        # statewide view so the county figures below read as a zoom-in, not an
        # unexplained drop in magnitude.
        state_part_df = load_participation(state_fips)
        st.markdown(f"### Statewide Available Workforce — {selected_state}")
        if state_part_df is not None and not state_part_df.empty:
            fig_state_elf, elf_stats = state_effective_labor_chart(
                state_part_df, selected_state)
            scols = st.columns(4)
            scols[0].markdown(metric_card(
                f"Working-Age Pop ({elf_stats['year']})",
                _fmt(elf_stats["wap"]),
                "ACS 18–64 headcount",
            ), unsafe_allow_html=True)
            scols[1].markdown(metric_card(
                "After Disability Adj. (SSA)",
                _fmt(elf_stats["dadj"]),
                f'−{_fmt(elf_stats["wap"] - elf_stats["dadj"])} SSDI/SSI',
            ), unsafe_allow_html=True)
            scols[2].markdown(metric_card(
                "Effective Labor Force",
                _fmt(elf_stats["elf"]),
                "after ACS participation rate",
            ), unsafe_allow_html=True)
            scols[3].markdown(metric_card(
                "Structural Gap",
                _fmt(elf_stats["gap"]),
                f'<span class="declining">{elf_stats["gap_pct"]:.0f}% below working-age pop</span>',
            ), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            st.plotly_chart(fig_state_elf, use_container_width=True)
            st.markdown(
                '<div class="note-box">'
                "Effective labor force = working-age population, less people with federal "
                "disability determinations (SSA, where county data is available), times the "
                "ACS civilian labor-force participation rate. The gap is structural — it is "
                "who is not available to work today, before any forecast or county detail."
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            st.info(
                "The statewide effective-labor-force view needs the participation model "
                "(ACS B23001 labor-force status + SSA disability). It is not loaded for "
                f"{selected_state} yet."
            )

        st.markdown("---")
        st.markdown(f"### County Drill-Down — {selected_county}")

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
            (f"{base_year} Working-Age",       _fmt(wf_base), ""),
            (f"Projected {end_year} (Median)", _fmt(wf_end),  _delta_html(pct_end)),
            (f"80% PI ({end_year})",           f"{_fmt(wf_end_lo)} – {_fmt(wf_end_hi)}", ""),
            ("Est. Annual Migration Rate",     f"{mig_rate:+.2f}%", "historical avg"),
            (f"Annual Retirements ({end_year})", _fmt(ann_ret), f"entries: {_fmt(ann_ent)}"),
        ]
        for col, (lbl, val, dlt) in zip(ccols, ckpis):
            col.markdown(metric_card(lbl, val, dlt), unsafe_allow_html=True)

        data_flags = ["ACS 5-year period estimate", "overlapping vintages"]
        if county_sum.get("pop_total_base", 0) < 2000:
            data_flags.append("small county: wider uncertainty")
        if county_sum.get("mig_std_pct", 0) >= 3:
            data_flags.append("volatile migration history")
        st.markdown(quality_badges(*data_flags), unsafe_allow_html=True)

        # ── Participation model KPI row (shown if data exists) ────────────
        part_df = load_participation(state_fips)
        if part_df is not None and not part_df.empty:
            county_fips3 = str(county_sum["county_fips"]).zfill(3)
            cpart = part_df[
                (part_df["county_fips"].astype(str).str.zfill(3) == county_fips3) &
                (part_df["year"] == part_df["year"].max())
            ]
            if not cpart.empty:
                row = cpart.iloc[0]
                dis_rate = row.get("disability_rate_pct")
                lfpr     = row.get("lfpr_pct")
                eff_lf   = row.get("effective_labor_force")
                layers   = row.get("layers_used", "")
                st.markdown("**Effective Labor Force (Participation Model)**")
                pkpi = st.columns(4)
                pkpi[0].markdown(metric_card(
                    "Working-Age Pop (ACS)",
                    _fmt(row.get("working_age_pop")),
                    "",
                ), unsafe_allow_html=True)
                pkpi[1].markdown(metric_card(
                    "Disability Rate (SSA)",
                    f"{dis_rate:.1f}%" if dis_rate and not pd.isna(dis_rate) else "—",
                    "SSDI + SSI, 18–64",
                ), unsafe_allow_html=True)
                pkpi[2].markdown(metric_card(
                    "Labor Force Part. Rate (ACS)",
                    f"{lfpr:.1f}%" if lfpr and not pd.isna(lfpr) else "—",
                    "ACS B23001 civilian 18–64",
                ), unsafe_allow_html=True)
                wap = row.get("working_age_pop")
                gap_label = ""
                if (eff_lf is not None and not pd.isna(eff_lf)
                        and wap is not None and not pd.isna(wap) and wap > 0):
                    gap_pct = (1 - eff_lf / wap) * 100
                    gap_abs = wap - eff_lf
                    gap_label = (
                        f'<span class="declining">'
                        f'{gap_pct:.0f}% structural gap '
                        f'({_fmt(gap_abs)} fewer than working-age pop)'
                        f'</span><br>'
                    )
                pkpi[3].markdown(metric_card(
                    "Effective Labor Force",
                    _fmt(eff_lf) if eff_lf and not pd.isna(eff_lf) else "—",
                    gap_label +
                    f'<span style="font-size:0.78rem;color:{C_NEUTRAL};">{layers}</span>',
                ), unsafe_allow_html=True)
                st.markdown("<br>", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        fig_c = go.Figure()
        if show_90ci:
            fig_c.add_trace(go.Scatter(
                x=pd.concat([county_proj["year"], county_proj["year"].iloc[::-1]]),
                y=pd.concat([county_proj["p95"], county_proj["p5"].iloc[::-1]]),
                fill="toself", fillcolor="rgba(0,63,135,0.10)",
                line=dict(color="rgba(0,0,0,0)"), name="90% PI", hoverinfo="skip",
            ))
        fig_c.add_trace(go.Scatter(
            x=pd.concat([county_proj["year"], county_proj["year"].iloc[::-1]]),
            y=pd.concat([county_proj["p90"], county_proj["p10"].iloc[::-1]]),
            fill="toself", fillcolor="rgba(0,63,135,0.18)",
            line=dict(color="rgba(0,0,0,0)"), name="80% PI", hoverinfo="skip",
        ))
        if show_50ci:
            fig_c.add_trace(go.Scatter(
                x=pd.concat([county_proj["year"], county_proj["year"].iloc[::-1]]),
                y=pd.concat([county_proj["p75"], county_proj["p25"].iloc[::-1]]),
                fill="toself", fillcolor="rgba(0,63,135,0.28)",
                line=dict(color="rgba(0,0,0,0)"), name="50% PI (IQR)", hoverinfo="skip",
            ))
        fig_c.add_trace(go.Scatter(
            x=county_proj["year"], y=county_proj["p50"],
            mode="lines+markers", name="Median",
            line=dict(color=C_BLUE, width=2.5), marker=dict(size=6),
            hovertemplate="<b>%{x}</b><br>Median: %{y:,.0f}<extra></extra>",
        ))
        fig_c.add_trace(go.Scatter(
            x=[base_year], y=[wf_base], mode="markers", name=f"{base_year} Baseline",
            marker=dict(color=C_GOLD, size=12, symbol="diamond"),
            hovertemplate=f"<b>{base_year} Baseline</b><br>%{{y:,.0f}}<extra></extra>",
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
                       "Annual Retirements", "Annual Entries", f"% vs {base_year}"]
        for c in ["P10 (80% lo)", "P25 (50% lo)", "Median",
                  "P75 (50% hi)", "P90 (80% hi)", "Annual Retirements", "Annual Entries"]:
            tbl[c] = tbl[c].map(_fmt)
        tbl[f"% vs {base_year}"] = tbl[f"% vs {base_year}"].map(lambda x: f"{x:+.1f}%")
        st.dataframe(tbl, hide_index=True, use_container_width=True)

    # ═════════════════════════════════════════════════════════════════════
    # TAB 4 — SECTOR EXPOSURE
    # ═════════════════════════════════════════════════════════════════════
    with tab_sector:
        from fetch_qcew import SECTOR_COLORS, SECTOR_DISPLAY_NAMES, SECTORS

        def sector_label(sector: str) -> str:
            return SECTOR_DISPLAY_NAMES.get(sector, sector)

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

            # True all-industries total (None for states not yet regenerated
            # since this layer was added — chart falls back to focus sectors).
            total_emp_df = load_total_employment(state_fips)

            sec_start = int(county_sector_df["year"].min())
            sec_end   = int(county_sector_df["year"].max())

            # ── Pre-compute sector stats ──────────────────────────────────
            sector_stats: dict = {}
            total_jobs_2023 = 0
            total_jobs_end  = 0
            for sector in SECTORS:
                s_rows   = state_sector_df[state_sector_df["sector"] == sector]
                base     = float(s_rows["emp_base"].iloc[0]) if len(s_rows) and not pd.isna(s_rows["emp_base"].iloc[0]) else None
                end_rows = s_rows[s_rows["year"] == sec_end]
                proj_val = float(end_rows["emp_proj"].iloc[0])   if len(end_rows) else None
                ci_lo    = float(end_rows["emp_ci_lo"].iloc[0])  if len(end_rows) else None
                ci_hi    = float(end_rows["emp_ci_hi"].iloc[0])  if len(end_rows) else None
                delta    = (proj_val - base) if (base and proj_val) else None
                pct      = (delta / base * 100) if (base and delta is not None) else None
                sector_stats[sector] = dict(base=base, proj=proj_val, ci_lo=ci_lo, ci_hi=ci_hi,
                                            delta=delta, pct=pct)
                if base:
                    total_jobs_2023 += base
                if proj_val:
                    total_jobs_end  += proj_val

            total_delta = total_jobs_end - total_jobs_2023
            wf_supply_2023 = float(summary["workforce_base"].sum())
            wf_supply_end  = float(summary["wf_end_p50"].sum())
            wf_supply_pct  = (wf_supply_end - wf_supply_2023) / wf_supply_2023 * 100

            # ── Section header ────────────────────────────────────────────
            st.markdown(f"""
<div style="background:linear-gradient(135deg,{C_BLUE} 0%,#005BB5 100%);
            color:white;padding:1rem 1.5rem;border-radius:8px;margin-bottom:1rem;">
  <strong style="font-size:1.05rem;">
    {selected_state} — Sector Employment vs. Working-Age Population Context &nbsp;·&nbsp; {sec_base_year} → {sec_end}
  </strong><br>
  <span style="opacity:0.85;font-size:0.88rem;">
    Sector employment = projected jobs by broad QCEW group &nbsp;·&nbsp;
    Population context = working-age population 18–64 (ACS cohort model)
  </span>
</div>""", unsafe_allow_html=True)
            st.markdown(
                quality_badges(
                    "QCEW broad sector groups",
                    "employment, not labor demand",
                    "population context, not labor force",
                ),
                unsafe_allow_html=True,
            )

            # ── State-level summary KPI row ───────────────────────────────
            kpi_cols = st.columns(4)
            supply_arrow = "growing" if wf_supply_pct >= 0 else "declining"
            demand_arrow = "growing" if total_delta >= 0 else "declining"
            kpi_cols[0].markdown(metric_card(
                f"Working-Age Population ({base_year})",
                _fmt(wf_supply_2023),
                f'<span class="{supply_arrow}">{wf_supply_pct:+.1f}% by {sec_end}</span>',
            ), unsafe_allow_html=True)
            kpi_cols[1].markdown(metric_card(
                f"Projected Working-Age Pop. ({sec_end})",
                _fmt(wf_supply_end),
                f'<span class="{supply_arrow}">{_fmt(wf_supply_end - wf_supply_2023)} net change</span>',
            ), unsafe_allow_html=True)
            kpi_cols[2].markdown(metric_card(
                f"Total Sector Jobs ({sec_base_year})",
                _fmt(total_jobs_2023),
                "",
            ), unsafe_allow_html=True)
            kpi_cols[3].markdown(metric_card(
                f"Total Sector Jobs Projected ({sec_end})",
                _fmt(total_jobs_end),
                f'<span class="{demand_arrow}">{total_delta:+,.0f} net new jobs</span>',
            ), unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # ── Grouped bar chart: jobs today vs projected employment ─────
            st.markdown("#### Jobs Today vs. Projected Employment by Broad Sector")
            st.caption(
                f"Each sector shows {sec_base_year} actual employment alongside the projected "
                f"{sec_end} employment estimate. The label on each projected bar shows "
                "the net change in jobs, not a direct labor-supply gap."
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
                gap_sectors.append(sector_label(sector))
                gap_base.append(st_s["base"])
                gap_proj.append(st_s["proj"])
                gap_delta.append(st_s["delta"] or 0)
                gap_ci_lo.append(st_s["ci_lo"] or st_s["proj"])
                gap_ci_hi.append(st_s["ci_hi"] or st_s["proj"])
                gap_colors.append(SECTOR_COLORS[sector])

            fig_gap = go.Figure()

            # baseline-year bars (solid)
            fig_gap.add_trace(go.Bar(
                name=f"{sec_base_year} Actual Jobs",
                x=gap_sectors,
                y=gap_base,
                marker_color=[f"rgba({int(c[1:3],16)},{int(c[3:5],16)},{int(c[5:7],16)},0.8)" for c in gap_colors],
                marker_line_color=[c for c in gap_colors],
                marker_line_width=1.5,
                text=[_fmt(v) for v in gap_base],
                textposition="outside",
                textfont=dict(color="black"),
                hovertemplate="<b>%{x}</b><br>" + str(sec_base_year) + " Jobs: %{y:,.0f}<extra></extra>",
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
                textfont=dict(color="black"),
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

            fig_gap.update_layout(
                barmode="group",
                bargap=0.25,
                bargroupgap=0.08,
                title=dict(
                    text=(f"{selected_state} — Sector Employment: {sec_base_year} Actual vs. "
                          f"{sec_end} Projected Employment"),
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

            # ── Working-age population vs. total employment overlay ───────
            _has_total = total_emp_df is not None and not total_emp_df.empty
            st.markdown("#### Working-Age Population vs. Total Employment Over Time")
            if _has_total:
                st.caption(
                    "Population context (left axis) = state working-age population 18–64 "
                    "(Monte Carlo AR(1) simulation bands). "
                    "Employment context (right axis): the solid line is projected "
                    "employment across <strong>all industries</strong> (QCEW total, all ownership) — "
                    "the true measure of labor demand pressure. The dashed line is the sum "
                    "of the five WSU Tech focus sectors, shown for reference. "
                    "Diverging population/employment trends suggest pressure, but do not by "
                    "themselves measure a labor gap.",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    '<div class="note-box" style="margin-bottom:0.6rem;">'
                    "<strong>CI note:</strong> The employment shaded band is the 80% prediction "
                    "interval for the all-industries total projection. The five focus sectors are "
                    "private-only (QCEW own_code 5); the all-industries total includes government, "
                    "so the focus line is an approximate — not exact — subset of the total.</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.caption(
                    "Population context (left axis) = state working-age population 18–64 "
                    "(Monte Carlo AR(1) simulation bands). "
                    "Employment context (right axis) = sum of projected employment across the five displayed QCEW groups. "
                    "Diverging trends suggest pressure, but do not by themselves measure a labor gap."
                )
                st.markdown(
                    '<div class="note-box" style="margin-bottom:0.6rem;">'
                    "<strong>Note:</strong> This employment line covers only the five WSU Tech focus "
                    "sectors, not all industries — regenerate this state's forecast to add the "
                    "all-industries total line. "
                    "The shaded band is the <em>sum of individual sector 80% prediction intervals</em>, "
                    "not a joint confidence interval.</div>",
                    unsafe_allow_html=True,
                )

            # Focus-sector sum (the five WSU Tech sectors) — secondary reference line
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

            # Population context: prediction band + median line
            fig_svd.add_trace(go.Scatter(
                x=pd.concat([state_proj["year"], state_proj["year"].iloc[::-1]]),
                y=pd.concat([state_proj["p90"], state_proj["p10"].iloc[::-1]]),
                fill="toself", fillcolor="rgba(0,63,135,0.10)",
                line=dict(color="rgba(0,0,0,0)"),
                name="Population 80% PI", hoverinfo="skip", yaxis="y1",
                showlegend=False,
            ))
            fig_svd.add_trace(go.Scatter(
                x=state_proj["year"], y=state_proj["p50"],
                mode="lines+markers", name="Working-Age Population (18–64)",
                line=dict(color=C_BLUE, width=2.5),
                marker=dict(size=5),
                yaxis="y1",
                hovertemplate="<b>%{x}</b><br>Population: %{y:,.0f}<extra></extra>",
            ))
            # Population baseline anchor
            fig_svd.add_trace(go.Scatter(
                x=[base_year], y=[wf_supply_2023],
                mode="markers", name=f"{base_year} Population (ACS)",
                marker=dict(color=C_GOLD, size=10, symbol="diamond"),
                yaxis="y1",
                hovertemplate=f"<b>{base_year} Baseline</b><br>%{{y:,.0f}}<extra></extra>",
                showlegend=False,
            ))

            if _has_total:
                total_by_year = total_emp_df.sort_values("year")
                total_all_base = (
                    float(total_emp_df["emp_base"].iloc[0])
                    if not pd.isna(total_emp_df["emp_base"].iloc[0]) else None
                )

                # Employment band: 80% PI for the all-industries total (primary)
                fig_svd.add_trace(go.Scatter(
                    x=pd.concat([total_by_year["year"], total_by_year["year"].iloc[::-1]]),
                    y=pd.concat([total_by_year["emp_ci_hi"],
                                 total_by_year["emp_ci_lo"].iloc[::-1]]),
                    fill="toself", fillcolor="rgba(245,166,35,0.12)",
                    line=dict(color="rgba(0,0,0,0)"),
                    name="Total Employment 80% PI", hoverinfo="skip", yaxis="y2",
                    showlegend=False,
                ))
                # PRIMARY: total employment across all industries (solid)
                fig_svd.add_trace(go.Scatter(
                    x=total_by_year["year"], y=total_by_year["emp_proj"],
                    mode="lines+markers", name="Projected Total Employment (all industries)",
                    line=dict(color=C_GOLD, width=3),
                    marker=dict(size=5),
                    yaxis="y2",
                    hovertemplate="<b>%{x}</b><br>Total employment: %{y:,.0f}<extra></extra>",
                ))
                # SECONDARY: five WSU Tech focus sectors (dashed reference)
                fig_svd.add_trace(go.Scatter(
                    x=demand_by_year["year"], y=demand_by_year["emp_proj"],
                    mode="lines+markers", name="Focus-Sector Employment (5 WSU Tech sectors)",
                    line=dict(color="#B8860B", width=2, dash="dash"),
                    marker=dict(size=4),
                    yaxis="y2",
                    hovertemplate="<b>%{x}</b><br>Focus-sector employment: %{y:,.0f}<extra></extra>",
                ))
                # Total-employment baseline anchor
                if total_all_base is not None:
                    fig_svd.add_trace(go.Scatter(
                        x=[sec_base_year], y=[total_all_base],
                        mode="markers", name=f"{sec_base_year} QCEW Total Employment",
                        marker=dict(color=C_GOLD, size=10, symbol="circle",
                                    line=dict(color=C_BLUE, width=2)),
                        yaxis="y2",
                        hovertemplate=f"<b>{sec_base_year} Total Employment Baseline</b><br>%{{y:,.0f}}<extra></extra>",
                        showlegend=False,
                    ))
            else:
                # Fallback (state not yet regenerated): focus sectors only
                fig_svd.add_trace(go.Scatter(
                    x=pd.concat([demand_hi_by_year["year"],
                                 demand_lo_by_year["year"].iloc[::-1]]),
                    y=pd.concat([demand_hi_by_year["emp_ci_hi"],
                                 demand_lo_by_year["emp_ci_lo"].iloc[::-1]]),
                    fill="toself", fillcolor="rgba(245,166,35,0.12)",
                    line=dict(color="rgba(0,0,0,0)"),
                    name="Employment 80% PI", hoverinfo="skip", yaxis="y2",
                    showlegend=False,
                ))
                fig_svd.add_trace(go.Scatter(
                    x=demand_by_year["year"], y=demand_by_year["emp_proj"],
                    mode="lines+markers", name="Projected Sector Employment (5 focus sectors)",
                    line=dict(color=C_GOLD, width=2.5, dash="dash"),
                    marker=dict(size=5),
                    yaxis="y2",
                    hovertemplate="<b>%{x}</b><br>Employment: %{y:,.0f}<extra></extra>",
                ))
                fig_svd.add_trace(go.Scatter(
                    x=[sec_base_year], y=[total_jobs_2023],
                    mode="markers", name=f"{sec_base_year} QCEW Employment",
                    marker=dict(color=C_GOLD, size=10, symbol="circle",
                                line=dict(color=C_BLUE, width=2)),
                    yaxis="y2",
                    hovertemplate=f"<b>{sec_base_year} Employment Baseline</b><br>%{{y:,.0f}}<extra></extra>",
                    showlegend=False,
                ))

            fig_svd.update_layout(
                title=dict(
                    text=(f"{selected_state} — Working-Age Population vs. "
                          f"{'Total' if _has_total else 'Sector'} Employment "
                          f"{sec_start}–{sec_end}"),
                    font=dict(size=15, color=C_BLUE),
                    y=0.97, yanchor="top", x=0, xanchor="left",
                ),
                xaxis=dict(title="Year", tickmode="linear", dtick=1,
                           title_font=dict(color="black"), tickfont=dict(color="black")),
                yaxis=dict(
                    title="Working-Age Population",
                    tickformat=",", side="left",
                    title_font=dict(color=C_BLUE), tickfont=dict(color=C_BLUE),
                ),
                yaxis2=dict(
                    title=("Projected Employment (all industries)"
                           if _has_total else "Projected Sector Employment"),
                    tickformat=",", side="right", overlaying="y",
                    title_font=dict(color="#B8860B"), tickfont=dict(color="#B8860B"),
                    showgrid=False,
                ),
                legend=dict(orientation="h", yanchor="bottom", y=1.02,
                            xanchor="center", x=0.5, font=dict(color="black", size=11)),
                plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                margin=dict(t=130, b=40, l=70, r=80), hovermode="x unified",
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
                        f'{sec_base_year}: {_fmt(st_s["base"])}</span>'
                    )
                else:
                    delta_lbl = ""
                col.markdown(
                    metric_card(
                        sector_label(sector),
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
                        name=f"{sector_label(sector)} 80% PI", hoverinfo="skip", showlegend=False,
                    ))
                    fig_state_sec.add_trace(go.Scatter(
                        x=s_rows["year"], y=s_rows["emp_proj"],
                        mode="lines+markers", name=sector_label(sector),
                        line=dict(color=color, width=2.5, dash="dash"),
                        marker=dict(size=5),
                        hovertemplate=f"<b>{sector_label(sector)}</b> %{{x}}<br>Projected: %{{y:,.0f}}<extra></extra>",
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
                c_jobs_2023 = c_sec.drop_duplicates("sector")["emp_base"].dropna().sum()
                c_jobs_end  = float(c_end_rows["emp_proj"].sum()) if not c_end_rows.empty else 0
                c_jobs_delta = c_jobs_end - c_jobs_2023
                c_jobs_cls  = "growing" if c_jobs_delta >= 0 else "declining"

                c_kpi_cols[0].markdown(metric_card(
                    f"County Working-Age Pop. ({base_year})",
                    _fmt(c_wf_2023),
                    f'<span class="{c_supply_cls}">{c_wf_pct:+.1f}% by {sec_end}</span>',
                ), unsafe_allow_html=True)
                c_kpi_cols[1].markdown(metric_card(
                    f"Projected Working-Age Pop. ({sec_end})",
                    _fmt(c_wf_end),
                    f'<span class="{c_supply_cls}">{_fmt(c_wf_end - c_wf_2023)} net change</span>',
                ), unsafe_allow_html=True)
                c_kpi_cols[2].markdown(metric_card(
                    f"County Sector Jobs ({sec_base_year})",
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
                st.markdown(f"#### {selected_county} — Jobs Today vs. Projected Employment by Broad Sector")

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
                    b23 = s_row["emp_base"].iloc[0]
                    epr = float(end_row["emp_proj"].iloc[0])
                    elo = float(end_row["emp_ci_lo"].iloc[0])
                    ehi = float(end_row["emp_ci_hi"].iloc[0])
                    c_gap_sectors.append(sector_label(sector))
                    c_gap_base.append(b23 if not pd.isna(b23) else 0)
                    c_gap_proj.append(epr)
                    c_gap_delta.append(epr - (b23 if not pd.isna(b23) else 0))
                    c_gap_ci_lo.append(elo)
                    c_gap_ci_hi.append(ehi)
                    c_gap_colors.append(SECTOR_COLORS[sector])

                fig_c_gap = go.Figure()
                fig_c_gap.add_trace(go.Bar(
                    name=f"{sec_base_year} Actual Jobs",
                    x=c_gap_sectors,
                    y=c_gap_base,
                    marker_color=[f"rgba({int(c[1:3],16)},{int(c[3:5],16)},{int(c[5:7],16)},0.8)" for c in c_gap_colors],
                    marker_line_color=c_gap_colors,
                    marker_line_width=1.5,
                    text=[_fmt(v) for v in c_gap_base],
                    textposition="outside",
                    textfont=dict(color="black"),
                    hovertemplate="<b>%{x}</b><br>" + str(sec_base_year) + " Jobs: %{y:,.0f}<extra></extra>",
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
                    textfont=dict(color="black"),
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
                fig_c_gap.update_layout(
                    barmode="group",
                    bargap=0.25,
                    bargroupgap=0.08,
                    title=dict(
                        text=(f"{selected_county} — Sector Employment: {sec_base_year} Actual vs. "
                              f"{sec_end} Projected Employment"),
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

                # ── Sector-detail prediction chart ────────────────────────
                st.markdown("#### Sector Deep-Dive")
                sec_choice = st.radio(
                    "Select sector for detailed trend view",
                    SECTORS,
                    horizontal=True,
                    format_func=sector_label,
                )

                one     = c_sec[c_sec["sector"] == sec_choice].sort_values("year")
                color_c = SECTOR_COLORS[sec_choice]

                if one.empty:
                    st.warning(f"No projection data for {sec_choice} in {selected_county}.")
                else:
                    method_val = one["method"].iloc[0]
                    sig_val    = bool(one["significant"].iloc[0])
                    note_val   = one["note"].iloc[0]
                    emp_base   = one["emp_base"].iloc[0]
                    emp_end_c  = float(one[one["year"] == sec_end]["emp_proj"].iloc[0]) \
                                 if len(one[one["year"] == sec_end]) else None

                    method_labels = {
                        "option_b":          (
                            "✔ Option B — Independent County OLS Trend"
                            + (" (significant)" if sig_val else " (trend uncertain — see PI)"),
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
                        name="80% PI", hoverinfo="skip",
                    ))
                    fig_sec_c.add_trace(go.Scatter(
                        x=one["year"], y=one["emp_proj"],
                        mode="lines+markers", name="Projected",
                        line=dict(color=color_c, width=2.5, dash="dash"),
                        marker=dict(size=6),
                        hovertemplate="<b>%{x}</b><br>Projected: %{y:,.0f}<extra></extra>",
                    ))
                    if emp_base and not pd.isna(emp_base):
                        fig_sec_c.add_trace(go.Scatter(
                            x=[sec_base_year], y=[emp_base], mode="markers",
                            name=f"{sec_base_year} QCEW Baseline",
                            marker=dict(color=C_GOLD, size=12, symbol="diamond"),
                            hovertemplate=f"<b>{sec_base_year} Baseline</b><br>%{{y:,.0f}}<extra></extra>",
                        ))

                    pct_lbl = ""
                    if emp_base and emp_end_c and emp_base > 0:
                        pct_lbl = f" ({(emp_end_c - emp_base) / emp_base * 100:+.1f}% vs {sec_base_year})"

                    fig_sec_c.update_layout(
                        title=dict(
                            text=f"{selected_county} — {sector_label(sec_choice)} Employment{pct_lbl}",
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
                    st.markdown(f"**Employment: {sec_base_year} Actual vs. Projection**")
                    emp_rows = []
                    for sector in SECTORS:
                        s_row   = c_sec[c_sec["sector"] == sector]
                        end_row = s_row[s_row["year"] == sec_end]
                        if s_row.empty:
                            continue
                        emp_23  = s_row["emp_base"].iloc[0]
                        emp_e   = float(end_row["emp_proj"].iloc[0])  if len(end_row) else None
                        ci_lo   = float(end_row["emp_ci_lo"].iloc[0]) if len(end_row) else None
                        ci_hi   = float(end_row["emp_ci_hi"].iloc[0]) if len(end_row) else None
                        meth    = s_row["method"].iloc[0]
                        net_new = (emp_e - emp_23) if (emp_e and emp_23 and not pd.isna(emp_23)) else None
                        pct_chg = (net_new / emp_23 * 100) if (net_new is not None and emp_23 > 0) else None
                        emp_rows.append({
                            "Sector":             sector_label(sector),
                            f"{sec_base_year} Jobs":  _fmt(emp_23) if not pd.isna(emp_23) else "—",
                            f"Projected Jobs {sec_end}": _fmt(emp_e) if emp_e else "—",
                            "Net New Jobs":       f"{net_new:+,.0f}" if net_new is not None else "—",
                            "80% PI":             (f"{_fmt(ci_lo)} – {_fmt(ci_hi)}"
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
                            "Sector":              sector_label(sector),
                            f"{sec_start} Proj":   f"${_fmt(wage_s)}" if wage_s else "—",
                            f"{sec_end} Proj":     f"${_fmt(wage_e)}" if wage_e else "—",
                            "% Change":            f"{pct_w:+.1f}%"   if pct_w  else "—",
                        })
                    st.dataframe(pd.DataFrame(wage_rows), hide_index=True,
                                 use_container_width=True)

            # ── CBP Establishment Trends — leading indicator overlay ──────
            cbp_trends = load_cbp_estab_trends(state_fips)
            if cbp_trends is not None and not cbp_trends.empty:
                st.markdown("#### Establishment Count Trend by Sector (CBP)")
                st.caption(
                    "Census County Business Patterns establishment counts trended 2015–2022. "
                    "Firm formation precedes hiring by 12–18 months — a sector with declining "
                    "establishments is consolidating; a sector with growing establishments is expanding. "
                    "Use as a leading indicator alongside the QCEW employment trends above."
                )
                cbp_state = cbp_trends[cbp_trends["state_fips"] == state_fips].copy()
                if not cbp_state.empty:
                    cbp_summary = (
                        cbp_state.groupby("sector", as_index=False)
                        .agg(
                            counties_covered=("county_fips", "nunique"),
                            median_pct_chg=("estab_pct_chg", "median"),
                            total_estab_latest=("estab_latest", "sum"),
                            avg_annual_slope=("estab_slope", "mean"),
                        )
                    )
                    cbp_summary = cbp_summary[cbp_summary["sector"].isin(SECTORS)]

                    cbp_kpis = st.columns(len(SECTORS))
                    for col, sector in zip(cbp_kpis, SECTORS):
                        s_row = cbp_summary[cbp_summary["sector"] == sector]
                        if s_row.empty:
                            col.markdown(metric_card(
                                sector_label(sector), "—",
                                "no establishment data",
                            ), unsafe_allow_html=True)
                            continue
                        latest_count = int(s_row["total_estab_latest"].iloc[0])
                        pct_chg      = float(s_row["median_pct_chg"].iloc[0])
                        annual       = float(s_row["avg_annual_slope"].iloc[0])
                        n_counties   = int(s_row["counties_covered"].iloc[0])
                        chg_cls = "growing" if pct_chg >= 0 else "declining"
                        col.markdown(metric_card(
                            sector_label(sector),
                            _fmt(latest_count),
                            (
                                f'<span class="{chg_cls}">{pct_chg:+.1f}% (2015–2022)</span><br>'
                                f'<span style="font-size:0.78rem;color:{C_NEUTRAL};">'
                                f'{annual:+.1f}/yr · {n_counties} of 105 counties</span>'
                            ),
                        ), unsafe_allow_html=True)

                    st.markdown(
                        '<div class="note-box" style="margin-top:0.6rem;">'
                        "Per-sector median % change across counties (not a state-level establishment count). "
                        "Manufacturing under-coverage reflects NAICS 31–33 returning no rows for rural KS counties "
                        "with zero manufacturing establishments — a real data limitation in CBP."
                        "</div>",
                        unsafe_allow_html=True,
                    )

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
    # TAB 5 — LOCAL ACTION: TRAINING PIPELINE
    # ═════════════════════════════════════════════════════════════════════
    with tab_local:
        ipeds_df = load_ipeds(state_fips)
        if ipeds_df is None:
            st.markdown(
                '<div class="note-box">'
                "No IPEDS training pipeline data available for this state.<br>"
                "Run the forecast with <code>--ipeds</code> to fetch NCES completions data."
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            from fetch_qcew import SECTOR_COLORS, SECTOR_DISPLAY_NAMES, SECTORS

            def sector_label(s: str) -> str:
                return SECTOR_DISPLAY_NAMES.get(s, s)

            # State-level aggregation by sector and year
            state_ipeds = (
                ipeds_df.groupby(["year", "sector"], as_index=False)["completions"].sum()
            )
            latest_year = int(state_ipeds["year"].max())
            latest = state_ipeds[state_ipeds["year"] == latest_year]

            st.markdown(f"""
<div style="background:linear-gradient(135deg,{C_BLUE} 0%,#005BB5 100%);
            color:white;padding:1rem 1.5rem;border-radius:8px;margin-bottom:1rem;">
  <strong style="font-size:1.05rem;">
    {selected_state} — Training Pipeline (IPEDS Completions by Sector) · {latest_year}
  </strong><br>
  <span style="opacity:0.85;font-size:0.88rem;">
    Source: NCES IPEDS completions · CIP-to-sector mapping · primary degree/certificate programs only
  </span>
</div>""", unsafe_allow_html=True)

            # KPI: total completions by sector (latest year)
            total_comp = int(latest["completions"].sum())
            kpi_cols = st.columns(min(len(SECTORS) + 1, 6))
            kpi_cols[0].markdown(metric_card(
                f"Total Completions ({latest_year})",
                _fmt(total_comp),
                f"{state_ipeds['year'].nunique()} years of data",
            ), unsafe_allow_html=True)
            for col, sector in zip(kpi_cols[1:], SECTORS):
                row_s = latest[latest["sector"] == sector]
                comp  = int(row_s["completions"].sum()) if not row_s.empty else 0
                col.markdown(metric_card(
                    sector_label(sector),
                    _fmt(comp),
                    "completions",
                ), unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # Time series: completions by sector
            st.markdown("#### Completions by Sector Over Time")
            fig_pipe = go.Figure()
            for sector in SECTORS:
                color = SECTOR_COLORS[sector]
                sub = state_ipeds[state_ipeds["sector"] == sector].sort_values("year")
                if sub.empty:
                    continue
                fig_pipe.add_trace(go.Scatter(
                    x=sub["year"], y=sub["completions"],
                    mode="lines+markers", name=sector_label(sector),
                    line=dict(color=color, width=2.5),
                    marker=dict(size=6),
                    hovertemplate=f"<b>{sector_label(sector)}</b> %{{x}}<br>Completions: %{{y:,.0f}}<extra></extra>",
                ))
            fig_pipe.update_layout(
                title=dict(
                    text=f"{selected_state} — IPEDS Completions by Workforce Sector",
                    font=dict(size=15, color=C_BLUE),
                ),
                xaxis=dict(title="Year", tickmode="linear", dtick=1,
                           title_font=dict(color="black"), tickfont=dict(color="black")),
                yaxis=dict(title="Annual Completions", tickformat=",",
                           title_font=dict(color="black"), tickfont=dict(color="black")),
                legend=dict(orientation="h", yanchor="bottom", y=1.02,
                            xanchor="right", x=1, font=dict(color="black")),
                plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                margin=dict(t=80, b=40, l=60, r=30), hovermode="x unified",
            )
            st.plotly_chart(fig_pipe, use_container_width=True)

            # County-level breakdown for selected county
            st.markdown(f"#### {selected_county} — Completions by Sector")
            county_fips3_c = str(county_sum["county_fips"]).zfill(3)
            county_ipeds = ipeds_df[
                ipeds_df["county_fips"].astype(str).str.zfill(3) == county_fips3_c
            ]
            if county_ipeds.empty:
                st.info("No IPEDS completions data mapped to this county (no postsecondary institutions).")
            else:
                c_pipe = (
                    county_ipeds.groupby(["year", "sector"], as_index=False)["completions"].sum()
                )
                fig_cpipe = go.Figure()
                for sector in SECTORS:
                    color = SECTOR_COLORS[sector]
                    sub = c_pipe[c_pipe["sector"] == sector].sort_values("year")
                    if sub.empty:
                        continue
                    fig_cpipe.add_trace(go.Bar(
                        x=sub["year"], y=sub["completions"],
                        name=sector_label(sector),
                        marker_color=color,
                        hovertemplate=f"<b>{sector_label(sector)}</b> %{{x}}<br>%{{y:,.0f}} completions<extra></extra>",
                    ))
                fig_cpipe.update_layout(
                    barmode="stack",
                    title=dict(text=f"{selected_county} — Completions by Sector",
                               font=dict(size=15, color=C_BLUE)),
                    xaxis=dict(title="Year", tickmode="linear", dtick=1,
                               title_font=dict(color="black"), tickfont=dict(color="black")),
                    yaxis=dict(title="Annual Completions", tickformat=",",
                               title_font=dict(color="black"), tickfont=dict(color="black")),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                xanchor="right", x=1, font=dict(color="black")),
                    plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                    margin=dict(t=80, b=40, l=60, r=30),
                )
                st.plotly_chart(fig_cpipe, use_container_width=True)

            st.markdown(
                '<div class="note-box">'
                "Completions count award recipients (degrees, certificates, diplomas) in CIP programs "
                "mapped to the five dashboard sectors. One graduate may appear in multiple programs. "
                "Completions are a supply-side proxy — they do not directly measure job placement or "
                "whether graduates remain in-state.</div>",
                unsafe_allow_html=True,
            )

    # ═════════════════════════════════════════════════════════════════════
    # TAB 5 — LOCAL ACTION: COMMUTE FLOWS
    # ═════════════════════════════════════════════════════════════════════
    with tab_local:
        commute_df = load_commute(state_fips)
        if commute_df is None:
            st.markdown(
                '<div class="note-box">'
                "No LODES commute flow data available for this state.<br>"
                "Run the forecast with <code>--lodes</code> to fetch Census LEHD origin-destination data."
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            snap_year = int(commute_df["year"].max()) if "year" in commute_df.columns else "latest"

            st.markdown(f"""
<div style="background:linear-gradient(135deg,{C_BLUE} 0%,#005BB5 100%);
            color:white;padding:1rem 1.5rem;border-radius:8px;margin-bottom:1rem;">
  <strong style="font-size:1.05rem;">
    {selected_state} — Commute Flows (LODES) · {snap_year}
  </strong><br>
  <span style="opacity:0.85;font-size:0.88rem;">
    Share of county jobs filled by local residents vs. in-commuters · Census LEHD OD Main file
  </span>
</div>""", unsafe_allow_html=True)

            # State KPIs
            avg_local  = commute_df["pct_workers_live_in_county"].mean()
            avg_import = commute_df["pct_workers_imported"].mean()
            n_counties = commute_df["county_fips"].nunique()
            ckpis = st.columns(3)
            ckpis[0].markdown(metric_card(
                "Counties with Data", str(n_counties), f"{snap_year} snapshot",
            ), unsafe_allow_html=True)
            ckpis[1].markdown(metric_card(
                "Avg Local Worker Share", f"{avg_local:.1f}%",
                "workers who live in the county",
            ), unsafe_allow_html=True)
            ckpis[2].markdown(metric_card(
                "Avg In-Commuter Share", f"{avg_import:.1f}%",
                "workers commuting from outside",
            ), unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # Scatter: local % vs total jobs
            st.markdown("#### Local Worker Share vs. Total Jobs at Worksite")
            st.caption("Counties with higher local share are more self-sufficient; "
                       "those with high in-commuter share may signal labor import dependency.")

            commute_plot = commute_df.copy()
            # Attach county names from summary
            name_map = summary.set_index(
                summary["county_fips"].astype(str).str.zfill(3)
            )["county_name"].to_dict()
            commute_plot["county_name"] = commute_plot["county_fips"].astype(str).str.zfill(3).map(name_map)

            fig_comm = go.Figure()
            fig_comm.add_trace(go.Scatter(
                x=commute_plot["total_jobs_at_worksite"],
                y=commute_plot["pct_workers_live_in_county"],
                mode="markers",
                text=commute_plot["county_name"],
                marker=dict(
                    size=10,
                    color=commute_plot["pct_workers_live_in_county"],
                    colorscale=[[0, C_RED], [0.5, C_GOLD], [1, C_GREEN]],
                    showscale=True,
                    colorbar=dict(title="Local %", tickfont=dict(color="black")),
                ),
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "Total jobs: %{x:,.0f}<br>"
                    "Local workers: %{y:.1f}%<extra></extra>"
                ),
                name="County",
            ))
            # Highlight selected county
            sel_row = commute_plot[
                commute_plot["county_fips"].astype(str).str.zfill(3) ==
                str(county_sum["county_fips"]).zfill(3)
            ]
            if not sel_row.empty:
                fig_comm.add_trace(go.Scatter(
                    x=sel_row["total_jobs_at_worksite"],
                    y=sel_row["pct_workers_live_in_county"],
                    mode="markers",
                    marker=dict(size=14, color=C_BLUE, symbol="star"),
                    name=selected_county,
                    hovertemplate=(
                        f"<b>{selected_county}</b><br>"
                        "Total jobs: %{x:,.0f}<br>"
                        "Local workers: %{y:.1f}%<extra></extra>"
                    ),
                ))
            fig_comm.update_layout(
                title=dict(text=f"{selected_state} — Local Worker Share vs. Total County Jobs ({snap_year})",
                           font=dict(size=15, color=C_BLUE)),
                xaxis=dict(title="Total Jobs at Worksite", tickformat=",",
                           title_font=dict(color="black"), tickfont=dict(color="black")),
                yaxis=dict(title="% Workers Living in County", range=[0, 105],
                           title_font=dict(color="black"), tickfont=dict(color="black")),
                plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                margin=dict(t=80, b=40, l=60, r=30),
            )
            st.plotly_chart(fig_comm, use_container_width=True)

            # County detail table
            st.markdown("#### County Commute Detail")
            tbl_comm = commute_df[[
                "county_fips", "total_jobs_at_worksite",
                "jobs_workers_live_in_county", "jobs_workers_imported",
                "pct_workers_live_in_county", "pct_workers_imported",
                "top_feeder_counties",
            ]].copy()
            tbl_comm["county_fips_padded"] = tbl_comm["county_fips"].astype(str).str.zfill(3)
            tbl_comm["County"] = tbl_comm["county_fips_padded"].map(name_map).fillna(tbl_comm["county_fips_padded"])
            tbl_comm = tbl_comm.rename(columns={
                "total_jobs_at_worksite":       "Total Jobs",
                "jobs_workers_live_in_county":  "Local Worker Jobs",
                "jobs_workers_imported":        "In-Commuter Jobs",
                "pct_workers_live_in_county":   "% Local",
                "pct_workers_imported":         "% In-Commuters",
                "top_feeder_counties":          "Top Feeder Counties",
            })
            for col in ["Total Jobs", "Local Worker Jobs", "In-Commuter Jobs"]:
                tbl_comm[col] = tbl_comm[col].map(_fmt)
            for col in ["% Local", "% In-Commuters"]:
                tbl_comm[col] = tbl_comm[col].map(lambda x: f"{x:.1f}%")
            tbl_comm = tbl_comm[["County", "Total Jobs", "Local Worker Jobs",
                                  "In-Commuter Jobs", "% Local", "% In-Commuters",
                                  "Top Feeder Counties"]].sort_values("County")
            st.dataframe(tbl_comm, hide_index=True, use_container_width=True, height=400)

            st.markdown(
                '<div class="note-box">'
                "LODES Origin-Destination Main file captures all primary jobs (one job per worker). "
                "Top feeder counties are home counties of in-commuters, by job count. "
                "LODES data lags 2–3 years; this snapshot reflects the most recent available year."
                "</div>",
                unsafe_allow_html=True,
            )

    # ═════════════════════════════════════════════════════════════════════
    # TAB 3 — DEMAND PRESSURE: OUTLOOK
    # ═════════════════════════════════════════════════════════════════════
    with tab_demand:
        jolts_df  = load_jolts()
        bls_df    = load_bls_outlook()
        has_jolts = jolts_df is not None and not jolts_df.empty
        has_bls   = bls_df is not None and not bls_df.empty

        if not has_jolts and not has_bls:
            st.markdown(
                '<div class="note-box">'
                "No demand outlook data available.<br>"
                "Run the forecast with <code>--jolts</code> (BLS vacancy rates) "
                "and/or <code>--bls-proj</code> (BLS national employment projections) "
                "to populate this tab."
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            from fetch_qcew import SECTOR_COLORS, SECTOR_DISPLAY_NAMES, SECTORS

            def sector_label(s: str) -> str:
                return SECTOR_DISPLAY_NAMES.get(s, s)

            st.markdown(f"""
<div style="background:linear-gradient(135deg,{C_BLUE} 0%,#005BB5 100%);
            color:white;padding:1rem 1.5rem;border-radius:8px;margin-bottom:1rem;">
  <strong style="font-size:1.05rem;">Demand Outlook — JOLTS Vacancy Rates &amp; BLS Employment Projections</strong><br>
  <span style="opacity:0.85;font-size:0.88rem;">
    National demand signals · Vacancy rate trend · Projected employment change
  </span>
</div>""", unsafe_allow_html=True)

            # ── JOLTS vacancy rate chart ──────────────────────────────────
            if has_jolts:
                st.markdown("#### BLS JOLTS — Annual Vacancy Rate by Sector (National)")
                st.markdown(
                    '<div class="note-box" style="margin-bottom:0.6rem;">'
                    "JOLTS data is national (not state-level). Vacancy rate = job openings as % of total "
                    "employment + openings. Use as a national demand signal alongside the state employment model."
                    "</div>",
                    unsafe_allow_html=True,
                )

                # Latest year KPI row
                latest_j = int(jolts_df["year"].max())
                latest_j_row = jolts_df[jolts_df["year"] == latest_j]
                jkpi_cols = st.columns(len(SECTORS))
                for col, sector in zip(jkpi_cols, SECTORS):
                    row_j = latest_j_row[latest_j_row["sector"] == sector]
                    vac   = float(row_j["vacancy_rate_pct"].iloc[0]) if not row_j.empty and "vacancy_rate_pct" in row_j.columns else None
                    slope = float(row_j["vacancy_rate_trend_slope"].iloc[0]) if not row_j.empty and "vacancy_rate_trend_slope" in row_j.columns else None
                    if vac is not None and not pd.isna(vac):
                        trend_lbl = ""
                        if slope is not None and not pd.isna(slope):
                            trend_cls = "growing" if slope > 0 else "declining"
                            trend_lbl = f'<span class="{trend_cls}">{slope:+.2f}%/yr trend</span>'
                        col.markdown(metric_card(
                            sector_label(sector),
                            f"{vac:.1f}%",
                            trend_lbl or "vacancy rate",
                        ), unsafe_allow_html=True)
                    else:
                        col.markdown(metric_card(sector_label(sector), "—", "no data"), unsafe_allow_html=True)

                st.markdown("<br>", unsafe_allow_html=True)

                fig_jolts = go.Figure()
                for sector in SECTORS:
                    color = SECTOR_COLORS[sector]
                    sub = jolts_df[jolts_df["sector"] == sector].sort_values("year")
                    if sub.empty or "vacancy_rate_pct" not in sub.columns:
                        continue
                    fig_jolts.add_trace(go.Scatter(
                        x=sub["year"], y=sub["vacancy_rate_pct"],
                        mode="lines+markers", name=sector_label(sector),
                        line=dict(color=color, width=2.5),
                        marker=dict(size=6),
                        hovertemplate=(
                            f"<b>{sector_label(sector)}</b> %{{x}}<br>"
                            "Vacancy rate: %{y:.2f}%<extra></extra>"
                        ),
                    ))
                fig_jolts.update_layout(
                    title=dict(
                        text="National Job Vacancy Rate by Sector (JOLTS annual avg)",
                        font=dict(size=15, color=C_BLUE),
                    ),
                    xaxis=dict(title="Year", tickmode="linear", dtick=1,
                               title_font=dict(color="black"), tickfont=dict(color="black")),
                    yaxis=dict(title="Vacancy Rate (%)", tickformat=".1f",
                               title_font=dict(color="black"), tickfont=dict(color="black")),
                    legend=dict(orientation="v", yanchor="top", y=1,
                                xanchor="left", x=1.02, font=dict(color="black")),
                    plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                    margin=dict(t=50, b=40, l=60, r=170), hovermode="x unified",
                )
                st.plotly_chart(fig_jolts, use_container_width=True)

            # ── BLS employment projections ────────────────────────────────
            if has_bls:
                st.markdown("#### BLS Employment Projections — Sector Demand Outlook")
                st.caption(
                    "BLS national (and KS state where available) employment projections, "
                    "aggregated to dashboard sectors. Display layer only — does not affect the cohort model."
                )
                for source in bls_df["projection_source"].unique():
                    src_df = bls_df[bls_df["projection_source"] == source]
                    by = int(src_df["base_year"].iloc[0])
                    py = int(src_df["proj_year"].iloc[0])
                    st.markdown(f"**{source.replace('_', ' ')} — {by} → {py}**")
                    bls_rows = []
                    for sector in SECTORS:
                        row_b = src_df[src_df["sector"] == sector]
                        if row_b.empty:
                            continue
                        base_e = row_b["base_emp_total"].iloc[0]
                        proj_e = row_b["proj_emp_total"].iloc[0]
                        chg    = row_b["emp_change_pct"].iloc[0]
                        bls_rows.append({
                            "Sector":           sector_label(sector),
                            f"Jobs ({by})":     _fmt(base_e) if base_e and not pd.isna(base_e) else "—",
                            f"Jobs ({py})":     _fmt(proj_e) if proj_e and not pd.isna(proj_e) else "—",
                            "Change %":         f"{chg:+.1f}%" if chg and not pd.isna(chg) else "—",
                        })
                    if bls_rows:
                        st.dataframe(pd.DataFrame(bls_rows), hide_index=True, use_container_width=True)

            # ── KS State vs BLS National — sector outlook comparison ──────
            ks_sector_df = load_ks_occ_by_sector(state_fips)
            if has_bls and ks_sector_df is not None and not ks_sector_df.empty:
                st.markdown("#### KS State (KDOL) vs. BLS National — Sector Outlook Comparison")
                st.caption(
                    "Side-by-side projected % change in sector employment, comparing the KS-specific "
                    "KDOL occupational projection (2022–2032) against the BLS national projection "
                    "(2024–2034). Divergence highlights where KS demand differs from national trends."
                )
                bls_sector_map = dict(zip(bls_df["sector"], bls_df["emp_change_pct"]))
                ks_sector_map  = dict(zip(ks_sector_df["sector"], ks_sector_df["emp_change_pct"]))
                comp_sectors = [s for s in SECTORS if s in bls_sector_map or s in ks_sector_map]
                fig_comp = go.Figure()
                fig_comp.add_trace(go.Bar(
                    name="BLS National (2024–2034)",
                    x=[sector_label(s) for s in comp_sectors],
                    y=[bls_sector_map.get(s) for s in comp_sectors],
                    marker_color=C_BLUE,
                    text=[f"{bls_sector_map.get(s):+.1f}%" if bls_sector_map.get(s) is not None else ""
                          for s in comp_sectors],
                    textposition="outside",
                    textfont=dict(color="black"),
                    hovertemplate="<b>%{x}</b><br>BLS National: %{y:+.1f}%<extra></extra>",
                ))
                fig_comp.add_trace(go.Bar(
                    name="KS State KDOL (2022–2032)",
                    x=[sector_label(s) for s in comp_sectors],
                    y=[ks_sector_map.get(s) for s in comp_sectors],
                    marker_color=C_GOLD,
                    text=[f"{ks_sector_map.get(s):+.1f}%" if ks_sector_map.get(s) is not None else ""
                          for s in comp_sectors],
                    textposition="outside",
                    textfont=dict(color="black"),
                    hovertemplate="<b>%{x}</b><br>KS State: %{y:+.1f}%<extra></extra>",
                ))
                fig_comp.update_layout(
                    barmode="group", bargap=0.25,
                    title=dict(text="Projected Employment Change % by Sector",
                               font=dict(size=15, color=C_BLUE)),
                    xaxis=dict(title="Sector", title_font=dict(color="black"),
                               tickfont=dict(color="black")),
                    yaxis=dict(title="Projected % Change", tickformat=".1f",
                               title_font=dict(color="black"), tickfont=dict(color="black"),
                               zeroline=True, zerolinecolor=C_NEUTRAL),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                xanchor="right", x=1, font=dict(color="black")),
                    plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                    margin=dict(t=80, b=40, l=60, r=30),
                )
                st.plotly_chart(fig_comp, use_container_width=True)

            # ── KS In-Demand Occupations (KDOL Workforce Innovation Board) ─
            in_demand_df = load_ks_occ_in_demand(state_fips)
            if in_demand_df is not None and not in_demand_df.empty:
                st.markdown("#### Kansas In-Demand Occupations — Top by Annual Openings")
                st.caption(
                    "Occupations flagged in-demand by the Kansas Workforce Innovation Board, "
                    "ranked by projected annual openings (2022–2032). Directly relevant for "
                    "WSU Tech program design priorities."
                )
                top_n = 25
                top_df = in_demand_df.head(top_n).copy()
                top_df["Sector"] = top_df["sector"].fillna("(other)").map(
                    lambda s: SECTOR_DISPLAY_NAMES.get(s, s) if s in SECTORS else s
                )
                display = pd.DataFrame({
                    "Occupation":          top_df["occ_title"],
                    "Sector":              top_df["Sector"],
                    "Annual Openings":     top_df["annual_openings"].map(_fmt),
                    "Current Employment":  top_df["base_emp"].map(_fmt),
                    "% Change 2022–2032":  top_df["pct_change"].map(lambda v: f"{v:+.1f}%"),
                })
                st.dataframe(display, hide_index=True, use_container_width=True)
                st.caption(
                    f"Showing top {top_n} of {len(in_demand_df)} in-demand occupations. "
                    "Source: KDOL LMIS occupational projections."
                )

    # ═════════════════════════════════════════════════════════════════════
    # TAB 3 — DEMAND PRESSURE: KS CURRENT LABOR MARKET PULSE (KDOL labor force)
    # ═════════════════════════════════════════════════════════════════════
    with tab_demand:
        kdol_lf_state = load_kdol_labforce_state(state_fips)
        kdol_lf_recent = load_kdol_labforce_county_recent(state_fips)

        if state_fips != "20":
            st.info(
                "The KS Current Labor Market Pulse uses KDOL LMIS data, "
                "which is only available for Kansas."
            )
        elif kdol_lf_state is None or kdol_lf_state.empty:
            st.markdown(
                '<div class="note-box">'
                "No KDOL labor force data available.<br>"
                "Place the KDOL LMIS labor force export at "
                "<code>data/kdol_cache/labforce__99999999.xls</code> "
                "and run <code>python scripts/parse_manual_kdol_labforce.py</code>."
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            # Latest monthly state-level snapshot
            state_mo = kdol_lf_state[kdol_lf_state["month"].notna()].copy()
            state_mo["_period"] = state_mo["Periodyear"].astype("Int64") * 100 + state_mo["month"].astype("Int64")
            state_mo = state_mo.sort_values("_period")

            latest = state_mo.iloc[-1]
            latest_yr = int(latest["Periodyear"])
            latest_mo = int(latest["month"])
            month_name = pd.Timestamp(year=latest_yr, month=latest_mo, day=1).strftime("%B %Y")

            # Year-ago comparison for delta
            yr_ago_period = (latest_yr - 1) * 100 + latest_mo
            yr_ago = state_mo[state_mo["_period"] == yr_ago_period]
            yr_ago_unemprate = float(yr_ago["Unemprate"].iloc[0]) if not yr_ago.empty else None
            yr_ago_lfpr      = float(yr_ago["Clfprate"].iloc[0])   if not yr_ago.empty else None

            cur_unemprate = float(latest["Unemprate"])
            cur_lfpr      = float(latest["Clfprate"])
            cur_lf        = float(latest["Laborforce"])
            cur_emp       = float(latest["Emplab"])

            st.markdown(f"""
<div style="background:linear-gradient(135deg,{C_BLUE} 0%,#005BB5 100%);
            color:white;padding:1rem 1.5rem;border-radius:8px;margin-bottom:1rem;">
  <strong style="font-size:1.05rem;">
    Kansas Current Labor Market Pulse — {month_name}
  </strong><br>
  <span style="opacity:0.85;font-size:0.88rem;">
    Live state-level labor force statistics from KDOL LMIS · monthly through latest available period ·
    more current than BLS LAUS (annual, ~6 wk lag).
  </span>
</div>""", unsafe_allow_html=True)

            pulse_cols = st.columns(4)

            def _delta_pct_pts(cur: float, prior: float | None, *, lower_is_better: bool) -> str:
                if prior is None or pd.isna(prior):
                    return "vs 1yr ago: no data"
                delta = cur - prior
                if abs(delta) < 0.05:
                    return f"vs 1yr ago: ±0.0 pp"
                cls = (
                    "growing" if (delta < 0 and lower_is_better) or (delta > 0 and not lower_is_better)
                    else "declining"
                )
                return f'<span class="{cls}">vs 1yr ago: {delta:+.1f} pp</span>'

            pulse_cols[0].markdown(metric_card(
                "Labor Force",
                _fmt(cur_lf),
                f"Employed: {_fmt(cur_emp)}",
            ), unsafe_allow_html=True)
            pulse_cols[1].markdown(metric_card(
                "Unemployment Rate",
                f"{cur_unemprate:.1f}%",
                _delta_pct_pts(cur_unemprate, yr_ago_unemprate, lower_is_better=True),
            ), unsafe_allow_html=True)
            pulse_cols[2].markdown(metric_card(
                "Labor Force Participation",
                f"{cur_lfpr:.1f}%",
                _delta_pct_pts(cur_lfpr, yr_ago_lfpr, lower_is_better=False),
            ), unsafe_allow_html=True)
            pulse_cols[3].markdown(metric_card(
                "Employment / Population",
                f"{float(latest['Emppopratio']):.1f}%",
                f"{int(latest['Unemp']):,} unemployed",
            ), unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # 24-month trend chart: unemployment rate + LFPR
            recent = state_mo.tail(24).copy()
            recent["period_label"] = (
                recent["Periodyear"].astype(int).astype(str) + "-" +
                recent["month"].astype(int).astype(str).str.zfill(2)
            )

            from plotly.subplots import make_subplots
            fig_pulse = make_subplots(specs=[[{"secondary_y": True}]])
            fig_pulse.add_trace(go.Scatter(
                x=recent["period_label"], y=recent["Unemprate"],
                mode="lines+markers", name="Unemployment Rate (%)",
                line=dict(color=C_RED, width=2.5), marker=dict(size=5),
                hovertemplate="<b>%{x}</b><br>Unemp Rate: %{y:.1f}%<extra></extra>",
            ), secondary_y=False)
            fig_pulse.add_trace(go.Scatter(
                x=recent["period_label"], y=recent["Clfprate"],
                mode="lines+markers", name="LFPR (%)",
                line=dict(color=C_BLUE, width=2.5, dash="dot"), marker=dict(size=5),
                hovertemplate="<b>%{x}</b><br>LFPR: %{y:.1f}%<extra></extra>",
            ), secondary_y=True)
            fig_pulse.update_layout(
                title=dict(text="Kansas Unemployment Rate &amp; LFPR — Last 24 Months",
                           font=dict(size=15, color=C_BLUE)),
                xaxis=dict(title="Month", tickangle=-45,
                           title_font=dict(color="black"),
                           tickfont=dict(color="black", size=9)),
                legend=dict(orientation="h", yanchor="bottom", y=1.02,
                            xanchor="right", x=1, font=dict(color="black")),
                plot_bgcolor=C_LIGHT, paper_bgcolor="white",
                margin=dict(t=80, b=80, l=60, r=60), hovermode="x unified",
            )
            fig_pulse.update_yaxes(
                title_text="Unemployment Rate (%)", tickformat=".1f",
                color="black", secondary_y=False,
            )
            fig_pulse.update_yaxes(
                title_text="LFPR (%)", tickformat=".1f",
                color="black", secondary_y=True,
            )
            st.plotly_chart(fig_pulse, use_container_width=True)

            # County-level latest snapshot table
            if kdol_lf_recent is not None and not kdol_lf_recent.empty:
                latest_county_period = kdol_lf_recent.assign(
                    _p=lambda d: d["Periodyear"].astype("Int64") * 100 + d["month"].astype("Int64")
                ).query("_p == _p.max()")
                if not latest_county_period.empty:
                    st.markdown(f"#### County Snapshot — {month_name}")
                    st.caption(
                        "Latest available month per county. Sorted by unemployment rate (highest first). "
                        "County data sometimes lags state by 1 month due to BLS LAUS embargo windows."
                    )
                    county_view = latest_county_period[[
                        "Areaname", "Laborforce", "Emplab", "Unemp", "Unemprate", "Clfprate",
                    ]].copy()
                    county_view = county_view.sort_values("Unemprate", ascending=False)
                    county_view.columns = ["County", "Labor Force", "Employed",
                                            "Unemployed", "Unemp Rate (%)", "LFPR (%)"]
                    for c in ["Labor Force", "Employed", "Unemployed"]:
                        county_view[c] = county_view[c].map(_fmt)
                    for c in ["Unemp Rate (%)", "LFPR (%)"]:
                        county_view[c] = county_view[c].map(lambda v: f"{v:.1f}")
                    st.dataframe(county_view, hide_index=True, use_container_width=True,
                                 height=420)

            st.markdown(
                '<div class="note-box">'
                "Source: KDOL LMIS labor force statistics (Local Area Unemployment Statistics "
                "equivalent). Reported monthly, not seasonally adjusted. Replaces the prior KDOL UI-claims "
                "panel — UI claims by NAICS are not publicly available."
                "</div>",
                unsafe_allow_html=True,
            )

    # ═════════════════════════════════════════════════════════════════════
    # TAB 6 — EXPLORER
    # ═════════════════════════════════════════════════════════════════════
    with tab_explorer:
        county_proj = proj[proj["county_name"] == selected_county].sort_values("year")
        county_sum = summary[summary["county_name"] == selected_county].iloc[0]
        county_fips3 = str(county_sum["county_fips"]).zfill(3)

        st.markdown(f"### {selected_county} County Drilldown")
        st.markdown(
            quality_badges(
                "selected county",
                "population, training, commute, and sector context",
                "use source tabs for full charts",
            ),
            unsafe_allow_html=True,
        )

        explorer_cols = st.columns(4)
        explorer_cols[0].markdown(metric_card(
            f"Working-Age Pop ({base_year})",
            _fmt(county_sum["workforce_base"]),
            "",
        ), unsafe_allow_html=True)
        explorer_cols[1].markdown(metric_card(
            f"Projected Pop ({end_year})",
            _fmt(county_sum["wf_end_p50"]),
            _delta_html(county_sum["pct_change_end"]),
        ), unsafe_allow_html=True)
        explorer_cols[2].markdown(metric_card(
            f"Annual Entries ({end_year})",
            _fmt(county_sum["annual_entries_end"]),
            "youth aging into 18-24",
        ), unsafe_allow_html=True)
        explorer_cols[3].markdown(metric_card(
            f"Annual Retirements ({end_year})",
            _fmt(county_sum["annual_retirements_end"]),
            "60-64 aging into 65+",
        ), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        left, right = st.columns(2)
        with left:
            st.markdown("#### Sector Exposure")
            if sector_data_exists(state_fips):
                from fetch_qcew import SECTOR_DISPLAY_NAMES, SECTORS

                county_sector_df, _state_sector_df = load_sector_data(state_fips)
                c_sec = county_sector_df[
                    county_sector_df["county_name"] == selected_county
                ].copy() if county_sector_df is not None else pd.DataFrame()
                if c_sec.empty:
                    st.info("No sector projection rows available for this county.")
                else:
                    sec_end = int(c_sec["year"].max())
                    exposure_rows = []
                    for sector in SECTORS:
                        s_row = c_sec[c_sec["sector"] == sector]
                        end_row = s_row[s_row["year"] == sec_end]
                        if s_row.empty or end_row.empty:
                            continue
                        emp_23 = s_row["emp_base"].iloc[0]
                        emp_e = float(end_row["emp_proj"].iloc[0])
                        net_new = emp_e - emp_23 if not pd.isna(emp_23) else None
                        exposure_rows.append({
                            "Sector": SECTOR_DISPLAY_NAMES.get(sector, sector),
                            f"{sec_base_year} Jobs": _fmt(emp_23) if not pd.isna(emp_23) else "—",
                            f"{sec_end} Jobs": _fmt(emp_e),
                            "Net": f"{net_new:+,.0f}" if net_new is not None else "—",
                        })
                    st.dataframe(pd.DataFrame(exposure_rows), hide_index=True, use_container_width=True)
            else:
                st.info("Sector projections are not available for this state.")

        with right:
            st.markdown("#### Local Action Signals")
            action_rows = []

            ipeds_df = load_ipeds(state_fips)
            if ipeds_df is not None and not ipeds_df.empty:
                county_ipeds = ipeds_df[
                    ipeds_df["county_fips"].astype(str).str.zfill(3) == county_fips3
                ]
                latest_ipeds = int(ipeds_df["year"].max())
                completions = (
                    county_ipeds[county_ipeds["year"] == latest_ipeds]["completions"].sum()
                    if not county_ipeds.empty else 0
                )
                action_rows.append({
                    "Signal": f"IPEDS completions ({latest_ipeds})",
                    "Value": _fmt(completions),
                    "Read": "local credential output",
                })
            else:
                action_rows.append({
                    "Signal": "IPEDS completions",
                    "Value": "—",
                    "Read": "not loaded",
                })

            commute_df = load_commute(state_fips)
            if commute_df is not None and not commute_df.empty:
                c_commute = commute_df[
                    commute_df["county_fips"].astype(str).str.zfill(3) == county_fips3
                ]
                if not c_commute.empty:
                    row = c_commute.iloc[0]
                    action_rows.append({
                        "Signal": "In-commuter share",
                        "Value": f"{row['pct_workers_imported']:.1f}%",
                        "Read": "labor shed dependency",
                    })
                    action_rows.append({
                        "Signal": "Top feeder counties",
                        "Value": row.get("top_feeder_counties", "—") or "—",
                        "Read": "home counties of in-commuters",
                    })
            else:
                action_rows.append({
                    "Signal": "Commute flows",
                    "Value": "—",
                    "Read": "not loaded",
                })

            st.dataframe(pd.DataFrame(action_rows), hide_index=True, use_container_width=True)

        st.markdown("#### Annual Population Projection")
        explorer_tbl = county_proj[[
            "year", "p10", "p50", "p90", "retirements_p50", "entries_p50", "pct_change_p50"
        ]].copy()
        explorer_tbl.columns = [
            "Year", "P10", "Median", "P90", "Annual Retirements", "Annual Entries", f"% vs {base_year}"
        ]
        for c in ["P10", "Median", "P90", "Annual Retirements", "Annual Entries"]:
            explorer_tbl[c] = explorer_tbl[c].map(_fmt)
        explorer_tbl[f"% vs {base_year}"] = explorer_tbl[f"% vs {base_year}"].map(lambda x: f"{x:+.1f}%")
        st.dataframe(explorer_tbl, hide_index=True, use_container_width=True)

    # ═════════════════════════════════════════════════════════════════════
    # TAB 7 — DATA TABLE
    # ═════════════════════════════════════════════════════════════════════
    with tab_data:
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
            "County", f"Baseline {base_year}", f"Median {end_year}",
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
            "Baseline Workforce (largest first)": (f"Baseline {base_year}", True),
        }
        sort_col, sort_asc = sort_map.get(sort_by, ("% Change", True))
        disp = disp.sort_values(sort_col, ascending=sort_asc)

        for c in [f"Baseline {base_year}", f"Median {end_year}",
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
    # TAB 9 — METHODOLOGY
    # ═════════════════════════════════════════════════════════════════════
    with tab_method:
        st.markdown(f"""
## Methodology

### Model Type
Annual **cohort-component** model tracking the working-age population (18–64) in each
county of **{selected_state}** from a {base_year} ACS baseline through {end_year}.

### Core Data Sources
| Source | Tab | CLI Flag | Description |
|--------|-----|----------|-------------|
| U.S. Census Bureau ACS 5-Year Estimates | Population / Available Workforce | (required) | Age-by-sex population (B01001) and labor-force status (B23001) for 2015–2024; each vintage is a 5-year period estimate |
| CDC 2021 National Life Tables | All | (built-in) | Age-specific annual survival probabilities |
| BLS Quarterly Census of Employment & Wages (QCEW) | Sector Exposure | (auto) | County annual employment and wages by NAICS sector, 2015–{sec_base_year} |

### Extended Data Sources (10-Dataset Integration)
| # | Source | Tab | CLI Flag | Description |
|---|--------|-----|----------|-------------|
| 1 | BLS LAUS | Available Workforce | `--laus` | County labor force counts and unemployment context |
| 2 | NCES IPEDS | Local Action | `--ipeds` | Postsecondary completions by CIP program and county |
| 3 | Census LODES | Local Action | `--lodes` | Origin-destination commute flows; local vs. imported worker share |
| 4 | BLS OES | (internal) | `--oes` | Occupational employment and wage estimates by sector |
| 5 | Census CBP | (internal) | `--cbp` | County business patterns; establishment counts and trends |
| 6 | BLS JOLTS | Demand Pressure | `--jolts` | National job openings and vacancy rates by sector |
| 7 | KDOL UI | Demand Pressure | `--kdol` | Kansas county UI claims by industry (KS-only) |
| 8 | KSDE/NCES CCD | (cohort model) | `--ksde` | K-12 enrollment by grade; patches ACS youth cohorts (KS-only) |
| 9 | SSA Disability | Available Workforce | `--ssa` | SSDI + SSI beneficiary counts; adjusts effective workforce |
| 10 | BLS Employment Projections | Demand Pressure | `--bls-proj` | 10-year national occupational employment projections |

### Components Modeled Each Year
1. **Survival** — Age-specific mortality applied to each 5-year cohort (CDC 2021 life tables)
2. **Aging** — Each year, 1/cohort-width of each age group advances to the next cohort
3. **Workforce entry** — The 15–17 cohort ages into the 18–24 workforce cohort (1/3 per year)
4. **Retirement exits** — The 60–64 cohort ages into 65+ (1/5 per year)
5. **Net migration** — Annual net migration rate applied proportionally across all working-age cohorts

### Migration Estimation
County net migration rates are estimated using the **cohort-survival residual method**:
- Historical working-age population change is observed from ACS 5-year snapshots (2015 → 2019 → 2021 → {base_year})
- Overlapping ACS 5-year intervals are downweighted/excluded where non-overlapping comparisons are available
- The expected change from mortality alone is subtracted, leaving the migration residual
- The mean and standard deviation form the county's migration distribution

### Prediction Intervals
2,000 Monte Carlo simulations per county. Each draws an annual migration-rate sequence
from an **AR(1) process** (φ = 0.3) reflecting migration persistence year-over-year.

| Band | Percentiles | Interpretation |
|------|-------------|----------------|
| 50% PI (IQR) | P25–P75 | Core range — outcomes in this band half the time |
| 80% PI | P10–P90 | Most likely range |
| 90% PI | P5–P95 | Near-full uncertainty envelope |

### Industry Sector Forecast
County-level employment and wage projections for five sectors using BLS QCEW 2015–{sec_base_year}
annual averages. NAICS code groupings:

| Sector | NAICS Codes |
|--------|------------|
| Healthcare | 62 — Health Care & Social Assistance |
| Manufacturing | 31–33 — Manufacturing (incl. production workers) |
| Hospitality, Entertainment & Food Service | 71 + 72 — Arts/Recreation + Accommodation/Food Services |
| Information & Professional Services | 51 + 54 — Information + Professional/Scientific/Technical Services |
| Utilities, Construction & Repair Services | 22 + 23 + 81 — Utilities + Construction + Other Repair/Personal Services |

**Option B** (independent county OLS trend) — used when {sec_base_year} county employment ≥ **500**
AND at least 3 historical observations exist. Employment is fit with a **log-linear**
OLS regression (fit on `log(employment)`, project, exponentiate back). Prediction
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
   instrument. With only 10 years of data (2015–{sec_base_year}) and COVID disruption in 2020–2021,
   real trends often fail the test at p < 0.05 but still convey useful direction. The
   `significant` flag is preserved and shown in the badge as informational context.
2. **MIN_OPT_B lowered from 2,000 → 500.** Kansas is dominated by small counties; the
   higher threshold excluded ~740 county-sector pairs whose trends are well-defined.
3. **Log-linear OLS** replaces straight linear OLS for employment fitting. Employment
   evolves multiplicatively (% growth per year), so log-space fitting produces more
   stable projections, prevents negative values, and yields long-run paths that match
   historical compounding behavior. Linear OLS is used automatically if any historical
   value is zero (log undefined), and remains the default for wage projections.

### Effective Labor Force (Participation Model)
When `--laus` or `--ssa` flags are used, the Available Workforce tab shows a three-layer
effective labor force estimate:

1. **ACS working-age population (18–64)** — raw cohort-model output
2. **Minus SSA disability** (SSDI + SSI, 18–64) — removes individuals with federal
   disability determinations (`disability_adjusted_pop`)
3. **× ACS B23001 civilian labor force participation rate** — accounts for structural non-participation
   (`effective_labor_force`)

The `layers_used` badge indicates which layers were populated for each county-year.
The cohort-model projection is scaled by each county's adjustment factor
(`effective_labor_force / working_age_pop`) to produce `eff_p50`, `eff_mean`, etc.

### Limitations
- National survival rates used; state-specific mortality may differ
- ACS 5-year vintages are overlapping period estimates, not independent annual observations
- Small counties (pop < 2,000) will have very wide confidence intervals
- Birth-rate pipeline — children born after {base_year} won't enter workforce until {base_year + 18}+
- JOLTS and BLS projections are **national** — state-level demand signals must be inferred
- LODES commute data lags 2–3 years; snapshot year may not match forecast base year
- KDOL UI claims are available only for Kansas; no stable public download API exists
- Participation model uses a static adjustment factor from the most recent data year;
  does not project future disability or participation rate changes
- Sector CI aggregation overstates uncertainty (sum of individual PIs, not joint CI)
- Economic shocks (plant closures, employer relocations) are not captured

### Adding Another State
Select any state from the sidebar — if no forecast exists yet, click **Generate Forecast**
to fetch and model it automatically (requires Census API key for best results).
        """)


if __name__ == "__main__":
    main()
