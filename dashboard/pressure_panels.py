"""County pressure views: observations, uncertainty, geography and provenance."""
from __future__ import annotations

import pandas as pd
import streamlit as st
from data_contracts import latest_observations

PILOT = {"20015", "20035", "20077", "20079", "20095", "20173", "20191"}
INDUSTRIES = {"22": "Utilities", "23": "Construction", "31-33": "Manufacturing",
              "51": "Information", "54": "Professional, scientific, and technical services",
              "62": "Health care and social assistance", "71": "Arts, entertainment, and recreation",
              "72": "Accommodation and food services", "81": "Other services (except public administration)",
              "10": "All industries", "00": "All industries"}


def county_slice(frame, county):
    if frame is None or frame.empty:
        return pd.DataFrame()
    return frame[frame.geography_type.eq("county") & frame.geography_id.eq(county)].copy()


def show_observations(frame, key, *, title=None):
    if title:
        st.markdown(f"#### {title}")
    if frame.empty:
        st.info("No validated observations for this selection. Missing data do not mean zero activity.")
        return
    latest = latest_observations(frame)
    latest = latest.assign(_published=latest.availability_status.eq("published")).sort_values(
        ["_published", "source_id", "measure_id"], ascending=[False, True, True]).drop(columns="_published")
    columns = ["measure_id", "reporting_unit", "program_code", "award_level", "occupation_code", "estimate", "unit", "reference_label", "period_end",
               "availability_status", "moe", "rse", "source_status", "quality_note", "coverage_note"]
    view = latest[[c for c in columns if c in latest]].rename(columns={
        "measure_id": "Measure", "estimate": "Estimate", "unit": "Unit",
        "reference_label": "Reference period", "period_end": "Period end",
        "availability_status": "Availability", "moe": "Margin of error", "rse": "RSE (%)",
        "occupation_code": "SOC code", "reporting_unit": "Institution ID", "program_code": "CIP code", "award_level": "Award level",
        "source_status": "Source flag", "quality_note": "Quality", "coverage_note": "Coverage"})
    view["Measure"] = view["Measure"].str.replace("_", " ").str.replace("qwi", "QWI").str.replace("qcew", "QCEW").str.replace("acs", "ACS").str.replace("laus", "LAUS").str.replace("oews", "OEWS").str.replace("trailing4q", "previous four quarters").str.replace("yoy pct", "change from same quarter last year (%)").str.replace("ks occ", "Regional occupation")
    view = view.loc[:, view.notna().any() & ~view.apply(lambda col: col.astype(str).eq("").all())]
    if "Estimate" in view:
        view["Estimate"] = view["Estimate"].map(lambda v: f"{v:,.2f}".rstrip("0").rstrip(".") if pd.notna(v) else "Unavailable")
    if "Availability" in view:
        view["Availability"] = view["Availability"].str.replace("_", " ").str.capitalize()
    st.dataframe(view, hide_index=True, width="stretch")
    if latest.estimate.isna().any():
        st.caption("Unavailable and suppressed estimates have no numeric value. Earlier published observations, where present, are in the history below.")
    with st.expander("Observation history and source details"):
        st.dataframe(frame.sort_values("period_end", ascending=False), hide_index=True, width="stretch")
    st.download_button("Download observations with provenance", frame.to_csv(index=False).encode(),
                       file_name=f"{key}.csv", mime="text/csv", key=f"download_{key}")


def render_demand(frame, county):
    st.markdown("### County employment and hiring evidence")
    st.caption("Workplace jobs and hiring flows. Hires are not vacancies, and separations are not measured retirements.")
    rows = county_slice(frame, county)
    if rows.empty:
        show_observations(rows, "county_demand")
        return
    rows = rows[rows.source_id.str.contains("qwi|qcew", case=False, regex=True)]
    codes = sorted(c for c in rows.industry_code.unique() if c)
    if codes:
        code = st.selectbox("Local industry", codes, index=codes.index("31-33") if "31-33" in codes else 0,
                            format_func=lambda v: INDUSTRIES.get(v, v), key="pressure_industry")
        rows = rows[rows.industry_code.eq(code)]
    show_observations(rows, "county_demand")
    st.caption("A release can contain different usable periods for different measures. Compare matched periods and universes; no combined shortage score is calculated.")
    st.markdown("#### Occupation wages and employment in the surrounding area")
    from geography import county_oews_area, select_area_observations
    area_code, area_name, members = county_oews_area(county)
    if area_code:
        area = select_area_observations(frame[frame.source_id.eq("oews")], [county])
        area = area[area.availability_status.eq("published")]
        st.caption(f"{area_name} OEWS area, May 2025 (Kansas members: {', '.join(members)}). Area employment is not allocated to counties. Employment and mean wages retain their own relative standard errors; median wages do not inherit the mean-wage RSE.")
        if not area.empty:
            titles = area.drop_duplicates("occupation_code").set_index("occupation_code").occupation_title.to_dict()
            occupation = st.selectbox("Area occupation", sorted(titles), format_func=lambda v: f"{v} · {titles[v]}", key="oews_occupation")
            area = area[area.occupation_code.eq(occupation)]
        show_observations(area, "oews_area")
    else:
        st.info("Occupation pay for this county's OEWS area has not been adopted in this release (adopted areas: Wichita and Kansas City).")


def render_supply(frame, county, annual_laus=None):
    rows = county_slice(frame, county)
    if not rows.empty:
        rows = rows[rows.source_id.str.contains("acs|laus", case=False, regex=True)]
    st.markdown("### Observed resident labor force")
    if annual_laus is not None:
        from dashboard.demographic_chart import render_demographic_pipeline
        render_demographic_pipeline(county, "explorer_resident_pipeline_")
    st.caption("ACS estimates describe their stated age universe and five-year window. Monthly LAUS measures residents and is not skill-specific availability. ACS MOEs are percentage points for rates and persons for counts.")
    show_observations(rows, "county_supply")


def render_training(frame, detail, county):
    st.markdown("### Credentials awarded by local institutions")
    st.caption("Awards are not unique people, local placements or newly available workers. Provider location does not establish graduate residence. CIP-to-occupation relations are contextual, not additive supply allocations.")
    rows = county_slice(frame, county)
    if not rows.empty:
        rows = rows[rows.source_id.str.contains("ipeds", case=False)]
    if not rows.empty:
        show_observations(rows[rows.reporting_unit.eq("")], "county_training")
    else:
        st.info("No validated credential observations in this county selection.")
    if detail is not None and not detail.empty:
        for col in ["geography_id", "county_fips5", "county_fips"]:
            if col in detail:
                chosen = detail[detail[col].astype(str).str.zfill(5).eq(county)]
                if not chosen.empty:
                    providers = sorted(chosen.institution_name.unique())
                    provider = st.selectbox("Institution", ["All reported institutions", *providers], key="pressure_provider")
                    if provider != "All reported institutions":
                        chosen = chosen[chosen.institution_name.eq(provider)]
                    programs = sorted(chosen.program_code.unique())
                    program = st.selectbox("Program (CIP code)", ["All reported programs", *programs], key="pressure_program")
                    if program != "All reported programs":
                        chosen = chosen[chosen.program_code.eq(program)]
                    columns = ["institution_name", "program_code", "credential_level", "completions", "reference_label", "response_flag_status", "imputation_status", "reconciled"]
                    view = chosen[columns].rename(columns={"institution_name":"Institution", "program_code":"CIP code", "credential_level":"Credential", "completions":"Awards", "reference_label":"Academic year", "response_flag_status":"Response", "imputation_status":"Reporting", "reconciled":"Totals reconciled"})
                    st.dataframe(view, hide_index=True, width="stretch")
                    st.download_button("Download program detail", chosen.to_csv(index=False).encode(),
                                       "credentials_program_detail.csv", "text/csv", key="training_detail")
                break
    st.caption("County apprenticeship and graduate placement feeds remain unavailable until their geography, suppression and access gates are resolved.")


def render_regions(regional, county, key="regional_soc"):
    if regional is None or regional.empty:
        return
    st.markdown("### Published regional occupation context")
    from geography import county_region
    region_id, region_name, members = county_region(county)
    if not region_id or not regional.geography_id.astype(str).eq(region_id).any():
        st.info("No published regional occupation projection is linked to this county.")
        return
    rows = regional[regional.geography_id.astype(str).eq(region_id)].copy()
    st.caption(f"{region_name} projection region: {', '.join(members)}. Regional projections retain their own 2022–2032 vintage; these are not county estimates.")
    if "occupation_code" in rows:
        options = sorted(rows.occupation_code.dropna().unique())
        titles = rows.drop_duplicates("occupation_code").set_index("occupation_code").get("occupation_title", pd.Series(dtype=str)).to_dict()
        choice = st.selectbox("Regional occupation (SOC)", options,
                              format_func=lambda v: f"{v} · {titles.get(v, '')}", key=key) if options else None
        if choice is not None:
            rows = rows[rows.occupation_code.eq(choice)]
    show_observations(rows, key + "_observations")


def render_commuting(frame, county, flows=None):
    st.markdown("### Commuting and the labor shed")
    rows = county_slice(frame, county)
    if not rows.empty:
        rows = rows[rows.source_id.str.contains("lodes", case=False)]
    show_observations(rows, "county_commuting")
    if flows is not None and not flows.empty:
        from geography import inbound_labor_shed
        coverage = st.select_slider("Share of existing workplace primary jobs to describe", options=[70, 80, 90], value=80, key="shed_share")
        shed = inbound_labor_shed(flows, [county], coverage/100)
        if not shed.empty:
            st.markdown(f"#### Origin counties covering at least {coverage}% of primary jobs")
            display = shed.rename(columns={"h_county_fips":"Home county FIPS", "jobs":"Primary jobs", "share":"Share", "cumulative_share":"Cumulative share"})
            st.dataframe(display, hide_index=True, width="stretch", column_config={"Share": st.column_config.NumberColumn(format="percent"), "Cumulative share": st.column_config.NumberColumn(format="percent")})
            st.download_button("Download observed labor shed", shed.to_csv(index=False).encode(), "observed_labor_shed.csv", "text/csv", key="shed_download")
    st.caption("LODES describes jobs and existing home-to-work connections, not recruitable people. Primary jobs (JT01) and all jobs (JT00) are separate universes. Inbound coverage does not establish complete outbound coverage.")


def render_quality(manifest):
    st.markdown("### Source releases and coverage")
    if not manifest:
        st.info("No validated county pressure release has been adopted.")
        return
    st.caption(f"Adopted release: {manifest['release_id']} · Created {manifest['created_utc']}")
    st.json(manifest.get("scope", {}), expanded=False)
    st.dataframe(pd.DataFrame(manifest.get("sources", [])), hide_index=True, width="stretch")
    coverage = pd.DataFrame(manifest.get("coverage", []))
    if not coverage.empty:
        coverage["statuses"] = coverage.statuses.map(str)
        st.dataframe(coverage, hide_index=True, width="stretch")
    st.caption("Latest usable dates are measure-specific. Status counts describe retained observations, not a claim that every county or industry has published data.")
    st.download_button("Download release manifest", __import__("json").dumps(manifest, indent=2),
                       "county_pressure_manifest.json", "application/json", key="pressure_manifest")
