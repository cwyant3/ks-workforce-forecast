"""
fetch_acs.py
Fetches ACS 5-year age-by-sex (B01001) data for all counties in a given state.
Designed to be state-agnostic; defaults to Kansas (FIPS 20).

Census API key is optional but recommended for production use.
Sign up free at: https://api.census.gov/data/key_signup.html
"""

import requests
import time
import pandas as pd
from pathlib import Path

ACS_YEARS = [2015, 2019, 2021, 2024]
ACS_BASE = "https://api.census.gov/data/{year}/acs/acs5"

# B01001 — Sex by Age variable codes
MALE_VARS = [f"B01001_{str(i).zfill(3)}E" for i in range(3, 26)]   # _003–_025
FEMALE_VARS = [f"B01001_{str(i).zfill(3)}E" for i in range(27, 50)] # _027–_049
TOTAL_POP = "B01001_001E"

# B23001 — Sex by Age by Employment Status for the Population 16 Years and Over.
# For the 18-64 workforce denominator, the 16-19 band is weighted at 0.5 to
# approximate ages 18-19, then all ACS age bands from 20-64 are included.
#
# B23001 repeats a 7-variable block per sex×age band:
#   +0 band total · +1 In labor force · +2 In labor force: In Armed Forces
#   +3 In labor force: Civilian · +4 Civilian Employed · +5 Civilian Unemployed
#   +6 Not in labor force
# So civilian labor force = band_total + 3 and armed forces = band_total + 2.
# (Bumping SCHEMA_VERSION below invalidates older caches that used wrong offsets.)
B23001_SCHEMA_VERSION = 3
B23001_18_64_WEIGHTS: dict[float, dict[str, list[str]]] = {
    0.5: {
        "total": ["B23001_003E", "B23001_089E"],
        "civilian_lf": ["B23001_006E", "B23001_092E"],
        "armed_forces": ["B23001_005E", "B23001_091E"],
    },
    1.0: {
        "total": [
            "B23001_010E", "B23001_017E", "B23001_024E", "B23001_031E",
            "B23001_038E", "B23001_045E", "B23001_052E", "B23001_059E",
            "B23001_066E", "B23001_096E", "B23001_103E", "B23001_110E",
            "B23001_117E", "B23001_124E", "B23001_131E", "B23001_138E",
            "B23001_145E", "B23001_152E",
        ],
        "civilian_lf": [
            "B23001_013E", "B23001_020E", "B23001_027E", "B23001_034E",
            "B23001_041E", "B23001_048E", "B23001_055E", "B23001_062E",
            "B23001_069E", "B23001_099E", "B23001_106E", "B23001_113E",
            "B23001_120E", "B23001_127E", "B23001_134E", "B23001_141E",
            "B23001_148E", "B23001_155E",
        ],
        "armed_forces": [
            "B23001_012E", "B23001_019E", "B23001_026E", "B23001_033E",
            "B23001_040E", "B23001_047E", "B23001_054E", "B23001_061E",
            "B23001_068E", "B23001_098E", "B23001_105E", "B23001_112E",
            "B23001_119E", "B23001_126E", "B23001_133E", "B23001_140E",
            "B23001_147E", "B23001_154E",
        ],
    },
}

B23001_VARS = sorted({
    var
    for groups in B23001_18_64_WEIGHTS.values()
    for vars_ in groups.values()
    for var in vars_
})

ACS_LF_STATUS_COLS = [
    "acs_lf_status_pop_18_64",
    "acs_civilian_labor_force_18_64",
    "acs_armed_forces_18_64",
    "acs_lfpr_pct",
]

# Maps logical age-group names → raw ACS variable lists (male + female combined)
AGE_GROUPS: dict[str, list[str]] = {
    "under_5":  ["B01001_003E", "B01001_027E"],
    "5_9":      ["B01001_004E", "B01001_028E"],
    "10_14":    ["B01001_005E", "B01001_029E"],
    "15_17":    ["B01001_006E", "B01001_030E"],
    # Working-age cohorts
    "18_24":    ["B01001_007E", "B01001_008E", "B01001_009E", "B01001_010E",
                 "B01001_031E", "B01001_032E", "B01001_033E", "B01001_034E"],
    "25_29":    ["B01001_011E", "B01001_035E"],
    "30_34":    ["B01001_012E", "B01001_036E"],
    "35_39":    ["B01001_013E", "B01001_037E"],
    "40_44":    ["B01001_014E", "B01001_038E"],
    "45_49":    ["B01001_015E", "B01001_039E"],
    "50_54":    ["B01001_016E", "B01001_040E"],
    "55_59":    ["B01001_017E", "B01001_041E"],
    "60_64":    ["B01001_018E", "B01001_019E", "B01001_042E", "B01001_043E"],
    # Retirement-age cohorts
    "65_69":    ["B01001_020E", "B01001_021E", "B01001_044E", "B01001_045E"],
    "70_74":    ["B01001_022E", "B01001_046E"],
    "75_79":    ["B01001_023E", "B01001_047E"],
    "80_84":    ["B01001_024E", "B01001_048E"],
    "85_plus":  ["B01001_025E", "B01001_049E"],
}

WORKFORCE_GROUPS = ["18_24", "25_29", "30_34", "35_39", "40_44",
                    "45_49", "50_54", "55_59", "60_64"]
YOUTH_GROUPS     = ["under_5", "5_9", "10_14", "15_17"]
RETIREMENT_GROUPS = ["65_69", "70_74", "75_79", "80_84", "85_plus"]


class CensusKeyError(RuntimeError):
    """Raised when the Census API rejects a request for a missing/invalid key."""


def _get(url: str, params: dict, retries: int = 3) -> list:
    last_exc = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
        except requests.RequestException as exc:
            # Request exceptions may include the query string and Census key.
            last_exc = type(exc).__name__
            if attempt == retries - 1:
                raise RuntimeError(f"Census request failed ({last_exc}); credentials omitted") from None
            print(f"  [retry {attempt+1}] Census request failed ({last_exc})")
            time.sleep(3 * (attempt + 1))
            continue

        # Census returns HTTP 200 (via a 302 redirect) with an HTML "Missing Key"
        # page for keyless or invalid-key requests, so raise_for_status() passes but
        # r.json() then fails with a cryptic "Expecting value: line 2 column 1".
        # Detect that here and raise an actionable error instead — and do NOT retry,
        # since a missing key will never succeed.
        try:
            return r.json()
        except ValueError:
            body = r.text.strip()
            low = body.lower()
            if "missing key" in low or ("valid" in low and "key" in low):
                raise CensusKeyError(
                    "Census API rejected the request — a valid CENSUS_API_KEY is required. "
                    "Set it in ks_workforce_forecast/.env (local) or in the Streamlit app's "
                    "Secrets (cloud). Free key: https://api.census.gov/data/key_signup.html"
                ) from None
            raise RuntimeError(
                f"Census API returned a non-JSON response (HTTP {r.status_code}): "
                f"{body[:200]!r}"
            ) from None


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def fetch_year(year: int, state_fips: str = "20",
               api_key: str | None = None,
               cache_dir: Path | None = None) -> pd.DataFrame:
    """Fetch full age distribution for all counties in state for one ACS year."""

    cache_file = (cache_dir / f"acs5_{year}_s{state_fips}.parquet") if cache_dir else None
    if cache_file and cache_file.exists():
        print(f"  [cache] {year}")
        cached = pd.read_parquet(cache_file)
        cached = _add_acs_metadata(cached, year, state_fips)
        cache_ver = int(cached["acs_lf_schema_version"].iloc[0]) \
            if "acs_lf_schema_version" in cached.columns and len(cached) else 0
        if all(c in cached.columns for c in ACS_LF_STATUS_COLS) and cache_ver >= B23001_SCHEMA_VERSION:
            return cached
        print(f"  [cache stale] {year} ACS labor-force status missing or schema "
              f"v{cache_ver} < v{B23001_SCHEMA_VERSION}; refetching")

    base = ACS_BASE.format(year=year)
    shared = {"for": f"county:*", "in": f"state:{state_fips}"}
    if api_key:
        shared["key"] = api_key

    # Batch 1: total pop + male vars (≤50 variable limit)
    batch1 = [TOTAL_POP] + MALE_VARS
    raw1 = _get(base, {"get": "NAME," + ",".join(batch1), **shared})
    hdr1 = raw1[0]

    rows: dict[str, dict] = {}
    for row in raw1[1:]:
        fips = row[hdr1.index("county")]
        rows[fips] = {"state_fips": state_fips.zfill(2),
                      "county_fips": fips,
                      "county_name": row[hdr1.index("NAME")].split(",")[0].strip()}
        for v in batch1:
            rows[fips][v] = int(row[hdr1.index(v)] or 0)

    time.sleep(1.5)

    # Batch 2: female vars
    raw2 = _get(base, {"get": ",".join(FEMALE_VARS), **shared})
    hdr2 = raw2[0]
    for row in raw2[1:]:
        fips = row[hdr2.index("county")]
        for v in FEMALE_VARS:
            rows[fips][v] = int(row[hdr2.index(v)] or 0)

    # Batch 3+: ACS labor-force-status variables from B23001.
    for batch in _chunks(B23001_VARS, 45):
        time.sleep(1.5)
        raw_lf = _get(base, {"get": ",".join(batch), **shared})
        hdr_lf = raw_lf[0]
        for row in raw_lf[1:]:
            fips = row[hdr_lf.index("county")]
            if fips not in rows:
                continue
            for v in batch:
                rows[fips][v] = int(row[hdr_lf.index(v)] or 0)

    # Retain published sampling uncertainty separately from model intervals.
    estimate_vars = [TOTAL_POP] + MALE_VARS + FEMALE_VARS + B23001_VARS
    for batch in _chunks([v[:-1] + "M" for v in estimate_vars], 45):
        time.sleep(1.5)
        raw_moe = _get(base, {"get": ",".join(batch), **shared})
        header = raw_moe[0]
        for values in raw_moe[1:]:
            fips = values[header.index("county")]
            if fips in rows:
                for variable in batch:
                    rows[fips][variable] = _census_number(values[header.index(variable)])

    df = pd.DataFrame(list(rows.values()))

    # Aggregate to logical age groups
    for group, vars_ in AGE_GROUPS.items():
        present = [v for v in vars_ if v in df.columns]
        df[f"pop_{group}"] = df[present].sum(axis=1)
        df[f"pop_{group}_moe"] = _sum_moe(df, [(v[:-1] + "M", 1.0) for v in vars_])

    df["pop_working_age"]  = df[[f"pop_{g}" for g in WORKFORCE_GROUPS]].sum(axis=1)
    df["pop_youth"]        = df[[f"pop_{g}" for g in YOUTH_GROUPS]].sum(axis=1)
    df["pop_retirement"]   = df[[f"pop_{g}" for g in RETIREMENT_GROUPS]].sum(axis=1)
    df["pop_total"]        = df[TOTAL_POP]
    df["pop_total_moe"] = df[TOTAL_POP[:-1] + "M"]
    df["moe_confidence_level"] = 0.90
    df = _add_labor_force_status(df)
    df["year"] = year
    df = _add_acs_metadata(df, year, state_fips)

    # Drop raw census variable columns to keep output clean
    raw_cols = [TOTAL_POP] + MALE_VARS + FEMALE_VARS + B23001_VARS
    df = df.drop(columns=[c for c in raw_cols if c in df.columns])

    if cache_file:
        cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_file, index=False)
        print(f"  [saved] {cache_file.name}")

    return df


def _weighted_sum(df: pd.DataFrame, field: str) -> pd.Series:
    total = pd.Series(0.0, index=df.index)
    for weight, groups in B23001_18_64_WEIGHTS.items():
        present = [v for v in groups[field] if v in df.columns]
        if present:
            total = total + df[present].sum(axis=1, min_count=len(present)) * weight
    return total


def _sum_moe(df: pd.DataFrame, weighted_vars: list[tuple[str, float]]) -> pd.Series:
    """ACS approximate sum MOE; missing components never count as zero.

    Root-sum-of-squares uses published 90% MOEs. Weights on the 16–19 band
    inherit the age-allocation approximation; they are not exact age-18–19 MOEs.
    """
    pieces = [pd.to_numeric(df.get(v, pd.Series(float("nan"), index=df.index)),
                            errors="coerce").where(lambda s: s >= 0) * weight
              for v, weight in weighted_vars]
    if not pieces:
        return pd.Series(float("nan"), index=df.index)
    squared = pd.concat(pieces, axis=1).pow(2)
    return squared.sum(axis=1, min_count=len(pieces)).pow(0.5)


def _add_labor_force_status(df: pd.DataFrame) -> pd.DataFrame:
    """Add ACS B23001 18-64 civilian LFPR fields to the county-year table.

    All-absent is a deliberate no-op: legacy cached frames predate B23001 and
    must still load. SOME-but-not-all is an error, not a no-op, because
    _weighted_sum() skips absent variables silently — a frame missing one
    variable would contribute zero for it and publish a plausibly wrong LFPR
    without raising. A Census schema change that drops or renames one B23001
    variable is exactly how that would happen (found 2026-09-04 while fixing a
    test fixture that had been under-counting this way).
    """
    present = [v for v in B23001_VARS if v in df.columns]
    if not present:
        return df
    missing = [v for v in B23001_VARS if v not in df.columns]
    if missing:
        shown = ", ".join(missing[:6]) + ("" if len(missing) <= 6 else f" (+{len(missing) - 6} more)")
        raise ValueError(
            f"_add_labor_force_status: {len(present)} of {len(B23001_VARS)} B23001 "
            f"variables present but {len(missing)} missing [{shown}]. Refusing to "
            f"compute a partial LFPR — check whether the Census B23001 schema changed "
            f"(api.census.gov/data/<year>/acs/acs5/groups/B23001.json) and update "
            f"B23001_18_64_WEIGHTS / B23001_SCHEMA_VERSION."
        )

    df = df.copy()
    for variable in B23001_VARS:
        df[variable] = pd.to_numeric(df[variable], errors="coerce").where(lambda s: s >= 0)
    lf_status_pop = _weighted_sum(df, "total")
    civilian_lf = _weighted_sum(df, "civilian_lf")
    armed_forces = _weighted_sum(df, "armed_forces")
    civilian_denominator = (lf_status_pop - armed_forces).where(lambda s: s >= 0)

    df["acs_lf_status_pop_18_64"] = lf_status_pop.round(0).astype("Int64")
    df["acs_civilian_labor_force_18_64"] = civilian_lf.round(0).astype("Int64")
    df["acs_armed_forces_18_64"] = armed_forces.round(0).astype("Int64")
    df["acs_lfpr_pct"] = (
        civilian_lf / civilian_denominator.where(civilian_denominator.ne(0)) * 100
    ).round(2).where(lambda s: s.between(0, 100))
    df["acs_lf_schema_version"] = B23001_SCHEMA_VERSION
    df["acs_age_basis"] = "18–64 approximation: half of 16–19 plus ages 20–64"
    df["acs_lf_method_type"] = "derived"
    for field, output in [("civilian_lf", "acs_civilian_labor_force_18_64_moe"),
                          ("total", "acs_lf_status_pop_18_64_moe"),
                          ("armed_forces", "acs_armed_forces_18_64_moe")]:
        df[output] = _sum_moe(df, [(v[:-1] + "M", weight)
                                  for weight, groups in B23001_18_64_WEIGHTS.items()
                                  for v in groups[field]])
    df["acs_moe_method"] = "90% approximate RSS; age allocation uncertainty not included"
    return df


def _add_acs_metadata(df: pd.DataFrame, year: int, state_fips: str) -> pd.DataFrame:
    """Attach period-estimate metadata, including for legacy cached files."""
    df = df.copy()
    df["state_fips"] = state_fips.zfill(2)
    df["acs_vintage_year"] = year
    df["acs_period_start_year"] = year - 4
    df["acs_period_end_year"] = year
    df["acs_period_midpoint_year"] = year - 2
    df["estimate_type"] = "ACS 5-year"
    df["overlapping_period_estimate"] = True
    df["acs_age_basis"] = "18–64 approximation: half of 16–19 plus ages 20–64"
    return df


def _census_number(value):
    """ACS numeric estimates/MOEs: preserve zero, reject sentinels and negatives."""
    import math
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def parse_acs_supply(payload: list, year: int, table: str = "S2301") -> pd.DataFrame:
    """Parse exact published 16+ measures and their 90% MOEs into observations.

    S2301 participation is the published population-16+ subject measure.
    B23025 civilian counts are separate measures; no S2301 percentage is
    multiplied by an incompatible 18–64 population to invent a count.
    """
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        raise ValueError("ACS response must be a Census header/rows array")
    specs = {
        "S2301": {"S2301_C02_001": ("labor_force_participation_rate_16_plus", "percent",
                                       "Population age 16 and over")},
        "B23025": {
            "B23025_001": ("population_16_plus", "people", "Population age 16 and over"),
            "B23025_002": ("labor_force_16_plus", "people", "Population age 16 and over; includes armed forces"),
            "B23025_003": ("civilian_labor_force_16_plus", "people", "Civilian labor force age 16 and over"),
            "B23025_004": ("civilian_employed_16_plus", "people", "Civilian employed age 16 and over"),
            "B23025_005": ("civilian_unemployed_16_plus", "people", "Civilian unemployed age 16 and over"),
            "B23025_006": ("armed_forces_16_plus", "people", "Armed forces age 16 and over"),
            "B23025_007": ("not_in_labor_force_16_plus", "people", "Population age 16 and over not in labor force"),
        },
    }
    if table not in specs:
        raise ValueError(f"Unsupported ACS supply table: {table}")
    fields = specs[table]
    header = payload[0]
    required = {"state", "county"} | {code + suffix for code in fields for suffix in ("E", "M")}
    if required - set(header):
        raise ValueError(f"ACS {table} missing fields: {sorted(required - set(header))}")
    source_url = f"https://api.census.gov/data/{year}/acs/acs5" + ("/subject" if table == "S2301" else "")
    observations = []
    for values in payload[1:]:
        if len(values) != len(header):
            raise ValueError("ACS row length does not match header")
        row = dict(zip(header, values))
        state, county = str(row["state"]), str(row["county"])
        if not state.isdigit() or not county.isdigit() or len(state) > 2 or len(county) > 3:
            raise ValueError("Invalid ACS county geography")
        for code, (measure, unit, universe) in fields.items():
            estimate = _census_number(row[code + "E"])
            moe = _census_number(row[code + "M"])
            if unit == "percent" and estimate is not None and estimate > 100:
                estimate = None
            observations.append({
                "source_id": "acs_" + table.lower(), "measure_id": measure,
                "geography_id": state.zfill(2) + county.zfill(3),
                "geography_type": "county", "geography_name": row.get("NAME"),
                "location_basis": "residence", "age_group": "16+", "sex_group": "all",
                "population_universe": universe, "period_start": f"{year-4}-01-01",
                "period_end": f"{year}-12-31", "frequency": "five_year",
                "estimate": estimate, "moe": moe, "moe_confidence_level": 0.90,
                "rse": (moe / 1.645 / estimate * 100 if moe is not None and estimate not in (None, 0) else None),
                "unit": unit, "source_status": str(row[code + "E"]),
                "source_moe_status": str(row[code + "M"]),
                "availability_status": "published" if estimate is not None else "not_reported",
                "quality_note": "MOE unavailable" if moe is None else "Published 90% sampling MOE",
                "method_type": "observed estimate", "source_url": source_url,
                "release_id": f"acs5-{year}", "reference_label": f"{year-4}–{year} ACS five-year",
            })
    result = pd.DataFrame(observations)
    if not result.empty and result.duplicated(["geography_id", "measure_id"]).any():
        raise ValueError("Duplicate ACS county/measure observations")
    return result


def fetch_acs_supply(year: int = 2024, state_fips: str = "20",
                     api_key: str | None = None, tables=("S2301", "B23025")) -> pd.DataFrame:
    """Fetch current exact 16+ indicators without touching cohort caches/outputs."""
    frames = []
    for table in tables:
        if table == "S2301":
            fields = ["S2301_C02_001E", "S2301_C02_001M"]
            endpoint = ACS_BASE.format(year=year) + "/subject"
        elif table == "B23025":
            fields = [f"B23025_{i:03d}{suffix}" for i in range(1, 8) for suffix in ("E", "M")]
            endpoint = ACS_BASE.format(year=year)
        else:
            raise ValueError(f"Unsupported ACS supply table: {table}")
        params = {"get": ",".join(["NAME"] + fields), "for": "county:*", "in": f"state:{state_fips.zfill(2)}"}
        if api_key:
            params["key"] = api_key
        frames.append(parse_acs_supply(_get(endpoint, params), year, table))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fetch_all(state_fips: str = "20",
              api_key: str | None = None,
              cache_dir: Path | None = None) -> pd.DataFrame:
    """Fetch all ACS years and return combined DataFrame."""
    frames = []
    for year in ACS_YEARS:
        print(f"Fetching ACS 5-yr {year} (state {state_fips})…")
        df = fetch_year(year, state_fips, api_key, cache_dir)
        frames.append(df)
        time.sleep(2)
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["county_fips", "year"]).reset_index(drop=True)
    return combined
