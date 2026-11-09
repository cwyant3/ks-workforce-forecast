"""County fertility calibration, scenario charts and transparent validation."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from demographic_pipeline import single_age_population
from fertility_model import national_schedules, calibrated_schedule
from fertility_validation import compare_fertility_models

INPUT_DIR = Path(__file__).resolve().parents[1]/"data"/"demographic_inputs"
COLORS = {"Low":"#7BA8D0", "Central":"#36816B", "High":"#C09B42"}


@st.cache_data(show_spinner=False)
def load_fertility(county, population, stamp):
    def read(name):
        return json.loads((INPUT_DIR/name).read_text(encoding="utf-8"))
    national = national_schedules(read("census_fertility_rates.json"))
    pooled = pd.DataFrame(read("fertility_pooled_births_2020_2024.json"))
    acs = read("acs2024_5yr_B01001_response.json")
    survival = pd.DataFrame(read("survival_age_sex.json")).sort_values("age")
    survivors = survival[["male_lx","female_lx"]].to_numpy().T
    state = sum(single_age_population(dict(zip(acs[0], row)),survivors) for row in acs[1:])
    local, calibration = calibrated_schedule(county,pooled,population,state,national)
    history = pd.DataFrame(read("births_county_2006_2025.json"))
    history = history[history.county_fips.eq(county)].copy()
    return local,national,calibration,history


def fertility_stamp():
    return tuple((INPUT_DIR/name).stat().st_mtime_ns for name in
                 ("census_fertility_rates.json","fertility_pooled_births_2020_2024.json",
                  "births_county_2006_2025.json","acs2024_5yr_B01001_response.json","survival_age_sex.json"))


def render_fertility_details(paths, schedules, calibration, bridge, history, county, key, migration_label):
    with st.expander("Birth history, fertility paths and accuracy checks"):
        st.write("Births = projected female exposure at each age × that age's fertility rate, summed across ages. "
                 "The central path applies Census national age-specific rate changes to a locally calibrated schedule. "
                 "These are our county projections, not official Census county forecasts.")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=history.year,y=history.births,name="Census estimated births · 2006–2025",
                                mode="lines+markers",line=dict(color="#A6B1BD")))
        for name,(summary,_) in paths.items():
            rows=summary[summary.year>=2025]
            fig.add_trace(go.Scatter(x=rows.year,y=rows.births,name=name+" fertility",mode="lines",
                                    line=dict(color=COLORS[name],dash="dot",width=3)))
        fig.update_layout(height=330,margin=dict(l=10,r=10,t=20,b=85),
                          title="Annual births · "+migration_label,
                          xaxis=dict(title="Year ending June 30",tickformat="d"),
                          yaxis=dict(title="Births per year",rangemode="tozero"),
                          legend=dict(orientation="h",y=-.3))
        st.plotly_chart(fig,width="stretch",key=key+"fertility_births")
        st.caption("Low and high gradually reach 20% below/above the central age-specific rates by 2042. "
                   "They are analyst sensitivity assumptions, not 80% prediction intervals. "
                   "Birth totals also respond to female cohort size and composition; a decline is not imposed.")
        outcome=pd.DataFrame([{"Fertility path":name,"Births in 2042":summary.iloc[-1].births,
                               "Residents under 18 in 2042":summary.iloc[-1].residents_under_18,
                               "Female exposure ages 14–54 in 2042":summary.iloc[-1].female_exposure_14_54}
                              for name,(summary,_) in paths.items()])
        count_columns = [column for column in outcome if column != "Fertility path"]
        outcome[count_columns] = outcome[count_columns].round().astype(int)
        st.dataframe(outcome,hide_index=True,width="stretch")
        rates=go.Figure()
        rates.add_trace(go.Scatter(x=list(range(14,55)),y=schedules["Central"][2025][14:55]*1000,
                                  name="Calibrated 2025",line=dict(color="#A6B1BD")))
        for name in paths:
            rates.add_trace(go.Scatter(x=list(range(14,55)),y=schedules[name][2042][14:55]*1000,
                                      name=name+" · 2042",line=dict(color=COLORS[name],dash="dot")))
        rates.update_layout(height=290,margin=dict(l=10,r=10,t=20,b=70),
                            xaxis_title="Modeled maternal age",yaxis_title="Births per 1,000 women per year",
                            legend=dict(orientation="h",y=-.3))
        st.plotly_chart(rates,width="stretch",key=key+"fertility_rates")
        st.write("Local calibration pools KDHE resident births in calendar years 2020–2024. "
                 "ACS 2020–2024 female estimates × five approximate the matching woman-years. "
                 "Within each published maternal-age band, Census supplies the modeled single-age pattern. "
                 "Each local rate is stabilized with 1,000 woman-years of Kansas-rate prior exposure per band, an explicit modeling choice. "
                 "Suppressed counts and zero county female-exposure cells use a Kansas rate proxy. "
                 "The Kansas exposure is the sum of all 105 county ACS estimates.")
        st.dataframe(calibration,hide_index=True,width="stretch")
        st.caption("The KIC source combines mothers age 35+; its split across ages 35–54 is modeled from Census. "
                   "Under-15 births are assigned to the age-14 boundary. These are disclosed tail approximations. "
                   "Unknown maternal ages are excluded from age calibration; no suppressed counts are reconstructed.")
        st.write(f"Timing bridge: the pooled calendar-year schedule uses the mean of Census 2024/2025 year-ended rates as an "
                 f"age-pattern proxy. A county-wide factor of **{bridge:.3f}** aligns its modeled 2025 births to the published "
                 "Census July–June 2025 estimate. This is calibration, not independent validation. All three paths share that "
                 "observed estimate; forecasts begin in 2026. The Census history retains vintage boundaries in 2011 and 2021.")
        st.write("Each year uses mean start/end female exposure after aging and survival. Migrants arrive at year-end and "
                 "affect births from the following year; future female newborns can also become mothers within the horizon. "
                 "Newborn sex shares use the baseline under-five mix, with half-year infant survival and later midpoint transitions. "
                 "Participation assumptions affect workforce totals, not births. Child migration and fertility differences by migrant status are not modeled.")
        st.markdown("[KDHE county births](https://kic.kdhe.ks.gov/birth.php) · "
                    "[Census projected fertility schedules](https://www.census.gov/data/datasets/2023/demo/popproj/2023-popproj.html) · "
                    "[Census methodology](https://www2.census.gov/programs-surveys/popproj/technical-documentation/methodology/methodstatement23.pdf)")
        render_validation(county,key)
        export=pd.concat([summary.assign(county_fips=county,migration_path=migration_label)
                          for summary,_ in paths.values()],ignore_index=True)
        st.download_button("Download fertility paths and female exposure",export.to_csv(index=False).encode(),
                           f"{county}_fertility_paths.csv","text/csv",key=key+"fertility_download")
        rate_rows=[dict(county_fips=county,year=year,age=age,fertility_path=name,births_per_woman_year=float(rate[age]),
                        source="Census NP2023 GROUP0 relative changes; county/Kansas calibrated",level_bridge=bridge)
                   for name,years in schedules.items() for year,rate in years.items() for age in range(14,55)]
        st.download_button("Download modeled age-specific fertility rates",pd.DataFrame(rate_rows).to_csv(index=False).encode(),
                           f"{county}_age_specific_fertility.csv","text/csv",key=key+"fertility_rate_download")


@st.cache_data(show_spinner=False)
def load_comparison(county,stamp):
    return compare_fertility_models(county)


def render_validation(county,key):
    st.markdown("**Historical check: age-cohort mechanism versus birth-count benchmarks**")
    stamp=tuple((INPUT_DIR/name).stat().st_mtime_ns for name in
                ("fertility_county_births_2006_2024.json","fertility_birth_totals_2006_2024.json",
                 "female_native_age_sex_history_2010_2024.json"))
    ranking,errors,coverage=load_comparison(county,stamp)
    st.write("The 20-year Census history above provides context. This separate retrospective check uses "
             "KDHE calendar-year births (2006–2024) and past ACS female-age estimates. "
             "At each origin, five preceding calendar years calibrate frozen maternal-age rates; "
             "female cohorts then age forward. The comparison does not use the later Census fertility schedule or SSA mortality table.")
    if ranking.empty:
        st.info("This county lacks enough matched 1-, 3- and 5-year comparisons for an overall historical ranking.")
    else:
        st.dataframe(ranking.drop(columns="model").round(1),hide_index=True,width="stretch")
        cohort=ranking[ranking.model.eq("frozen_asfr_cohort")].iloc[0]
        best=ranking.iloc[0]
        st.caption(f"Frozen age-cohort benchmark MAE: {cohort['Mean absolute error']:,.0f} births. "
                   f"Lowest comparison MAE: {best['Mean absolute error']:,.0f} births ({best['Method']}). "
                   "Lower error is better. This evidence does not establish that the new central path is more accurate.")
    st.caption("All methods use identical eligible 1-, 3- and 5-year holdouts; horizon-specific errors receive equal weight. "
               "This tests a simplified frozen-rate cohort mechanism, not the exact current forecast. "
               "It uses rolling ACS exposure proxies, no deaths or migration, and a uniform age pattern within source bands, "
               "including the broad 35+ group modeled as 35–54. Historical suppression uses state proxies. "
               "Revised observations are not as-known-at-the-time releases. The 18-year horizon remains unvalidated.")
    st.download_button("Download matched historical comparisons",errors.to_csv(index=False).encode(),
                       f"{county}_fertility_historical_comparisons.csv","text/csv",key=key+"fertility_checks")
    st.download_button("Download comparison coverage and assumptions",json.dumps(coverage,indent=2).encode(),
                       f"{county}_fertility_comparison_coverage.json","application/json",key=key+"fertility_coverage")
