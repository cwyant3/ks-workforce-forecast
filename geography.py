"""Versioned geography relations. Area observations must never be summed by county join."""
from functools import lru_cache
from pathlib import Path
import pandas as pd

SOUTH_CENTRAL_COUNTIES = ("20015", "20035", "20077", "20079", "20095", "20173", "20191")
WICHITA_OEWS_COUNTIES = ("20015", "20079", "20173", "20191")
# Kansas members of the bistate Kansas City, MO-KS OEWS area (BLS May 2025 definitions).
KANSAS_CITY_OEWS_COUNTIES = ("20091", "20103", "20107", "20121", "20209")
# Adopted OEWS areas: code -> (name, Kansas member counties).
# May 2025 BLS definitions, verified 2026-11-12; nonmetro API code 2000006 uses type M.
OEWS_AREAS = {'48620': ('Wichita, KS', ('20015', '20079', '20173', '20191')),
 '28140': ('Kansas City, MO-KS', ('20091', '20103', '20107', '20121', '20209')),
 '45820': ('Topeka, KS', ('20085', '20087', '20139', '20177', '20197')),
 '29940': ('Lawrence, KS', ('20045',)),
 '31740': ('Manhattan, KS', ('20061', '20149', '20161')),
 '41140': ('St. Joseph, MO-KS', ('20043',)),
 '27900': ('Joplin, MO-KS', ('20021',)),
 '2000006': ('Kansas nonmetropolitan area',
             ('20001', '20003', '20005', '20007', '20009', '20011', '20013', '20017', '20019', '20023',
              '20025', '20027', '20029', '20031', '20033', '20035', '20037', '20039', '20041', '20047',
              '20049', '20051', '20053', '20055', '20057', '20059', '20063', '20065', '20067', '20069',
              '20071', '20073', '20075', '20077', '20081', '20083', '20089', '20093', '20095', '20097',
              '20099', '20101', '20105', '20109', '20111', '20113', '20115', '20117', '20119', '20123',
              '20125', '20127', '20129', '20131', '20133', '20135', '20137', '20141', '20143', '20145',
              '20147', '20151', '20153', '20155', '20157', '20159', '20163', '20165', '20167', '20169',
              '20171', '20175', '20179', '20181', '20183', '20185', '20187', '20189', '20193', '20195',
              '20199', '20201', '20203', '20205', '20207'))}
GEOGRAPHY_VERSION = "2026-11-12"


_CENSUS_COUNTY_NAMES = Path(__file__).parent / "data/demographic_inputs/births_co-est2025-alldata.utf8.csv"


@lru_cache(maxsize=1)
def _us_county_names() -> dict:
    """Five-digit FIPS -> (county name, state name) for every US county, from the Census 2025 estimates file."""
    table = pd.read_csv(_CENSUS_COUNTY_NAMES, usecols=["SUMLEV", "STATE", "COUNTY", "STNAME", "CTYNAME"],
                        dtype=str, encoding="utf-8-sig")
    table = table[table.SUMLEV.eq("050")]
    return {s.zfill(2)+c.zfill(3): (n, st) for s, c, n, st in zip(table.STATE, table.COUNTY, table.CTYNAME, table.STNAME)}


def county_label(fips, kansas_names=None) -> str:
    """Readable county label. Kansas counties keep the dashboard's name; other states add a postal code
    (e.g. 'Jackson County, MO') so border-county origins are never shown as a bare FIPS code."""
    fips = str(fips).zfill(5)
    if fips.startswith("20") and kansas_names and kansas_names.get(fips):
        return kansas_names[fips]
    found = _us_county_names().get(fips)
    if not found:
        return f"County FIPS {fips}"
    from fetch_lodes import FIPS_TO_ABBR
    name, state = found
    return name if fips.startswith("20") else f"{name}, {FIPS_TO_ABBR.get(fips[:2], state).upper()}"


def county_area_relations() -> pd.DataFrame:
    return pd.read_csv(Path(__file__).parent / "crosswalks/county_areas_20261020.csv", dtype=str).fillna("")


def county_region(county) -> tuple[str, str, list[str]]:
    """(region_id, region_name, member county names) for a five-digit county FIPS; blanks if unmapped."""
    relations = county_area_relations()
    row = relations.loc[relations.county_fips5.eq(str(county))]
    if row.empty:
        return "", "", []
    region_id = row.iloc[0].projection_region_id
    members = relations.loc[relations.projection_region_id.eq(region_id)]
    return region_id, row.iloc[0].projection_region, sorted(members.county_name)


def county_oews_area(county) -> tuple[str, str, list[str]]:
    """(area code, area name, Kansas member county names) for an adopted OEWS area; blanks otherwise."""
    relations = county_area_relations()
    row = relations.loc[relations.county_fips5.eq(str(county))]
    code = row.iloc[0].oews_area if not row.empty else ""
    if code not in OEWS_AREAS:
        return "", "", []
    members = relations.loc[relations.oews_area.eq(code), "county_name"]
    return code, OEWS_AREAS[code][0], sorted(members)


def select_area_observations(observations: pd.DataFrame, counties, area_column="oews_area"):
    """Select each native area once, even when several selected counties belong to it."""
    relations = county_area_relations()
    areas = set(relations.loc[relations.county_fips5.isin(counties), area_column]) - {""}
    return observations.loc[observations.geography_id.isin(areas)].copy()


def inbound_labor_shed(flows, workplace_counties, coverage=0.8):
    """Observed JT01 origin counties reaching a chosen cumulative share; not extra supply."""
    if not 0 < coverage <= 1:
        raise ValueError("coverage must lie in (0, 1]")
    sub = flows.loc[flows.w_county_fips.isin(workplace_counties)].copy()
    if sub.empty:
        return pd.DataFrame(columns=["h_county_fips", "jobs", "share", "cumulative_share"])
    if set(sub.job_type) != {"JT01"} or not sub.inbound_complete.all():
        raise ValueError("A labor shed requires complete main+aux primary-job coverage")
    if sub.year.nunique() != 1:
        raise ValueError("Select one reference year for a labor shed")
    out = sub.groupby("h_county_fips", as_index=False).jobs.sum()
    out = out.sort_values(["jobs", "h_county_fips"], ascending=[False, True]).reset_index(drop=True)
    total = out.jobs.sum()
    if total <= 0:
        return out.assign(share=float("nan"), cumulative_share=float("nan")).iloc[:0]
    out["share"] = out.jobs / total
    out["cumulative_share"] = out.share.cumsum()
    # Include the row that crosses the threshold, with deterministic FIPS tie order.
    return out.loc[out.cumulative_share.shift(fill_value=0) < coverage].copy()
