"""A county narrative connecting observed conditions, outlook and response choices."""
from __future__ import annotations

import streamlit as st

from dashboard.responsive import metric_columns
from dashboard.pressure_charts import (
    BLUE, TEAL, INDUSTRIES, available, current, series, metric_card,
    workforce, demand, training, movement, kc_metro,
)
from dashboard.outlook_forecast import render_forecast, render_scenarios
from dashboard.training_pathways import render_pathways


def current_reading(frame, county, industry):
    """Describe measured levels without turning context into a shortage estimate."""
    growth = current(series(frame, "qcew", "qcew_employment_yoy_pct", county, industry))
    participation = current(series(frame, "acs_s2301", "labor_force_participation_rate_16_plus", county))
    unemployment = current(series(frame, "laus", "unemployment_rate", county))
    supply = []
    if available(participation):
        supply.append(f"{participation.estimate:.1f}% of residents age 16+ participate in the labor force ({participation.reference_label}).")
    if available(unemployment):
        supply.append(f"The resident unemployment rate is {unemployment.estimate:.1f}% ({unemployment.reference_label}).")
    supply.append("These describe different resident populations and periods; they do not identify who is ready for a particular job.")
    if available(growth):
        direction = "increased" if growth.estimate > 0 else "decreased" if growth.estimate < 0 else "was unchanged"
        change = f" by {abs(growth.estimate):.1f}%" if growth.estimate else ""
        employer = (f"{INDUSTRIES.get(industry, industry)} employment {direction}{change} from a year earlier "
                    f"({growth.reference_label}). This is a change in filled jobs; hiring and pay provide additional context.")
    else:
        employer = "A published current employment-growth observation is unavailable for this county and industry. The page does not infer a trend from missing data."
    if not available(participation) and not available(unemployment):
        supply.insert(0, "Current resident participation and unemployment observations are unavailable for this selection.")
    return " ".join(supply), employer


def render_outlook(frame, detail, flows, regional, proj, effective, sectors, summary,
                   county, county_name, names, annual_laus=None, resident_flows=None, metro=None):
    st.markdown("""<style>
    @media (max-width: 850px) {
      [data-testid="stHorizontalBlock"] {flex-wrap: wrap;}
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        width: 100% !important; flex: 1 1 100% !important; min-width: 0 !important;
      }
    }
    </style>""", unsafe_allow_html=True)
    st.caption("WORKFORCE OUTLOOK · COUNTY EVIDENCE AND FORECASTS")
    st.title(county_name)
    st.write("Can this community support its future employment needs, and where can we act?")
    industry = st.selectbox("Industry focus", list(INDUSTRIES),
                            index=list(INDUSTRIES).index("31-33"),
                            format_func=lambda x: INDUSTRIES[x], key="outlook_industry")
    st.caption("County selection governs this page. Industry applies to employer activity and compatible employment forecasts. "
               "Resident supply covers all industries. Occupations have a separate selection; regional outlooks and area wages retain their native geography.")
    st.markdown("**Read the story:** Outlook → Current conditions → Occupations and training → Regional reach → Scenarios")
    st.caption("Teal: resident supply · Blue: employer activity · Gold: training and scenario choices. Each chart states its period and scope.")

    st.markdown("### 1. Where are we headed?")
    render_forecast(proj, effective, sectors, summary, county, industry)

    st.divider()
    st.markdown("### 2. What is happening now?")
    st.caption("OBSERVED · Latest adopted observations, with each source's own reference period. These observations explain context; they do not automatically recalibrate the forecasts above.")
    supply_text, employer_text = current_reading(frame, county, industry)
    supply_col, employer_col = metric_columns(2, "outlook-conditions")
    with supply_col:
        st.markdown("#### Resident supply conditions")
        st.write(supply_text)
        for src, measure, title in [
            ("laus", "labor_force", "Residents in the labor force"),
            ("acs_s2301", "labor_force_participation_rate_16_plus", "Resident participation"),
            ("laus", "unemployment_rate", "Resident unemployment rate"),
        ]:
            metric_card(frame, series(frame, src, measure, county), title,
                        "outlook_now_"+measure, color=TEAL)
    with employer_col:
        st.markdown("#### Employer conditions")
        st.write(employer_text)
        for measure, title in [
            ("qcew_employment", "Workplace jobs in the selected industry"),
            ("qcew_employment_yoy_pct", "Employment change from a year earlier"),
            ("qcew_weekly_wage", "Average weekly industry pay"),
        ]:
            metric_card(frame, series(frame, "qcew", measure, county, industry), title,
                        "outlook_now_"+measure, color=BLUE)
    with st.expander("Explore participation, county comparisons and hiring"):
        resident_tab, employer_tab = st.tabs(["Resident workforce", "Employment and hiring"])
        with resident_tab:
            workforce(frame, county, "outlook_detail_supply_")
        with employer_tab:
            demand(frame, county, industry, names, "outlook_detail_employer_")

    from dashboard.demographic_chart import render_demographic_pipeline
    render_demographic_pipeline(county, "outlook_resident_pipeline_")

    st.divider()
    st.markdown("### 3. Which occupations and training pathways matter?")
    st.write("Use the regional occupation outlook to focus the training conversation. Program awards show existing education activity; employer validation is still needed to establish hiring needs and skill requirements.")
    render_pathways(frame, detail, regional, county)

    st.divider()
    st.markdown("### 4. What training activity and regional reach already exist?")
    st.write("Training institutions and established commuting connections help frame a response. Awards may include people already working, and incoming commuters already hold local jobs.")
    for col, src, measure, title in zip(metric_columns(2, "outlook-capacity"), ["ipeds", "lodes"],
                                       ["credentials_awarded", "pct_workers_imported"],
                                       ["All reported county credential awards", "Existing incoming commuter share"]):
        with col:
            metric_card(frame, series(frame, src, measure, county), title, "outlook_capacity_"+measure)
    with st.expander("Explore all programs, institutions and commuting origins"):
        train_tab, commute_tab = st.tabs(["Training capacity", "Recruiting geography"])
        with train_tab:
            training(frame, detail, county, "outlook_all_training_", industry)
        with commute_tab:
            movement(frame, flows, county, names, "outlook_all_movement_", resident_flows)
    kc_metro(metro, county, industry, "outlook_")

    st.divider()
    st.markdown("### 5. What could change the outlook?")
    render_scenarios(proj, effective, county)
    st.markdown("#### Turn the evidence into a next step")
    st.markdown(
        "- **Validate the employer need:** distinguish growth hiring, replacement hiring and incumbent-worker skill needs.\n"
        "- **Review the pathway:** confirm program capacity, occupational fit, completion and local placement before proposing expansion.\n"
        "- **Test regional access:** use commuting origins to investigate transport, scheduling and recruiting reach.\n"
        "- **Document assumptions:** compare scenarios with the baseline and identify what evidence would support each change."
    )
    with st.expander("Coverage, definitions and source records"):
        st.write("Observed residents, workplace jobs, annual projected openings and institutional awards have different denominators. "
                 "No subtraction on this page produces a validated worker shortage. ACS sampling margins, model prediction bands and scenario assumptions remain distinct.")
        st.write("County demand and regional occupation evidence is shown wherever the adopted release covers the county. Missing or suppressed measures stay unavailable. "
                 "Forecasts use their existing baselines; a newer observation is not silently treated as a model refresh. Sources are included in the detailed panels and downloadable observations.")
    st.download_button("Download county observations with sources",
                       frame[frame.geography_id.eq(county)].to_csv(index=False).encode(),
                       f"{county}_workforce_observations.csv", "text/csv", key="outlook_download")
