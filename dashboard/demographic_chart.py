"""Resident cohort pipeline, separate from observed age-16+ LAUS measures."""
from pathlib import Path
import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from demographic_pipeline import (DISPLAY_BANDS, single_age_population,
                                  participation_shares, project_cohorts)
from dashboard.age_violin import age_violin_figure, band_summary, scale_for_paths
from dashboard.fertility_details import load_fertility, fertility_stamp, render_fertility_details
from fertility_model import project_fertility_paths

INPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "demographic_inputs"
COLORS = ["#91C9B3", "#36816B", "#235947", "#C09B42"]


def _record(table, county=None):
    records = [dict(zip(table[0], row)) for row in table[1:]]
    selected = [row for row in records if county is None or row.get("state", "")+row.get("county", "") == county]
    if len(selected) != 1:
        raise ValueError("A unique county demographic baseline is unavailable.")
    return selected[0]


@st.cache_data(show_spinner=False)
def load_inputs(county, stamp):
    """Read only reviewed local inputs; no network request on a UI rerun."""
    def read(name):
        return json.loads((INPUT_DIR / name).read_text(encoding="utf-8"))
    if not str(county).startswith("20"):
        raise ValueError("The demographic pipeline currently has reviewed inputs for Kansas counties only.")
    record = _record(read("acs2024_5yr_B01001_response.json"), county)
    record.update(_record(read("acs2024_5yr_B23001_response.json"), county))
    labels = read("acs2024_5yr_B23001_labels.json")["variables"]
    survival_rows = pd.DataFrame(read("survival_age_sex.json")).sort_values("age")
    if survival_rows.age.tolist() != list(range(120)):
        raise ValueError("Survival inputs must cover every age from 0 through 119.")
    survivors = survival_rows[["male_lx", "female_lx"]].to_numpy().T
    survival = 1-survival_rows[["male_qx", "female_qx"]].to_numpy().T
    population = single_age_population(record, survivors)
    state_record = _record(read("acs2024_5yr_B23001_state_response.json"))
    shares, proxies = participation_shares(record, labels, fallback=state_record, return_proxies=True)
    return population, shares, survival, proxies


def pipeline_figure(baseline, scenario=None):
    fig = go.Figure()
    for (name, _), color in zip(DISPLAY_BANDS.items(), COLORS):
        fig.add_trace(go.Scatter(x=baseline.year, y=baseline[f"labor_force_{name}"],
                                stackgroup="residents", mode="lines", name=f"Ages {name}",
                                line=dict(color=color, width=1), fillcolor=color,
                                hovertemplate=f"%{{x:.0f}} · ages {name}<br>%{{y:,.0f}} modeled participants<extra></extra>"))
    fig.add_trace(go.Scatter(x=baseline.year, y=baseline.labor_force_18_plus,
                            mode="lines", name="Total · no net migration",
                            line=dict(color="#183C31", width=2, dash="dot"),
                            hovertemplate="%{x:.0f}<br>%{y:,.0f} participants age 18+<extra></extra>"))
    if scenario is not None:
        fig.add_trace(go.Scatter(x=scenario.year, y=scenario.labor_force_18_plus,
                                mode="lines", name="Selected scenario",
                                line=dict(color="#DD6B20", dash="dash", width=3),
                                hovertemplate="%{x:.0f} scenario<br>%{y:,.0f} participants age 18+<extra></extra>"))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=100),
                      xaxis=dict(title="Reference year", tick0=2024, dtick=3, tickformat="d"),
                      yaxis=dict(title="Modeled civilian labor force · age 18+", tickformat=",.0f", rangemode="tozero"),
                      legend=dict(orientation="h", y=-.25, yanchor="top", font=dict(size=10)))
    return fig


def entrants_figure(baseline):
    years = baseline.iloc[1:]
    fig = go.Figure()
    for column, label, color in [("reaching_18", "Reaching age 18", "#36816B"),
                                 ("reaching_65", "Reaching age 65", "#C09B42")]:
        fig.add_trace(go.Bar(x=years.year, y=years[column], name=label, marker_color=color,
                            hovertemplate=f"%{{x:.0f}}<br>%{{y:,.0f}} residents {label.lower()}<extra></extra>"))
    fig.update_layout(height=310, barmode="group", margin=dict(l=10, r=10, t=10, b=75),
                      xaxis=dict(title="Reference year", tick0=2024, dtick=3, tickformat="d"),
                      yaxis=dict(title="Residents per year", tickformat=",.0f"),
                      legend=dict(orientation="h", y=-.3, yanchor="top"))
    return fig


def render_demographic_pipeline(county, key="demographic_pipeline_"):
    st.markdown("#### Resident workforce pipeline: 18-year demographic outlook")
    try:
        stamp = (INPUT_DIR / "source_manifest.json").stat().st_mtime_ns
        population, shares, survival, proxies = load_inputs(str(county), stamp)
        local_rates, national, calibration, birth_source = load_fertility(str(county), population, fertility_stamp())
        births_2025 = birth_source[birth_source.year.eq(2025)].births
        if births_2025.empty:
            raise ValueError("No published 2025 birth estimate for this county.")
        observed_births = float(births_2025.iloc[0])
        baseline_paths, baseline_schedules, bridge = project_fertility_paths(population, shares, survival,
                                                   local_rates, national, observed_births)
    except (ValueError, OSError, KeyError) as exc:
        st.info(f"Demographic pipeline unavailable: {exc}")
        return None
    st.caption("2024 → 2042 · Eighteen years from the ACS reference year. Move the year slider to follow resident age cohorts.")
    st.write("Follow resident age cohorts as they grow older, including new children from the county birth model. "
             "The baseline holds participation patterns constant and assumes no net migration.")
    st.caption("Census-style age-specific fertility: Kansas county births calibrate local rates, and Census national schedules "
               "guide future age-specific changes. The 2025 births are published estimates; 2026–2042 are conditional projections.")
    fertility_path = st.radio("Fertility path", ["Low", "Central", "High"], index=1, horizontal=True, key=key+"fertility")
    st.caption("Low / high gradually reach 20% below / above central fertility rates by 2042. These are assumptions, not probability bounds.")
    proxy_bands = int(calibration.Method.str.contains("Kansas proxy").sum())
    if proxy_bands:
        st.caption(f"This county uses Kansas fertility proxies in {proxy_bands} maternal-age bands because local counts are suppressed "
                   "or ACS female exposure is zero. The fertility details identify each band.")
    baseline, baseline_ages = baseline_paths[fertility_path]
    scenario = scenario_ages = scenario_paths = None
    with st.expander("Compare migration and participation assumptions"):
        compare = st.checkbox("Show a scenario alongside the demographic baseline", key=key+"compare")
        if compare:
            migration_rate = st.slider("Annual net migration of residents ages 18–64 (% of baseline population ages 18–64)",
                                       -2.0, 2.0, 0.0, .1, key=key+"migration")
            participation_change = st.slider("Change in civilian labor-force share by 2042 (percentage points)",
                                             -10.0, 10.0, 0.0, .5, key=key+"participation")
            migrants = population[:, 18:65].sum()*migration_rate/100
            scenario_paths, scenario_schedules, _ = project_fertility_paths(population, shares, survival,
                                       local_rates, national, observed_births,
                                       annual_net_migrants=migrants, participation_pp=participation_change)
            scenario, scenario_ages = scenario_paths[fertility_path]
            capped = not np.allclose(scenario.iloc[1:].net_migrants_applied, migrants)
            st.caption(f"Assumes {migrants:+,.0f} net residents ages 18–64 each year. Arrivals use the baseline adult age/sex mix; "
                       "departures use the remaining adult mix, capped at available residents. "
                       "The participation change phases in over 18 years for each adult age/sex group. "
                       "Migrants arrive at year-end and affect births from the following year. "
                       "These are selected assumptions, not estimated migration or probabilities. Scenarios here do not change the other dashboard models.")
            if capped:
                st.warning("Requested departures exceed available residents in at least one year. Actual outmigration is capped; "
                           "the downloaded values show the amount applied each year.")
    selected_year = st.slider("Explore the population by year", 2024, 2042, 2024, 1, format="%d", key=key+"year")
    path_name = "No net migration"
    if scenario is not None:
        path_name = st.radio("Population path", ["No net migration", "Selected scenario"], horizontal=True, key=key+"path")
        st.caption("Migration changes the resident shapes. Participation changes only the labor-force estimate below.")
    selected_path = baseline_ages if path_name == "No net migration" else scenario_ages
    selected_summary = baseline if path_name == "No net migration" else scenario
    all_age_paths = [ages for _,ages in baseline_paths.values()]
    if scenario_paths is not None:
        all_age_paths.extend(ages for _,ages in scenario_paths.values())
    density_scale = scale_for_paths(*all_age_paths)
    selected_state = selected_path[selected_year-2024]
    st.plotly_chart(age_violin_figure(selected_state, selected_year, density_scale), width="stretch", key=key+"chart")
    st.caption("Wider sections mean more residents at that age. Widths use one common scale across all age ranges and slider years. "
               "Dots mark median ages; vertical bars mark the middle half. Shapes smooth modeled ages, not individual records.")
    year_row = selected_summary.iloc[selected_year-2024]
    st.caption(f"{selected_year} · {path_name}: {selected_state.sum():,.0f} modeled residents of all ages · "
               f"{year_row.labor_force_18_plus:,.0f} modeled civilian labor-force participants age 18+.")
    if selected_year > 2024:
        st.caption(f"{fertility_path} fertility · {year_row.births:,.0f} births in the year ending June 30, {selected_year} · "
                   f"{year_row.residents_under_18:,.0f} residents under 18.")
    selected_paths = baseline_paths if path_name == "No net migration" else scenario_paths
    selected_schedules = baseline_schedules if path_name == "No net migration" else scenario_schedules
    render_fertility_details(selected_paths, selected_schedules, calibration, bridge, birth_source, str(county), key, path_name)
    with st.expander("Selected-year age counts and labor-force trend"):
        st.dataframe(band_summary(selected_state), hide_index=True, width="stretch")
        st.plotly_chart(pipeline_figure(baseline, scenario), width="stretch", key=key+"labor_trend")
    first, last = baseline.iloc[0], baseline.iloc[-1]
    change = last.labor_force_18_plus-first.labor_force_18_plus
    st.caption(f"No-net-migration baseline: {first.labor_force_18_plus:,.0f} modeled participants in 2024 → "
               f"{last.labor_force_18_plus:,.0f} in 2042 ({change:+,.0f}). "
               "These are conditional projections, not observed future counts or an 80% prediction interval.")
    if scenario is not None:
        st.caption(f"Selected scenario in 2042: {scenario.iloc[-1].labor_force_18_plus:,.0f} participants "
                   f"({scenario.iloc[-1].labor_force_18_plus-last.labor_force_18_plus:+,.0f} versus the demographic baseline).")
    st.markdown("##### Who is approaching working age?")
    st.plotly_chart(entrants_figure(baseline), width="stretch", key=key+"entrants")
    st.caption("Reaching 18 signals potential workforce entry; reaching 65 does not imply retirement. "
               "Workers age 65+ remain in the main chart. These bars count residents, not hires or job openings.")
    if proxies:
        st.warning("Kansas age/sex participation shares substitute for zero-population county cells: " + "; ".join(proxies) + ".")
    with st.expander("Demographic sources, assumptions and downloadable values"):
        st.write("Age and sex counts: Census ACS 2020–2024 five-year table B01001. This is a multiyear estimate, "
                 "treated as a 2024 reference baseline, not a point-in-time census. The first chart value is also modeled. "
                 "Single-year ages are allocated evenly within each published band below 85. Ages 85+ use the national life-table survivor distribution. "
                 "Each cohort then advances exactly one year per step; survivors at the table's terminal age remain in a 119+ group at age-119 mortality.")
        st.write("Participation: county ACS B23001 civilian labor-force counts divided by all residents in each age/sex band. "
                 "These effective shares account for baseline military and institutional exclusions; they are not the official civilian participation-rate denominator. "
                 "Shares remain constant as cohorts move between age bands. The same 16–19 share is used for ages 18–19. "
                 "Small county estimates can be noisy; ACS sampling error is not propagated into a probability band.")
        st.write("Survival: SSA 2023 period life table, published with the 2026 Trustees Report. National age/sex mortality is held constant; "
                 "it is not a county mortality forecast. The baseline includes no net migration. New birth cohorts use the separately "
                 "documented age-specific fertility paths and survival timing approximation. Children born after the reference baseline do not reach age 18 "
                 "within this horizon, so births change the child population but not the age-18+ labor-force total through 2042.")
        st.write("The headline LAUS measure elsewhere covers age 16+ and a different period and methodology. "
                 "It is not spliced into this age-18+ model. School enrollment is not added to population counts. "
                 "Public/private/homeschooled children are within the resident population universe; enrollment and county homeschool counts have not been integrated.")
        st.markdown("[Census age table](https://api.census.gov/data/2024/acs/acs5/groups/B01001.html) · "
                    "[Census labor-force table](https://api.census.gov/data/2024/acs/acs5/groups/B23001.html) · "
                    "[SSA life table](https://www.ssa.gov/oact/STATS/table4c6.html) · "
                    "[Census cohort methodology](https://www.census.gov/programs-surveys/popproj/about.html)")
        display = baseline if scenario is None else pd.concat([baseline.assign(path="No net migration"),
                                                               scenario.assign(path="Selected scenario")], ignore_index=True)
        st.dataframe(display, hide_index=True, width="stretch")
        st.download_button("Download demographic pipeline", display.to_csv(index=False).encode(),
                           f"{county}_resident_pipeline_2024_2042.csv", "text/csv", key=key+"download")
    return baseline
