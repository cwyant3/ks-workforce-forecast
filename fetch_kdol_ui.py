"""
fetch_kdol_ui.py
Fetches Kansas Department of Labor (KDOL) Unemployment Insurance (UI) claims
data by county and industry sector.

UI claims are the earliest leading indicator of sector distress — they appear
12–18 months before QCEW annual averages reflect a disruption. For training
organizations deciding where to invest new capacity, this is the real-time pulse.

KS-ONLY MODULE: This data is specific to Kansas. The feature flag `state_fips`
is accepted for API signature consistency but will raise if not "20".

Data source: KDOL Labor Market Information Services (LMIS)
  https://www.dol.ks.gov/lmis/unemployment-insurance-data

Access strategy (two-path):
  1. ATTEMPT: Download from KDOL LMIS direct CSV URL (if available)
  2. FALLBACK: Load from a manually placed file at data/kdol_cache/kdol_ui_manual.csv
               or data/kdol_cache/kdol_ui_manual.xlsx

Manual file format (CSV or Excel, required columns):
  year       — 4-digit integer
  month      — 1–12 integer
  county     — county name OR 3-digit FIPS (module normalises both)
  naics2     — 2-digit NAICS code string (e.g. "62", "23")
  ui_claims  — count of initial/continued UI claims for that period

Output DataFrame columns:
  state_fips, county_fips (3-digit str), year (int), month (int),
  naics2 (str), sector (str | None), ui_claims (int),
  rolling_3mo (float), trend_direction (str: "rising"|"stable"|"falling")
"""

import io
import time
import warnings
import requests
import pandas as pd
import numpy as np
from pathlib import Path

# KS-only; raise if caller passes another state
_KS_FIPS = "20"

# Rolling window for trend computation (months)
_ROLLING_WINDOW = 3

# KDOL LMIS known download endpoints (may change; update if 404)
# These are best-guess URLs from KDOL's data portal structure.
# If they return 404, the module falls back to the manual file path.
_KDOL_CANDIDATE_URLS: list[str] = [
    "https://www.dol.ks.gov/docs/default-source/lmis-library/"
    "unemployment-insurance/ui-claims-by-county-naics.csv",
    "https://lmis.dol.ks.gov/api/ui/claims/county",   # hypothetical REST endpoint
]

# NAICS 2-digit → sector (same as other modules)
NAICS2_TO_SECTOR: dict[str, str] = {
    "62": "Healthcare",
    "31": "Manufacturing",
    "32": "Manufacturing",
    "33": "Manufacturing",
    "71": "Hospitality & Entertainment",
    "72": "Hospitality & Entertainment",
    "51": "IT/Computer Services",
    "54": "IT/Computer Services",
    "22": "Skilled Trades",
    "23": "Skilled Trades",
    "81": "Skilled Trades",
}

# Kansas county name → 3-digit FIPS (105 counties)
_KS_COUNTY_FIPS: dict[str, str] = {
    "allen": "001", "anderson": "003", "atchison": "005", "barber": "007",
    "barton": "009", "bourbon": "011", "brown": "013", "butler": "015",
    "chase": "017", "chautauqua": "019", "cherokee": "021", "cheyenne": "023",
    "clark": "025", "clay": "027", "cloud": "029", "coffey": "031",
    "comanche": "033", "cowley": "035", "crawford": "037", "decatur": "039",
    "dickinson": "041", "doniphan": "043", "douglas": "045", "edwards": "047",
    "elk": "049", "ellis": "051", "ellsworth": "053", "finney": "055",
    "ford": "057", "franklin": "059", "geary": "061", "gove": "063",
    "graham": "065", "grant": "067", "gray": "069", "greeley": "071",
    "greenwood": "073", "hamilton": "075", "harper": "077", "harvey": "079",
    "haskell": "081", "hodgeman": "083", "jackson": "085", "jefferson": "087",
    "jewell": "089", "johnson": "091", "kearny": "093", "kingman": "095",
    "kiowa": "097", "labette": "099", "lane": "101", "leavenworth": "103",
    "lincoln": "105", "linn": "107", "logan": "109", "lyon": "111",
    "mcpherson": "113", "marion": "115", "marshall": "117", "meade": "119",
    "miami": "121", "mitchell": "123", "montgomery": "125", "morris": "127",
    "morton": "129", "nemaha": "131", "neosho": "133", "ness": "135",
    "norton": "137", "osage": "139", "osborne": "141", "ottawa": "143",
    "pawnee": "145", "phillips": "147", "pottawatomie": "149", "pratt": "151",
    "rawlins": "153", "reno": "155", "republic": "157", "rice": "159",
    "riley": "161", "rooks": "163", "rush": "165", "russell": "167",
    "saline": "169", "scott": "171", "sedgwick": "173", "seward": "175",
    "shawnee": "177", "sheridan": "179", "sherman": "181", "smith": "183",
    "stafford": "185", "stanton": "187", "stevens": "189", "sumner": "191",
    "thomas": "193", "trego": "195", "wabaunsee": "197", "wallace": "199",
    "washington": "201", "wichita": "203", "wilson": "205", "woodson": "207",
    "wyandotte": "209",
}

_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; workforce-forecast/1.0)"}


# ── Download attempt ──────────────────────────────────────────────────────────

def _try_download() -> pd.DataFrame | None:
    """Attempt download from known KDOL URLs. Return None if all fail."""
    for url in _KDOL_CANDIDATE_URLS:
        try:
            resp = requests.get(url, headers=_HTTP_HEADERS, timeout=30)
            if resp.status_code == 200 and len(resp.content) > 500:
                print(f"    Downloaded from {url}")
                return pd.read_csv(io.BytesIO(resp.content), dtype=str)
        except Exception:
            pass
    return None


def _load_manual_file(cache_dir: Path) -> pd.DataFrame | None:
    """Load manually placed KDOL UI file from cache directory."""
    for name in ("kdol_ui_manual.csv", "kdol_ui_manual.xlsx",
                 "kdol_ui_manual.xls"):
        path = cache_dir / name
        if path.exists():
            print(f"    Loading manual KDOL UI file: {path.name}")
            if path.suffix == ".csv":
                return pd.read_csv(path, dtype=str)
            return pd.read_excel(path, dtype=str)
    return None


# ── Normalisation ──────────────────────────────────────────────────────────────

def _resolve_county_fips(county_col: pd.Series) -> pd.Series:
    """
    Convert county name or 3-digit FIPS to standardised 3-digit FIPS string.
    Strips " County" suffix, lowercases, and looks up in _KS_COUNTY_FIPS.
    Returns NaN for unresolved values.
    """
    def _resolve(val: str) -> str | None:
        v = str(val).strip().lower().replace(" county", "").replace(".", "")
        # Already a 3-digit FIPS number
        if v.isdigit() and len(v) <= 3:
            return v.zfill(3)
        # Already 5-digit FIPS starting with "20"
        if v.startswith("20") and len(v) == 5 and v.isdigit():
            return v[2:].zfill(3)
        return _KS_COUNTY_FIPS.get(v)

    return county_col.map(_resolve)


def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalise raw KDOL dataframe to the module's standard schema."""
    raw.columns = raw.columns.str.strip().str.lower().str.replace(" ", "_")

    # Resolve county to 3-digit FIPS
    county_src = next(
        (c for c in raw.columns if "county" in c), None
    )
    if county_src is None:
        raise KeyError(f"No county column found. Columns: {list(raw.columns)}")
    raw["county_fips"] = _resolve_county_fips(raw[county_src])

    # Resolve NAICS2
    naics_src = next(
        (c for c in raw.columns if "naics" in c), None
    )
    if naics_src is None:
        raise KeyError(f"No NAICS column found. Columns: {list(raw.columns)}")
    raw["naics2"] = raw[naics_src].astype(str).str.strip().str[:2].str.zfill(2)

    # UI claims
    claims_src = next(
        (c for c in raw.columns if "claim" in c or "ui_" in c), None
    )
    if claims_src is None:
        raise KeyError(f"No claims column found. Columns: {list(raw.columns)}")
    raw["ui_claims"] = pd.to_numeric(raw[claims_src], errors="coerce").fillna(0).astype(int)

    # Year and month
    if "year" not in raw.columns:
        raise KeyError("'year' column required")
    raw["year"] = pd.to_numeric(raw["year"], errors="coerce").astype("Int64")

    if "month" not in raw.columns:
        raw["month"] = 1   # assume January if monthly breakdown not available
    raw["month"] = pd.to_numeric(raw["month"], errors="coerce").fillna(1).astype(int)

    raw["sector"]     = raw["naics2"].map(NAICS2_TO_SECTOR)
    raw["state_fips"] = _KS_FIPS

    cols = ["state_fips", "county_fips", "year", "month",
            "naics2", "sector", "ui_claims"]
    return raw[[c for c in cols if c in raw.columns]].dropna(
        subset=["county_fips", "year"]
    ).reset_index(drop=True)


# ── Trend computation ─────────────────────────────────────────────────────────

def _rolling_trend(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add rolling_3mo (3-month average) and trend_direction per
    (county_fips, naics2) group.

    trend_direction:
      "rising"  — slope of last 6 months is positive and > 5%
      "falling" — slope is negative and < -5%
      "stable"  — everything else
    """
    df = df.sort_values(["county_fips", "naics2", "year", "month"]).copy()

    # Create a sortable period int for rolling
    df["_period"] = df["year"] * 100 + df["month"]

    results = []
    for (county, naics2), grp in df.groupby(["county_fips", "naics2"]):
        grp = grp.sort_values("_period").copy()
        grp["rolling_3mo"] = grp["ui_claims"].rolling(
            _ROLLING_WINDOW, min_periods=1
        ).mean().round(1)

        # Trend: OLS over last 6 periods
        tail = grp.tail(6)
        if len(tail) >= 3:
            x     = np.arange(len(tail), dtype=float)
            y     = tail["ui_claims"].values.astype(float)
            slope = float(np.polyfit(x, y, 1)[0])
            mean_ = float(y.mean()) if y.mean() != 0 else 1.0
            rel   = slope / mean_
            if rel > 0.05:
                direction = "rising"
            elif rel < -0.05:
                direction = "falling"
            else:
                direction = "stable"
        else:
            direction = "stable"

        grp["trend_direction"] = direction
        results.append(grp)

    out = pd.concat(results, ignore_index=True)
    return out.drop(columns=["_period"])


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_kdol_ui(
    state_fips: str = "20",
    rolling_months: int = 24,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch Kansas KDOL UI claims data by county and NAICS sector.

    Attempts download from KDOL LMIS; falls back to a manually placed file
    at {cache_dir}/kdol_ui_manual.csv (or .xlsx).

    Parameters
    ----------
    state_fips     : must be "20" (Kansas); raises for other states
    rolling_months : number of most recent months to keep (default 24)
    cache_dir      : directory for cache and manual file placement

    Returns
    -------
    DataFrame: state_fips, county_fips, year, month, naics2, sector,
               ui_claims, rolling_3mo, trend_direction

    Manual file placement
    ---------------------
    If the KDOL download fails, place a CSV at:
      {cache_dir}/kdol_ui_manual.csv
    Required columns: year, month, county, naics2, ui_claims
    (See module docstring for column details.)
    """
    if state_fips.zfill(2) != _KS_FIPS:
        raise ValueError(
            f"fetch_kdol_ui is Kansas-only (FIPS 20). Got: {state_fips}"
        )

    if cache_dir is None:
        raise ValueError("cache_dir is required")
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = cache_dir / "kdol_ui.parquet"
    if cache_file.exists():
        print(f"  [cache] KDOL UI")
        return pd.read_parquet(cache_file)

    # --- Try download first ---
    raw = _try_download()
    if raw is None:
        print("  [KDOL] Direct download unavailable — checking for manual file…")
        raw = _load_manual_file(cache_dir)

    if raw is None:
        warnings.warn(
            "\n\nKDOL UI data not available.\n"
            "KDOL does not expose a stable public download API.\n"
            "To use this module:\n"
            f"  1. Visit: https://www.dol.ks.gov/lmis/unemployment-insurance-data\n"
            f"  2. Download the county×industry UI claims file\n"
            f"  3. Save it as: {cache_dir / 'kdol_ui_manual.csv'}\n"
            f"     Required columns: year, month, county, naics2, ui_claims\n"
            "Then re-run with --kdol flag.\n",
            UserWarning,
            stacklevel=2,
        )
        return pd.DataFrame(columns=[
            "state_fips", "county_fips", "year", "month",
            "naics2", "sector", "ui_claims", "rolling_3mo", "trend_direction",
        ])

    # --- Normalise ---
    df = _normalise(raw)

    # --- Rolling window filter (most recent N months) ---
    if rolling_months > 0 and not df.empty:
        df["_period"] = df["year"] * 100 + df["month"]
        cutoff = df["_period"].nlargest(rolling_months).min()
        df = df[df["_period"] >= cutoff].drop(columns=["_period"])

    # --- Compute trends ---
    df = _rolling_trend(df)

    df.to_parquet(cache_file, index=False)
    print(f"  [saved] kdol_ui.parquet  ({len(df)} rows, "
          f"{df['county_fips'].nunique()} counties, "
          f"{df[['year','month']].drop_duplicates().shape[0]} periods)")

    return df


def sector_pulse(kdol_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate KDOL UI claims to (sector, year, month) statewide.
    Returns a statewide sector pulse for the dashboard traffic-light panel.
    Includes trend_direction per sector based on the most recent 6 months.
    """
    if kdol_df.empty:
        return pd.DataFrame()

    agg = (
        kdol_df[kdol_df["sector"].notna()]
        .groupby(["sector", "year", "month"], as_index=False)["ui_claims"]
        .sum()
        .sort_values(["sector", "year", "month"])
    )

    # Re-compute rolling and trend at the statewide level
    results = []
    for sector, grp in agg.groupby("sector"):
        grp = grp.copy()
        grp["rolling_3mo"] = grp["ui_claims"].rolling(3, min_periods=1).mean().round(1)
        tail = grp.tail(6)
        if len(tail) >= 3:
            x     = np.arange(len(tail), dtype=float)
            y     = tail["ui_claims"].values.astype(float)
            slope = float(np.polyfit(x, y, 1)[0])
            mean_ = float(y.mean()) if y.mean() != 0 else 1.0
            rel   = slope / mean_
            direction = "rising" if rel > 0.05 else ("falling" if rel < -0.05 else "stable")
        else:
            direction = "stable"
        grp["trend_direction"] = direction
        results.append(grp)

    return pd.concat(results, ignore_index=True)
