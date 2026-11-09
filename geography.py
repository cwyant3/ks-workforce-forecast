"""Versioned geography relations. Area observations must never be summed by county join."""
from pathlib import Path
import pandas as pd

SOUTH_CENTRAL_COUNTIES = ("20015", "20035", "20077", "20079", "20095", "20173", "20191")
WICHITA_OEWS_COUNTIES = ("20015", "20079", "20173", "20191")
GEOGRAPHY_VERSION = "2026-10-20"


def county_area_relations() -> pd.DataFrame:
    return pd.read_csv(Path(__file__).parent / "crosswalks/county_areas_20261020.csv", dtype=str).fillna("")


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
