"""User-selected preview; adopted forecasts and metro panels stay unchanged."""
import json

import numpy as np
import pandas as pd
import streamlit as st

from demographic_preview import INPUT, PREVIEW, load_context, preview_paths
from dashboard.age_violin import age_violin_figure, band_summary, scale_for_paths
from dashboard.presentation import plot_chart


def preview_stamp():
    files = [(PREVIEW, ["source_manifest.json", "population_july2025.json", "cc-est2025-syasex-20.csv"]),
             (INPUT, ["acs2024_5yr_B01001_response.json", "acs2024_5yr_B23001_response.json",
                      "acs2024_5yr_B23001_labels.json", "acs2024_5yr_B23001_state_response.json",
                      "survival_age_sex.json", "census_fertility_rates.json",
                      "fertility_pooled_births_2020_2024.json", "births_county_2006_2025.json"])]
    return tuple((folder/name).stat().st_mtime_ns for folder, names in files for name in names)


@st.cache_data(show_spinner=False)
def cached_context(county, stamp):
    return load_context(county)


def render_preview(county, key):
    from dashboard.demographic_chart import pipeline_figure, entrants_figure
    st.info("Preview · Census July 2025 population → 2043. The adopted forecast and other dashboard models remain unchanged.")
    try:
        context = cached_context(str(county), preview_stamp())
        paths, schedules = preview_paths(context)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error(f"2025–2043 preview unavailable: {exc}")
        return None
    population, _, _, proxies, _, _, calibration, adjustment, _ = context
    st.caption("2025 → 2043 · Eighteen years from July 1, 2025. Published single ages 0–84; "
               "the published 85+ total is split using national survivor weights.")
    st.write("Follow resident age cohorts under fixed ACS 2020–2024 effective participation shares, "
             "constant national survival and no net migration in the baseline. These are conditional county projections.")
    fertility = st.radio("Fertility path", ["Low", "Central", "High"], index=1, horizontal=True, key=key+"fertility")
    st.caption("2026–2043 births are modeled. Fertility retains 2020–2024 births/ACS female exposure and the original "
               "2025 birth-level factor. The 2025 estimate is not inserted again after the new baseline. "
               "Low/high age-specific rates reach ±20% of central by 2043. These are sensitivities, not probability bounds.")
    scenario_paths = None
    with st.expander("Compare migration and participation assumptions"):
        if st.checkbox("Show a scenario alongside the demographic baseline", key=key+"compare"):
            migration = st.slider("Annual net migration of residents ages 18–64 (% of baseline population ages 18–64)", -2., 2., 0., .1, key=key+"migration")
            participation = st.slider("Change in civilian labor-force share by 2043 (percentage points)", -10., 10., 0., .5, key=key+"participation")
            migrants = population[:, 18:65].sum()*migration/100
            scenario_paths, _ = preview_paths(context, migrants, participation)
            st.caption(f"{migrants:+,.0f} net age-18–64 residents each year. Arrivals use the baseline adult age/sex mix; "
                       "departures use the remaining adult mix, capped at available residents. Migrants arrive at year-end, "
                       "affecting births from the following year. Participation changes phase in over 18 years, bounded at 0–100%. "
                       "These assumptions affect this preview only.")
            if not np.allclose(scenario_paths[fertility][0].net_migrants_applied.iloc[1:], migrants):
                st.warning("Requested outmigration is capped at available residents; downloads report applied amounts.")
    year = st.slider("Explore the population by year", 2025, 2043, 2025, 1, format="%d", key=key+"year")
    chosen = "No net migration"
    if scenario_paths is not None:
        chosen = st.radio("Population path", ["No net migration", "Selected scenario"], horizontal=True, key=key+"path")
    selected = paths if chosen == "No net migration" else scenario_paths
    baseline, _ = paths[fertility]
    summary, ages = selected[fertility]
    density = scale_for_paths(*[states for _, states in paths.values()],
                              *([] if scenario_paths is None else [states for _, states in scenario_paths.values()]))
    state = ages[year-2025]
    plot_chart(age_violin_figure(state, year, density, baseline_state=ages[0], baseline_year=2025), width="stretch", key=key+"chart")
    st.caption("Width shows resident age density, not uncertainty. One scale covers all enabled years/paths. "
               "Dots mark median age; bars mark the middle half. Changes compare with July 2025; terminal age represents 119+.")
    row = summary.iloc[year-2025]
    st.caption(f"{year} · {chosen} · {fertility} fertility: {state.sum():,.0f} modeled residents of all ages · "
               f"{row.labor_force_18_plus:,.0f} modeled civilian labor-force participants age 18+.")
    if year > 2025:
        st.caption(f"{row.births:,.0f} modeled births in the year ending June 30, {year} · {row.residents_under_18:,.0f} residents under 18.")
    with st.expander("Selected-year age counts and labor-force trend"):
        st.dataframe(band_summary(state, ages[0]), hide_index=True, width="stretch")
        plot_chart(pipeline_figure(baseline, None if scenario_paths is None else scenario_paths[fertility][0]), width="stretch", key=key+"labor_trend")
    st.caption(f"No-net-migration baseline: {baseline.labor_force_18_plus.iloc[0]:,.0f} participants in 2025 → "
               f"{baseline.labor_force_18_plus.iloc[-1]:,.0f} in 2043. Not an official Census labor-force forecast or an 80% prediction interval.")
    st.markdown("##### Who is approaching working age?")
    plot_chart(entrants_figure(baseline), width="stretch", key=key+"entrants")
    st.caption("Residents reaching 18/65 are cohort indicators, not hires or retirements. Births after the baseline remain under 18 through 2043.")
    if proxies:
        st.warning("Kansas age/sex participation shares substitute for zero-population ACS county cells: "+"; ".join(proxies)+".")
    proxy_bands = int(calibration.Method.str.contains("Kansas proxy").sum())
    if proxy_bands:
        st.caption(f"Kansas fertility proxies apply in {proxy_bands} maternal-age bands; see calibration details.")
    with st.expander("Preview sources, calibration and downloadable values"):
        st.write("Population: Census CC-EST2025-SYASEX, July 1, 2025, January 2025 boundaries. Participation: "
                 "ACS B23001 civilian labor force divided by residents in 2020–2024 age/sex bands, not official civilian-noninstitutional LFPRs. "
                 "The age-16–19 share applies at 18–19. Population and participation periods differ. National SSA2023 survival is fixed, "
                 "including a modeled 119+ terminal cell. No county-specific future migration, participation or probabilistic calibration is implied. "
                 "Enrollment is not added to residents.")
        st.write(f"Fertility retains the existing 2020–2024 KDHE/ACS calibration and original 2025 level factor ({adjustment:.5f}). "
                 "That factor was calibrated on ACS2024, not PEP2025. Census national schedules supply age-specific changes through 2043; "
                 "birth cohorts use half-year/midpoint survival. This transferred calibration assumption is not an independently validated "
                 "2026 birth anchor. Historical fertility diagnostics do not establish 18-year accuracy. The Kansas City cross-state "
                 "comparison and other dashboard models retain their adopted inputs.")
        st.markdown("[Census population source](https://www.census.gov/data/tables/time-series/demo/popest/2020s-counties-detail.html) · "
                    "[ACS participation table](https://api.census.gov/data/2024/acs/acs5/groups/B23001.html)")
        st.dataframe(calibration, hide_index=True, width="stretch")
        outcome = pd.DataFrame([{"Fertility path": name, "Births in 2043": frame.births.iloc[-1],
                                 "Residents under 18 in 2043": frame.residents_under_18.iloc[-1]}
                                for name, (frame, _) in selected.items()])
        st.dataframe(outcome, hide_index=True, width="stretch")
        exported = pd.concat([frame.assign(path=label) for label, result in
                              [("No net migration", paths), ("Selected scenario", scenario_paths)] if result is not None
                              for frame, _ in result.values()], ignore_index=True)
        st.dataframe(exported, hide_index=True, width="stretch")
        st.download_button("Download preview trajectories (all fertility paths)", exported.to_csv(index=False).encode(),
                           f"{county}_preview_2025_2043.csv", "text/csv", key=key+"download")
        age_export = pd.DataFrame([{"county_fips": str(county), "population_basis": "Preview: Census PEP July2025", "year": year,
                                   "path": chosen, "fertility_path": fertility, "sex": sex, "age": age if age < 119 else "119+", "residents": value}
                                  for sex, values in zip(["male", "female"], state) for age, value in enumerate(values)])
        st.download_button("Download selected-year preview age counts", age_export.to_csv(index=False).encode(),
                           f"{county}_preview_age_counts_{year}.csv", "text/csv", key=key+"age_download")
        st.json(json.loads((PREVIEW/"source_manifest.json").read_text(encoding="utf-8")), expanded=False)
    return baseline
