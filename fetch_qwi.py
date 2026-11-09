"""County QWI observations with source flags and explicit unreported counties.

QWI is employment/hiring/earnings evidence, not vacancies or retirement counts.
Network requests are explicit; importing this module never refreshes data.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests

QWI_URL = "https://api.census.gov/data/timeseries/qwi/sa"
MEASURES = {"Emp": ("qwi_employment", "jobs"),
            "HirA": ("qwi_hires", "hires"),
            "Sep": ("qwi_separations", "separations"),
            "EarnS": ("qwi_stable_earnings", "USD/month")}
DIMENSIONS = ["geography_id", "industry_code", "ownership", "job_universe",
              "age_group", "sex_group", "seasonal_adjustment", "firm_size", "firm_age"]


def qwi_query(state_fips: str, period: str, industry: str = "31-33",
              ownership: str = "A05", age_group: str = "A00", sex_group: str = "0") -> dict:
    return {"get": ",".join([*MEASURES, *("s" + m for m in MEASURES)]),
            "for": "county:*", "in": "state:" + state_fips.zfill(2), "time": period,
            "sex": sex_group, "agegrp": age_group, "industry": industry,
            "ownercode": ownership, "seasonadj": "U", "firmsize": "0", "firmage": "0"}


def parse_qwi_response(payload: list, counties: list[str], *, period: str,
                       state_fips: str = "20", industry: str = "31-33",
                       ownership: str = "A05", age_group: str = "A00",
                       sex_group: str = "0", release_id: str = "unspecified") -> pd.DataFrame:
    """Parse one requested quarter/slice; preserve missing counties and raw flags.

    Only status 1 is treated as published in this initial adapter. Other codes
    remain explicit and conservative until their interpretation is reviewed.
    """
    from data_contracts import normalize_observations
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        raise ValueError("QWI response is not the expected header/rows JSON array")
    required = {*MEASURES, *("s" + m for m in MEASURES), "state", "county", "time"}
    if not required.issubset(payload[0]):
        raise ValueError("QWI response missing measures, source flags, or geography/period")
    data = pd.DataFrame(payload[1:], columns=payload[0])
    if not data.empty:
        if not data["time"].eq(period).all() or not data["state"].eq(state_fips.zfill(2)).all():
            raise ValueError("QWI response differs from requested state/period")
        expected = {"industry": industry, "ownercode": ownership, "agegrp": age_group,
                    "sex": sex_group, "seasonadj": "U", "firmsize": "0", "firmage": "0"}
        for key, value in expected.items():
            if key not in data or not data[key].eq(value).all():
                raise ValueError("QWI response differs from requested dimension: " + key)
        if data["county"].duplicated().any():
            raise ValueError("QWI response contains multiple rows per requested county/slice")
    records = {state_fips.zfill(2) + str(r["county"]).zfill(3): r for r in data.to_dict("records")}
    quarter = pd.Period(period, freq="Q")
    url = QWI_URL + "?" + urlencode(qwi_query(state_fips, period, industry, ownership, age_group, sex_group))
    rows = []
    for county in counties:
        fips = str(county).zfill(5) if len(str(county)) > 3 else state_fips.zfill(2) + str(county).zfill(3)
        r = records.get(fips)
        for variable, (measure, unit) in MEASURES.items():
            flag = str(r.get("s" + variable)) if r is not None else "absent"
            value = pd.to_numeric(r.get(variable), errors="coerce") if r is not None else float("nan")
            status = ("not_reported" if r is None else "suppressed" if flag == "5"
                      else "cannot_compute" if flag == "-1" else "published"
                      if flag == "1" and pd.notna(value) and value >= 0 else "cannot_compute")
            rows.append(dict(geography_id=fips, measure_id=measure, estimate=value if status == "published" else None,
                             unit=unit, source_status=flag, availability_status=status,
                             industry_code=industry, ownership=ownership,
                             age_group=age_group, sex_group=sex_group,
                             period_start=str(quarter.start_time.date()), period_end=str(quarter.end_time.date()),
                             source_url=url, reference_label=period))
    return normalize_observations(pd.DataFrame(rows), source_id="qwi", defaults={
        "geography_type": "county", "location_basis": "workplace", "frequency": "quarterly",
        "job_universe": "UI-covered jobs", "seasonal_adjustment": "U", "firm_size": "0", "firm_age": "0",
        "release_id": release_id, "method_type": "published estimate"})


def fetch_qwi(counties: list[str], *, period: str, state_fips: str = "20",
              industry: str = "31-33", ownership: str = "A05", age_group: str = "A00",
              sex_group: str = "0", api_key: str | None = None,
              release_id: str = "unspecified", session=None) -> pd.DataFrame:
    """Fetch a requested slice. Credentials never enter provenance or exception URLs."""
    params = qwi_query(state_fips, period, industry, ownership, age_group, sex_group)
    if api_key:
        params["key"] = api_key
    try:
        response = (session or requests).get(QWI_URL, params=params, timeout=90)
        if response.status_code != 200:
            raise ValueError("HTTP " + str(response.status_code))
        payload = response.json()  # rejects Missing Key HTML even with HTTP 200
    except (requests.RequestException, ValueError):
        raise RuntimeError("QWI request failed or returned a non-data response; credentials omitted") from None
    return parse_qwi_response(payload, counties, period=period, state_fips=state_fips,
                              industry=industry, ownership=ownership, age_group=age_group,
                              sex_group=sex_group, release_id=release_id)


def latest_valid_by_measure(frame: pd.DataFrame) -> pd.DataFrame:
    """Select per-measure valid dates, keeping latest unavailable row if none exist."""
    if frame.empty:
        return frame.copy()
    keys = [c for c in ["source_id", "measure_id", *DIMENSIONS] if c in frame]
    rows = []
    for _, group in frame.groupby(keys, dropna=False):
        ordered = group.sort_values("period_end")
        valid = ordered[(ordered["availability_status"] == "published") & ordered["estimate"].notna()]
        rows.append((valid if not valid.empty else ordered).iloc[-1])
    return pd.DataFrame(rows).reset_index(drop=True)
