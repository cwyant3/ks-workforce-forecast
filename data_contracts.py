"""Shared, lossless observation contract for the county pressure layer."""
from __future__ import annotations

import pandas as pd
import numpy as np

STATUS = {"published", "suppressed", "cannot_compute", "not_reported", "incomplete",
          "not_applicable", "access_failed"}
DEFAULTS = {
    "geography_type": "county", "location_basis": "", "industry_code": "",
    "industry_system": "", "occupation_code": "", "occupation_system": "",
    "ownership": "", "job_universe": "", "age_group": "", "sex_group": "",
    "reporting_unit": "", "program_code": "", "award_level": "",
    "seasonal_adjustment": "", "firm_size": "", "firm_age": "",
    "frequency": "", "availability_status": "published", "source_status": "",
    "method_type": "observed estimate", "source_url": "", "release_id": "",
    "reference_label": "", "coverage_note": "", "quality_note": "",
    "moe_confidence_level": np.nan, "preliminary": False,
}
KEY = ["source_id", "measure_id", "geography_type", "geography_id", "location_basis",
       "industry_code", "occupation_code", "ownership", "job_universe", "age_group",
       "sex_group", "reporting_unit", "program_code", "award_level",
       "seasonal_adjustment", "firm_size", "firm_age",
       "period_start", "period_end", "frequency"]
REQUIRED = ["source_id", "measure_id", "geography_id", "period_start", "period_end", "estimate", "unit"]


def normalize_observations(df: pd.DataFrame, source_id: str | None = None,
                           defaults: dict | None = None) -> pd.DataFrame:
    """Add metadata without inferring missing estimates or population universes.

    Extra source columns are retained, including disclosure and imputation flags.
    Suppression is never resolved by numeric filling.
    """
    result = df.copy()
    settings = {**DEFAULTS, **(defaults or {})}
    if source_id is not None:
        settings["source_id"] = source_id
    for column, value in settings.items():
        if column not in result:
            result[column] = value
        elif isinstance(value, str):
            result[column] = result[column].fillna(value)
    for column in REQUIRED:
        if column not in result:
            if result.empty:
                result[column] = pd.Series(dtype="float64" if column == "estimate" else "string")
            else:
                raise ValueError(f"Observation field missing: {column}")
    for column in [*KEY, "unit", "source_status", "source_url", "release_id"]:
        result[column] = result[column].astype("string").fillna("")
    for column in ["estimate", "moe", "rse", "moe_confidence_level"]:
        if column not in result:
            result[column] = np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")
    unavailable = ~result["availability_status"].eq("published")
    result.loc[unavailable, ["estimate", "moe", "rse"]] = np.nan
    return result


def validate_observations(df: pd.DataFrame) -> list[str]:
    missing = set(REQUIRED + KEY + ["availability_status"]) - set(df.columns)
    if missing:
        return [f"Missing fields: {sorted(missing)}"]
    errors = []
    if df.empty:
        return ["Observation table is empty"]
    if not df["availability_status"].isin(STATUS).all():
        errors.append("Unknown availability status")
    if df.duplicated(KEY).any():
        errors.append("Duplicate observation identity")
    county = df["geography_type"].eq("county")
    if not df.loc[county, "geography_id"].astype(str).str.fullmatch(r"\d{5}").all():
        errors.append("County identifiers must be five-digit strings")
    published = df["availability_status"].eq("published")
    values = pd.to_numeric(df["estimate"], errors="coerce")
    if not np.isfinite(values[published]).all():
        errors.append("Published observations require finite estimates")
    if values[~published].notna().any():
        errors.append("Unavailable observations must not expose a numeric estimate")
    starts = pd.to_datetime(df["period_start"], errors="coerce")
    ends = pd.to_datetime(df["period_end"], errors="coerce")
    if starts.isna().any() or ends.isna().any() or (starts > ends).any():
        errors.append("Invalid observation period")
    if df["source_url"].astype(str).str.contains(r"[?&](?:key|registrationkey)=", regex=True, case=False).any():
        errors.append("Credentials must not appear in source URLs")
    return errors


def latest_observations(df: pd.DataFrame, *, usable_only: bool = False) -> pd.DataFrame:
    """Latest per exact measure/universe, never per whole source file."""
    if df.empty:
        return df.copy()
    selected = df[df.availability_status.eq("published")] if usable_only else df
    grain = [col for col in KEY if col not in ("period_start", "period_end")]
    return selected.sort_values(["period_end", "period_start"]).drop_duplicates(grain, keep="last")
