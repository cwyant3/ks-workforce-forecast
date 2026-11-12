"""Official program/occupation relationships, with no worker-supply allocation."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from textwrap import wrap

import pandas as pd
import streamlit as st

from dashboard.responsive import metric_columns
from geography import county_area_relations, select_area_observations
from dashboard.pressure_charts import (BLUE, GOLD, draw, evidence,
                                      horizontal, metric_card, program_titles)

CROSSWALK_URL = "https://nces.ed.gov/ipeds/cipcode/Files/CIP2020_SOC2018_Crosswalk.xlsx"
CROSSWALK_PATH = Path(__file__).resolve().parents[1] / "crosswalks" / "cip2020_soc2018.csv"


def normalize_crosswalk(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize the official CIP-SOC sheet only; exclude NO MATCH sentinels.

    Do not concatenate its reverse, added-match, or unmatched worksheets.
    This relation has no weights, enrollment, capacity, placement or supply counts.
    """
    columns = {"CIP2020Code":"program_code", "CIP2020Title":"program_title",
               "SOC2018Code":"occupation_code", "SOC2018Title":"occupation_title"}
    if not set(columns).issubset(raw):
        raise ValueError("Official crosswalk fields are missing")
    result = raw[list(columns)].rename(columns=columns).copy()
    result = result.fillna("").astype(str).apply(lambda s: s.str.strip())
    matched = (result.program_code.str.fullmatch(r"\d{2}\.\d{4}")
               & result.occupation_code.str.fullmatch(r"\d{2}-\d{4}")
               & result.program_title.ne("NO MATCH")
               & result.occupation_title.ne("NO MATCH")
               & result.program_code.ne("99.9999")
               & result.occupation_code.ne("99-9999"))
    return result.loc[matched].drop_duplicates(["program_code", "occupation_code"]).sort_values(
        ["program_code", "occupation_code"]).reset_index(drop=True)


@lru_cache(maxsize=1)
def load_crosswalk() -> pd.DataFrame:
    return pd.read_csv(CROSSWALK_PATH, dtype=str, keep_default_na=False)


def regional_occupation_rows(regional: pd.DataFrame, county: str) -> pd.DataFrame:
    """Keep detailed native SOCs at their published region, never county allocations."""
    if regional is None or regional.empty:
        return pd.DataFrame()
    required = {"geography_id", "occupation_code", "occupation_level"}
    if not required.issubset(regional):
        return regional.iloc[:0].copy()
    relation = county_area_relations()
    regions = relation.loc[relation.county_fips5.eq(str(county)), "projection_region_id"]
    mask = (regional.geography_id.isin(regions)
            & pd.to_numeric(regional.occupation_level, errors="coerce").eq(6)
            & regional.occupation_code.astype(str).str.fullmatch(r"\d{2}-\d{4}"))
    return regional.loc[mask].copy()


def occupation_pay_rows(frame: pd.DataFrame, county: str, occupation: str) -> pd.DataFrame:
    """Select only exact-SOC pay from the county's adopted native OEWS area."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    required = {"source_id", "occupation_code", "measure_id", "geography_id"}
    if not required.issubset(frame):
        return frame.iloc[:0].copy()
    rows = frame.loc[frame.source_id.eq("oews") & frame.occupation_code.eq(occupation)
                     & frame.measure_id.isin(["oews_median_annual_wage", "oews_mean_annual_wage"])]
    return select_area_observations(rows, [str(county)])


def related_program_awards(detail: pd.DataFrame, crosswalk: pd.DataFrame,
                           county: str, occupation: str) -> pd.DataFrame:
    """Filter by a set of related CIPs, never expand award rows through a join.

    Select the latest county collection once. Each institution/CIP/award level
    appears once; distinct award levels remain separate and are not unique people.
    """
    if detail is None or detail.empty:
        return pd.DataFrame()
    required = {"geography_id", "program_code", "reporting_unit", "award_level", "year", "completions"}
    if not required.issubset(detail):
        raise ValueError("Program detail lacks reporting identity or geography")
    selected = detail.loc[detail.geography_id.astype(str).eq(str(county))].copy()
    if selected.empty:
        return selected
    years = pd.to_numeric(selected.year, errors="coerce")
    selected = selected.loc[years.eq(years.max())].copy()
    mapping = crosswalk.loc[crosswalk.occupation_code.eq(occupation)]
    selected = selected.loc[selected.program_code.isin(set(mapping.program_code))].copy()
    if selected.empty:
        return selected
    identity = ["reporting_unit", "program_code", "award_level", "year"]
    # Repeated identical records must not inflate bars. Conflicts require review.
    selected = selected.drop_duplicates()
    if selected.duplicated(identity).any():
        raise ValueError("Conflicting duplicate program-award records; review source detail")
    titles = mapping.drop_duplicates("program_code").set_index("program_code").program_title
    selected["program_title"] = selected.program_code.map(titles).str.rstrip(".")
    counts = crosswalk.groupby("program_code").occupation_code.nunique()
    selected["related_occupation_count"] = selected.program_code.map(counts)
    return selected.sort_values(["program_code", "reporting_unit", "award_level"]).reset_index(drop=True)


def render_pathways(frame, detail, regional, county, key="outlook_pathways_"):
    """Render one exact SOC's regional outlook, area pay and related county awards."""
    rows = regional_occupation_rows(regional, county)
    if rows.empty:
        st.info("No published regional occupation outlook is linked to this county.")
        return None
    crosswalk = load_crosswalk()
    titles = rows.drop_duplicates("occupation_code").set_index("occupation_code").occupation_title.to_dict()
    codes = sorted(titles, key=lambda code: (titles[code], code))
    default = codes.index("51-4121") if "51-4121" in codes else 0
    occupation = st.selectbox("Occupation and related training", codes, index=default,
                              format_func=lambda code: f"{titles[code]} ({code})", key=key+"soc")
    st.caption("This occupation selector is independent of the industry selection above. No industry-to-occupation relationship is inferred.")
    chosen = rows.loc[rows.occupation_code.eq(occupation)]
    from geography import county_region
    _rid, _rname, _members = county_region(county)
    st.caption(f"Outlook: {_rname} Kansas projection region, 2022–2032. These regional estimates cover all {len(_members)} member counties and are not allocated to the selected county.")
    pay = occupation_pay_rows(frame, county, occupation)
    cols = metric_columns(3, key+"metrics")
    with cols[0]:
        metric_card(chosen, chosen[chosen.measure_id.eq("ks_occ_annual_openings")],
                    "Projected annual openings · region", key+"openings", color=BLUE)
    with cols[1]:
        metric_card(chosen, chosen[chosen.measure_id.eq("ks_occ_annual_change")],
                    "Annual employment change · region", key+"growth", color=BLUE)
    with cols[2]:
        wage = pay.loc[pay.measure_id.eq("oews_median_annual_wage")] if not pay.empty else pay
        metric_card(frame, wage, "Median annual pay · OEWS area", key+"pay", color=BLUE)
    if pay.empty:
        st.caption("Exact-occupation pay has not been adopted for this county's OEWS area. No broad occupation or county industry wage is substituted.")
    else:
        from geography import county_oews_area
        st.caption(f"Pay describes the {county_oews_area(county)[1]} OEWS area, not county pay or starting pay. It keeps its own reference period, separate from the regional projection.")
        if 'quality_note' in pay:
            for note in pay.loc[~pay.availability_status.eq('published'), 'quality_note'].dropna().unique():
                if note:
                    st.caption(note)
    st.caption("Projected annual openings include growth, occupation transfers and labor-force exits. They are not current vacancies; exits are not measured retirements.")
    mapping = crosswalk.loc[crosswalk.occupation_code.eq(occupation)]
    st.markdown("#### Related programs at institutions in the county")
    st.caption("The NCES crosswalk links instructional programs with occupations. A program may relate to several occupations; its awards are never allocated among them or added across occupations.")
    if mapping.empty:
        st.info("The official crosswalk has no matched program relationship for this occupation. This does not establish that training is unnecessary or unavailable.")
    else:
        try:
            related = related_program_awards(detail, crosswalk, county, occupation)
        except ValueError as exc:
            st.warning(str(exc))
            related = pd.DataFrame()
        if related.empty:
            st.info("No related county institution awards are reported in the adopted collection. This does not establish that no training providers or pathways exist.")
        else:
            labels = program_titles()
            related["program_title"] = related.program_code.map(labels).fillna(related.program_title)
            reported = related.copy()
            if "availability_status" in reported:
                reported.loc[~reported.availability_status.eq("published"), "completions"] = float("nan")
            if "reconciled" in reported:
                reported.loc[~reported.reconciled.eq(True), "completions"] = float("nan")
            totals = reported.groupby(["program_code", "program_title"], dropna=False).completions.agg(
                lambda values: values.sum() if values.notna().all() else float("nan"))
            totals = totals.dropna().sort_values(ascending=False).head(8)
            periods = sorted(related.reference_label.dropna().astype(str).unique()) if "reference_label" in related else []
            if periods:
                st.caption("Institution awards · "+", ".join(periods)+" · IPEDS. Each institution/program/award level is counted once in this view.")
            if not totals.empty:
                codes = [code for code, _ in totals.index]
                full_titles = [title for _, title in totals.index]
                wrapped = ["<br>".join(wrap(title, width=34)) for title in full_titles]
                fig = horizontal(totals.tolist(), codes,
                                 "Related program awards · not occupation supply", color=GOLD,
                                 height=max(250, sum((label.count("<br>")+1)*20+16 for label in wrapped)+65))
                fig.update_yaxes(type="category", tickmode="array", tickvals=codes, ticktext=wrapped)
                fig.update_traces(customdata=list(zip(full_titles, codes)),
                                  hovertemplate="%{customdata[0]}<br>CIP %{customdata[1]}<br>%{x:,.0f} awards<extra></extra>")
                draw(fig, key+"programs")
            columns = [c for c in ["institution_name", "program_title", "program_code", "credential_level", "completions", "reference_label", "availability_status", "imputation_status", "related_occupation_count"] if c in reported]
            with st.expander("Related institutions, credentials and reporting details"):
                st.dataframe(reported[columns].rename(columns={"institution_name":"Institution", "program_title":"Related program", "program_code":"CIP", "credential_level":"Credential", "completions":"Reported awards", "reference_label":"Academic year", "availability_status":"Availability", "imputation_status":"Reporting", "related_occupation_count":"Related occupations in crosswalk"}), hide_index=True, width="stretch")
            st.caption("Awards are credentials, not unique graduates, available workers, capacity or local placements. Institution location does not establish graduate residence. Annual openings and awards have different scope and periods; no vacancy gap or supply ratio is calculated.")
        with st.expander("Official related programs and source"):
            st.dataframe(mapping[["program_code", "program_title"]].drop_duplicates().rename(columns={"program_code":"CIP 2020", "program_title":"Program"}), hide_index=True, width="stretch")
            st.markdown(f"[NCES CIP 2020–SOC 2018 crosswalk]({CROSSWALK_URL}) · downloaded October 21, 2026. Relationships do not prescribe a required credential.")
    evidence(pd.concat([chosen, pay], ignore_index=True), "Occupation outlook and pay source records")
    return occupation
