"""County outlook views. Resident measures and workplace jobs retain their universes."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.presentation import plot_chart

from fetch_qcew import SECTOR_NAICS, SECTOR_DISPLAY_NAMES

TEAL = "#36816B"
BLUE = "#315E88"
GOLD = "#BA793B"


def county_rows(frame, county):
    """Select full county FIPS, checking state when the stored county code is short."""
    if frame is None or frame.empty or "county_fips" not in frame:
        return pd.DataFrame()
    county = str(county)
    if len(county) != 5 or not county.isdigit():
        raise ValueError("Select a five-digit county FIPS")
    codes = frame.county_fips.astype("string").str.zfill(3)
    mask = codes.eq(county) | codes.eq(county[-3:])
    if "state_fips" in frame:
        states = pd.to_numeric(frame.state_fips, errors="coerce")
        mask &= states.eq(int(county[:2]))
    elif codes.str.len().eq(3).any() and county[:2] != "20":
        # These unqualified county-only outputs belong to the Kansas pilot.
        mask &= codes.eq(county)
    return frame.loc[mask.fillna(False)].copy()


def exact_sector(industry):
    """A constituent NAICS cannot stand in for the model's broader composite."""
    selected = str(industry).strip()
    for name, codes in SECTOR_NAICS.items():
        if selected == "+".join(codes):
            return name
    return None


def _trajectory(rows, value, lower, upper):
    if rows.empty or not {"year", value}.issubset(rows):
        return pd.DataFrame(columns=["year", "value", "lower", "upper"])
    result = pd.DataFrame({"year": pd.to_numeric(rows.year, errors="coerce"),
                           "value": pd.to_numeric(rows[value], errors="coerce")})
    for target, source in (("lower", lower), ("upper", upper)):
        result[target] = pd.to_numeric(rows[source], errors="coerce") if source in rows else np.nan
    if result.year.isna().any() or result.year.duplicated().any():
        raise ValueError("A forecast must contain one observation per county and year")
    invalid = ~(result.lower.le(result.value) & result.value.le(result.upper))
    result.loc[invalid, ["lower", "upper"]] = np.nan
    return result.sort_values("year").reset_index(drop=True)


def _observed_baseline(rows, value):
    if rows.empty or not {"base_year", value}.issubset(rows):
        return None
    pairs = rows[["base_year", value]].drop_duplicates()
    if len(pairs) != 1 or pairs.isna().any().any():
        return None
    year, amount = pairs.iloc[0]
    if not np.isfinite(float(amount)) or amount < 0:
        return None
    return int(year), float(amount)


def prepare_forecast(proj, effective, sectors, summary, county, industry):
    """Pure view model; never joins resident counts to workplace job counts."""
    population = county_rows(proj, county)
    participation = county_rows(effective, county)
    jobs = county_rows(sectors, county)
    sector = exact_sector(industry)
    jobs = jobs[jobs.sector.eq(sector)] if sector and "sector" in jobs else pd.DataFrame()
    observed_jobs = jobs
    if "baseline_availability_status" in jobs:
        observed_jobs = jobs[jobs.baseline_availability_status.eq("published")]
    result = {
        "population": _trajectory(population, "p50", "p10", "p90"),
        "participation": _trajectory(participation, "eff_p50", "eff_p10", "eff_p90"),
        "jobs": _trajectory(jobs, "emp_proj", "emp_ci_lo", "emp_ci_hi"),
        "population_base": _observed_baseline(population, "workforce_base"),
        "jobs_base": _observed_baseline(observed_jobs, "emp_base"),
        "sector": sector,
        "sector_note": "; ".join(jobs.note.dropna().astype(str).unique()) if "note" in jobs else "",
        "sector_metadata": {field: jobs[field].drop_duplicates().tolist()
                            for field in ("method", "history_last_year", "history_usable_years",
                                          "model_quality", "baseline_availability_status", "base_year")
                            if field in jobs},
        "county": county,
    }
    # The summary may supply a baseline count, but never invent its date.
    if result["population_base"] is None:
        result["population_base"] = _observed_baseline(county_rows(summary, county), "workforce_base")
    return result


def sector_band(view):
    methods = set(view.get("sector_metadata", {}).get("method", []))
    if methods == {"option_b"}:
        return "80% prediction interval", "County OLS prediction interval."
    if methods and methods.issubset({"option_a", "option_a_fallback"}):
        return "Scaled state-model band", "State model bounds scaled by the county allocation; the band excludes uncertainty in the county share and population proxy."
    return "Model uncertainty band", "Check the model method before interpreting its uncertainty band."


def outlook_summary(view):
    """Compare endpoint changes only over years shared by both model series."""
    supply = view["participation"].dropna(subset=["value"])
    jobs = view["jobs"].dropna(subset=["value"])
    common = sorted(set(supply.year) & set(jobs.year))
    if len(common) < 2:
        return "Review the resident outlook below. A paired employment outlook requires a compatible industry model with shared forecast years."
    first, last = common[0], common[-1]
    changes = []
    for rows in (supply, jobs):
        start = rows.loc[rows.year.eq(first), "value"].iloc[0]
        end = rows.loc[rows.year.eq(last), "value"].iloc[0]
        if start <= 0:
            return "The model series do not support a meaningful percentage-change comparison."
        changes.append((end/start - 1)*100)
    return (f"From {int(first)} to {int(last)}, the existing models project participating residents "
            f"to change by {changes[0]:+.1f}% and {view['sector'].lower()} workplace employment by {changes[1]:+.1f}%. "
            "Use the current conditions and pathways below to investigate what could sustain or change those trajectories.")


def indexed_trajectories(view):
    """Model trajectories at one common model year; no interpolation or level gap."""
    usable = {k: v.dropna(subset=["value"]) for k, v in view.items()
              if k in ("population", "participation", "jobs") and not v.empty}
    if len(usable) < 2:
        return pd.DataFrame()
    common = set.intersection(*(set(frame.year) for frame in usable.values()))
    if not common:
        return pd.DataFrame()
    base = min(common)
    records = []
    for name, frame in usable.items():
        denominator = frame.loc[frame.year.eq(base), "value"].iloc[0]
        if denominator <= 0:
            return pd.DataFrame()
        selected = frame.loc[frame.year.ge(base), ["year", "value"]].copy()
        selected["index"] = selected.value / denominator * 100
        selected["series"] = name
        selected["index_year"] = int(base)
        records.append(selected)
    return pd.concat(records, ignore_index=True)


def scenario_projection(proj, effective, county, participation_pp=0.0, annual_net_migration=0.0):
    """Additive sensitivity, not a new demographic simulation or probability model.

    The participation change is immediate and constant across forecast years.
    Extra age-18–64 residents arrive at each forecast year-end and remain in this
    arithmetic sensitivity: annual increment × (year - first model year + 1).
    No additional aging, mortality, births, commuting, or placement is modeled.
    """
    if not np.isfinite([participation_pp, annual_net_migration]).all():
        raise ValueError("Scenario assumptions must be finite")
    p = county_rows(proj, county)
    e = county_rows(effective, county)
    if p.empty or e.empty or not {"eff_p50", "participation_adj_factor"}.issubset(e):
        return pd.DataFrame()
    _trajectory(p, "p50", "p10", "p90")
    _trajectory(e, "eff_p50", "eff_p10", "eff_p90")
    if not {"year", "p50"}.issubset(p):
        return pd.DataFrame()
    result = p[["year", "p50"]].merge(e[["year", "eff_p50", "participation_adj_factor"]],
                                       on="year", how="left", validate="one_to_one").sort_values("year")
    for col in ("p50", "eff_p50", "participation_adj_factor"):
        result[col] = pd.to_numeric(result[col], errors="coerce").astype(float)
    factors = result.participation_adj_factor.dropna()
    if factors.empty or not factors.between(0, 1).all() or factors.nunique() != 1:
        return pd.DataFrame()
    result["baseline_population"] = result.p50
    result["baseline_participation"] = result.eff_p50
    result["additional_residents"] = annual_net_migration * (result.year - result.year.min() + 1)
    result["scenario_population"] = (result.p50 + result.additional_residents).clip(lower=0)
    result["scenario_factor"] = (result.participation_adj_factor + participation_pp / 100).clip(0, 1)
    # Add the unrounded change to the stored rounded baseline: zero assumptions
    # reproduce the adopted output exactly instead of introducing rounding drift.
    result["scenario_participation"] = result.eff_p50 + (
        result.scenario_population * result.scenario_factor - result.p50 * result.participation_adj_factor)
    if participation_pp or annual_net_migration:
        result["scenario_participation"] = result.scenario_participation.clip(lower=0).clip(upper=result.scenario_population)
    result["change"] = result.scenario_participation - result.baseline_participation
    return result.reset_index(drop=True)


def _figure(rows, color, label, band_label, baseline=None, limits=None):
    fig = go.Figure()
    if not rows.empty:
        fig.add_trace(go.Scatter(x=rows.year, y=rows.lower, line=dict(width=0),
                                showlegend=False, hoverinfo="skip", connectgaps=False))
        fig.add_trace(go.Scatter(x=rows.year, y=rows.upper, line=dict(width=0),
                                fill="tonexty", fillcolor="rgba(90,125,145,0.16)",
                                name=band_label, hoverinfo="skip", connectgaps=False))
        fig.add_trace(go.Scatter(x=rows.year, y=rows.value, name=label, mode="lines+markers",
                                line=dict(color=color, dash="dash"), connectgaps=False,
                                hovertemplate="%{x}: %{y:,.0f}<extra>%{fullData.name}</extra>"))
    if baseline is not None:
        fig.add_trace(go.Scatter(x=[baseline[0]], y=[baseline[1]], name="Observed base estimate",
                                mode="markers", marker=dict(color=color, symbol="diamond", size=10),
                                hovertemplate="%{x}: %{y:,.0f}<extra>Observed base estimate</extra>"))
    fig.update_layout(height=340, margin=dict(l=5, r=10, t=20, b=100),
                      legend=dict(orientation="h", y=-.35, yanchor="top", font=dict(size=10)),
                      yaxis=dict(range=_zoom_range(rows, baseline), tickformat=",.0f"),
                      xaxis=dict(title="Reference year", dtick=2, range=limits))
    return fig


def _zoom_range(rows, baseline=None):
    """Fit the y-axis to the trend line so slopes are visible.

    The axis does not start at zero. The uncertainty band is included only up to
    one trend-span beyond the line, so a very wide band cannot flatten the trend;
    a band that runs past the view is clipped, not hidden in the data.
    """
    values = [v for v in rows.value if pd.notna(v)] if not rows.empty else []
    if baseline is not None and pd.notna(baseline[1]):
        values.append(baseline[1])
    if not values:
        return None
    low, high = min(values), max(values)
    span = (high - low) or abs(high) * .02 or 1.0
    lows = [v for v in rows.lower if pd.notna(v)] if not rows.empty else []
    highs = [v for v in rows.upper if pd.notna(v)] if not rows.empty else []
    if lows:
        low = max(min(lows), low - span)
    if highs:
        high = min(max(highs), high + span)
    pad = (high - low) * .08
    return [low - pad, high + pad]


def render_forecast(proj, effective, sectors, summary, county, industry, key="outlook_forecast_"):
    view = prepare_forecast(proj, effective, sectors, summary, county, industry)
    st.info(outlook_summary(view))
    st.caption("PROJECTED · Existing model baselines and assumptions. Diamonds identify observed baseline estimates; dashed lines show modeled values. "
               "Vertical axes are zoomed to the trend and do not start at zero; very wide ranges may be clipped at the chart edge.")
    years = [float(y) for name in ("population", "participation", "jobs") for y in view[name].year]
    years += [point[0] for point in (view["population_base"], view["jobs_base"]) if point]
    limits = [min(years) - .5, max(years) + .5] if years else None
    jobs_band, jobs_band_note = sector_band(view)
    columns = st.columns(3)
    specs = [("population", "Residents ages 18–64", TEAL, "Median population", "80% simulation range", view["population_base"]),
             ("participation", "Participating residents, approximate ages 18–64", TEAL, "Static participation model", "Scaled 80% simulation range", None),
             ("jobs", "Workplace employment", BLUE, "Modeled jobs", jobs_band, view["jobs_base"])]
    for column, (name, title, color, label, band, baseline) in zip(columns, specs):
        with column:
            st.markdown(f"<div style='font-weight:600;min-height:3.5rem'>{title}</div>", unsafe_allow_html=True)
            if name == "jobs" and view["sector"] is None:
                st.info("No exact industry forecast for this NAICS selection. Broader grouped sectors are not substituted.")
            elif view[name].empty or view[name].value.notna().sum() == 0:
                st.info("This county model is unavailable.")
            else:
                plot_chart(_figure(view[name], color, label, band, baseline, limits),
                                width="stretch", key=key + name)
                if name == "population" and baseline:
                    st.caption(f"ACS {baseline[0]-4}–{baseline[0]} period estimate shown at its ending year. Dashed line: cohort model; the band is P10–P90 across simulations.")
                elif name == "participation":
                    st.caption("County civilian participation held constant, using the approximate 18–64 ACS baseline. The band scales population simulations and excludes uncertainty in participation.")
                elif name == "jobs":
                    st.caption(SECTOR_DISPLAY_NAMES.get(view["sector"], view["sector"]) + ". County workplace jobs. " + jobs_band_note)
                    metadata = view["sector_metadata"]
                    history = [str(int(v)) for v in metadata.get("history_last_year", []) if pd.notna(v)]
                    st.caption("Last usable local history: " + (", ".join(history) if history else "unavailable") + ".")
                    quality = set(metadata.get("model_quality", []))
                    if "demographic_proxy_no_local_observations" in quality:
                        st.warning("Demographic proxy: no usable local employment observations. This is an allocated estimate.")
                    elif "modeled_from_older_observations" in quality:
                        st.warning("Uses older local employment observations. The current baseline is unavailable or suppressed.")
                    labels = {"method": "Method", "history_last_year": "Last usable historical year",
                              "history_usable_years": "Usable historical years", "model_quality": "Model quality",
                              "baseline_availability_status": "Baseline availability", "base_year": "Baseline year"}
                    with st.expander("Employment model details"):
                        st.write(view["sector_note"])
                        st.caption(" · ".join(labels[field] + ": " + ", ".join("unavailable" if pd.isna(v) else str(v) for v in values)
                                             for field, values in metadata.items()))
    st.caption("These are different populations and model assumptions. Resident participation is not an industry-specific labor pool; subtracting it from workplace jobs would not measure a worker shortage. Cohort age exits mean leaving the age range, not retirement.")
    indexed = indexed_trajectories(view)
    if not indexed.empty and st.checkbox("Compare modeled growth from a common year", key=key + "indexed"):
        labels = {"population": "Resident population", "participation": "Participating residents", "jobs": "Workplace jobs"}
        fig = go.Figure()
        for name, rows in indexed.groupby("series"):
            fig.add_trace(go.Scatter(x=rows.year, y=rows["index"], name=labels[name], mode="lines+markers",
                                    line=dict(color=BLUE if name == "jobs" else TEAL,
                                              dash="dot" if name == "participation" else "solid")))
        year = int(indexed.index_year.iloc[0])
        fig.update_layout(height=300, yaxis_title=f"{year} model value = 100", xaxis_title="Model year")
        plot_chart(fig, width="stretch", key=key + "index_chart")
        st.caption("Indexed paths compare growth only. The common starting point is modeled, and no resident-to-job gap is calculated.")
    return view


def render_scenarios(proj, effective, county, key="outlook_scenario_"):
    baseline = scenario_projection(proj, effective, county)
    if baseline.empty or baseline.baseline_participation.notna().sum() == 0:
        st.info("A matched county population and participation baseline is required for scenarios.")
        return baseline
    st.caption("Assumption sensitivity only. This does not rerun the cohort forecast, assign probabilities, or estimate training placements.")
    st.caption(f"Assumed start: {int(baseline.year.min())}. Changes apply from the first displayed forecast year, not from today's date.")
    left, right = st.columns(2)
    with left:
        pp = st.slider("Participation change (percentage points)", -20.0, 20.0, 0.0, .5, key=key + "pp")
    with right:
        migration = st.number_input("Additional net age-18–64 residents each forecast year", value=0, step=50, key=key + "migration")
    result = scenario_projection(proj, effective, county, pp, migration)
    fig = go.Figure()
    for field, label, color in (("baseline_participation", "Adopted static-participation model", TEAL),
                                ("scenario_participation", "Assumption sensitivity", GOLD)):
        fig.add_trace(go.Scatter(x=result.year, y=result[field], name=label, mode="lines+markers",
                                line=dict(color=color), connectgaps=False))
    fig.update_layout(height=330, yaxis_title="Participating residents, approximate ages 18–64",
                      xaxis_title="Model year", legend=dict(orientation="h", y=1.15))
    plot_chart(fig, width="stretch", key=key + "chart")
    last = result.iloc[-1]
    if pd.notna(last.scenario_participation):
        st.metric(f"{int(last.year)} sensitivity", f"{last.scenario_participation:,.0f}", f"{last.change:+,.0f} versus adopted model")
    factor = result.participation_adj_factor.dropna().iloc[0]
    st.caption(f"Baseline participation factor: {factor:.1%}. The percentage-point change applies in every modeled year and is capped at 0–100%. Additional residents accumulate from {int(result.year.min())}, arriving at each year-end and remaining in subsequent years. This arithmetic assumes no extra aging or mortality and applies the same participation factor to those residents. Negative population is capped at zero; no employment or placement effect is assumed.")
    with st.expander("Scenario values and assumptions"):
        st.dataframe(result[["year", "baseline_population", "scenario_population", "baseline_participation",
                             "scenario_factor", "scenario_participation", "change"]], hide_index=True, width="stretch")
    return result
