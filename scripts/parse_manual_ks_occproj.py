"""
parse_manual_ks_occproj.py
Parse the Kansas Department of Labor (KDOL LMIS) occupational employment
projections file (occproj__YYYYBBYYYY.xls) into parquet outputs the
dashboard can consume.

This is SOC-occupation-level KS-specific projection data with KDOL's
in-demand / DemandRank flags — strategically the highest-value layer for
WSU Tech program design (answers "what occupations should we train for
in Kansas?").

File comes from KDOL Telerik report builder served as HTML-disguised-as-.xls.

Outputs (data/outputs/):
  ks_occ_proj_state_s20.parquet    — state-level SOC occupations
  ks_occ_proj_region_s20.parquet   — sub-state region (LWDA) SOC occupations
  ks_occ_in_demand_top_s20.parquet — DemandRank-sorted top in-demand list
  ks_occ_by_sector_s20.parquet     — rolled up to 5 dashboard sectors

Schema (cleaned):
  state_fips, areatype, areaname (region name when areatype=15),
  soc_code (matoccode), occ_title (codeTitle), codelevel (1=group, 7=detail),
  base_year, proj_year, base_emp (estoccprj), proj_emp (projoccprj),
  pct_change (pchg), annual_openings, annual_exits, annual_transfers,
  annual_change, in_demand (0/1), demand_rank (Int), green_job (0/1),
  sector (mapped from SOC major group), projection_source
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

# SOC major group → dashboard sector (mirrors fetch_bls_proj.SOC2_TO_SECTOR)
SOC2_TO_SECTOR: dict[str, str] = {
    "15": "IT/Computer Services",
    "11": "IT/Computer Services",   # management — loosely aligned
    "29": "Healthcare",
    "31": "Healthcare",
    "35": "Hospitality & Entertainment",
    "39": "Hospitality & Entertainment",
    "47": "Skilled Trades",
    "49": "Skilled Trades",
    "51": "Manufacturing",
    "17": "Manufacturing",          # engineering/architecture
}

_NUMERIC = ["estoccprj", "projoccprj", "grrate", "nchg", "pchg",
            "aopeng", "aopenr", "aopent", "exits", "annualexits",
            "transfers", "annualtransfers", "change", "annualchange",
            "openings", "annualopenings", "codelevel"]


def parse(xls_path: Path, state_fips: str = "20") -> pd.DataFrame:
    raw = pd.read_html(xls_path)[0]
    raw.columns = [str(c).strip() for c in raw.iloc[0]]
    raw = raw.iloc[1:].reset_index(drop=True)
    raw = raw[raw["stfips"].astype(str).str.zfill(2) == state_fips.zfill(2)].copy()

    for c in _NUMERIC:
        if c in raw.columns:
            raw[c] = pd.to_numeric(
                raw[c].astype(str).str.replace(",", "").str.replace("%", "").str.strip(),
                errors="coerce",
            )

    raw["matoccode"] = raw["matoccode"].astype(str).str.strip()
    # Format SOC code as DD-DDDD when possible
    raw["soc_code"]  = raw["matoccode"].apply(
        lambda x: f"{x[:2]}-{x[2:]}" if x.isdigit() and len(x) == 6 else x
    )
    raw["sector"]    = raw["matoccode"].str[:2].map(SOC2_TO_SECTOR)

    # in_demand / green: KDOL flags are "0"/"1" strings or NaN
    for flag in ("inDemand", "regionalInDemand", "green", "regionalGreen"):
        if flag in raw.columns:
            raw[flag] = pd.to_numeric(raw[flag], errors="coerce").fillna(0).astype("Int64")

    return raw.rename(columns={
        "stfips": "state_fips",
        "codeTitle": "occ_title",
        "estyear": "base_year",
        "projyear": "proj_year",
        "estoccprj": "base_emp",
        "projoccprj": "proj_emp",
        "pchg": "pct_change",
        "annualopenings": "annual_openings",
        "annualexits": "annual_exits",
        "annualtransfers": "annual_transfers",
        "annualchange": "annual_change",
        "inDemand": "in_demand",
        "regionalInDemand": "regional_in_demand",
        "green": "green_job",
        "regionalGreen": "regional_green",
        "DemandRank": "demand_rank",
    })


# ── Published-workbook support ────────────────────────────────────────────────
# Since the 2024-2034 cycle KDOL also publishes a clean .xlsx ("2024-2034 KS
# Occupational Projections.xlsx", LMIS, published July 2026) alongside the
# legacy Telerik HTML-as-.xls export. Two consequences:
#
#   1. It is STATEWIDE ONLY — there is no areatype/region dimension, so the
#      region output cannot be rebuilt from it (see main()).
#   2. It carries NO in-demand / green flags. Those live in a separate KDOL
#      book, "{YYYY} Occupational Employment Demand (Kansas and Regions).xlsx",
#      which has the High/Emerging Demand flags and a statewide Rank plus one
#      sheet per LWDA region — but no employment levels. So the in-demand layer
#      is rebuilt by joining that book's flags onto these employment levels on
#      SOC code (--demand-file).
#
# It does add columns the Telerik export never had: median/mean annual wage and
# the typical education / experience / on-the-job-training requirements.
_LEVEL_TO_CODELEVEL: dict[str, int] = {
    "total, all occupations": 1,
    "major occupation group": 2,
    "minor occupation group": 3,
    "broad occupation": 5,
    "detailed occupation": 6,
}


def _norm(col: object) -> str:
    """Collapse a multi-line Excel header into a lowercase single-space key."""
    return re.sub(r"\s+", " ", str(col)).strip().lower()


def _find(cols: list[str], *needles: str) -> str | None:
    for c in cols:
        if all(n in c for n in needles):
            return c
    return None


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "").str.replace("%", "").str.strip(),
        errors="coerce",
    )


def _years_from_columns(cols: list[str]) -> tuple[str | None, str | None]:
    base = proj = None
    for c in cols:
        m = re.search(r"(?:19|20)\d{2}", c)
        if not m or "employment" not in c:
            continue
        if "projected" in c:
            proj = m.group(0)
        elif base is None:
            base = m.group(0)
    return base, proj


def load_demand_flags(path: Path) -> tuple[pd.DataFrame, str | None]:
    """Read KDOL's Occupational Employment Demand book into SOC-keyed flags.

    Returns (flags, vintage). The statewide "Kansas" sheet supplies in_demand /
    emerging_demand / high_wage / demand_rank; every other region sheet
    contributes regional_in_demand when it flags the SOC as High Demand.
    """
    xl = pd.ExcelFile(path)
    vintage = None
    m = re.search(r"(?:19|20)\d{2}", path.name)
    if m:
        vintage = m.group(0)

    ks_sheet = next((s for s in xl.sheet_names if _norm(s) == "kansas"), None)
    if ks_sheet is None:
        raise RuntimeError(f"No statewide 'Kansas' sheet in {path.name}")

    def _read(sheet: str) -> pd.DataFrame | None:
        # Row 0 is the sheet title and row 1 a subtitle; the real header is row 2.
        d = xl.parse(sheet, dtype=str, header=2)
        d.columns = [_norm(c) for c in d.columns]
        if _find(list(d.columns), "soc") is None:
            return None
        return d

    ks = _read(ks_sheet)
    if ks is None:
        raise RuntimeError(f"Could not locate the SOC header row on '{ks_sheet}'")
    cols = list(ks.columns)
    soc_col  = _find(cols, "soc") if _find(cols, "soc title") is None else \
               next(c for c in cols if c == "soc" or (c.startswith("soc") and "title" not in c))
    high_col = _find(cols, "high", "demand")
    emrg_col = _find(cols, "emerging", "demand")
    wage_col = _find(cols, "high", "wage")
    rank_col = _find(cols, "rank")

    def _yes(s: pd.Series) -> pd.Series:
        return (s.astype(str).str.strip().str.lower() == "yes").astype("Int64")

    flags = pd.DataFrame()
    flags["soc_code"] = ks[soc_col].astype(str).str.strip()
    flags["in_demand"] = _yes(ks[high_col]) if high_col else 0
    flags["emerging_demand"] = _yes(ks[emrg_col]) if emrg_col else 0
    flags["high_wage"] = _yes(ks[wage_col]) if wage_col else 0
    flags["demand_rank"] = _num(ks[rank_col]).astype("Int64") if rank_col else pd.NA
    flags = flags[flags["soc_code"].str.match(r"^\d{2}-\d{4}$", na=False)]

    # Regional High Demand — union across the LWDA sheets.
    regional: set[str] = set()
    for sheet in xl.sheet_names:
        if sheet == ks_sheet or _norm(sheet) in ("notes", "about the data"):
            continue
        d = _read(sheet)
        if d is None:
            continue
        rc = list(d.columns)
        s_col = next((c for c in rc if c == "soc" or (c.startswith("soc") and "title" not in c)), None)
        h_col = _find(rc, "high", "demand")
        if not (s_col and h_col):
            continue
        hits = d.loc[_yes(d[h_col]) == 1, s_col].astype(str).str.strip()
        regional.update(hits.tolist())

    flags["regional_in_demand"] = flags["soc_code"].isin(regional).astype("Int64")
    return flags.drop_duplicates("soc_code").reset_index(drop=True), vintage


def parse_published(xlsx_path: Path, state_fips: str = "20") -> pd.DataFrame:
    """Parse KDOL's published occupational-projections .xlsx into the Telerik schema."""
    xl = pd.ExcelFile(xlsx_path)
    sheet = next((s for s in xl.sheet_names if "occupational projections" in s.lower()), None)
    if sheet is None:
        raise RuntimeError(
            f"No 'Occupational Projections' sheet in {xlsx_path.name} "
            f"(sheets: {xl.sheet_names})"
        )
    raw = xl.parse(sheet, dtype=str, header=1)
    raw.columns = [_norm(c) for c in raw.columns]
    cols = list(raw.columns)

    lvl_col   = _find(cols, "occupation level")
    code_col  = _find(cols, "occupation code")
    title_col = _find(cols, "occupation title")
    if not (lvl_col and code_col and title_col):
        raise RuntimeError(f"Unexpected occupational columns in {xlsx_path.name}: {cols}")
    base_year, proj_year = _years_from_columns(cols)
    base_col  = _find(cols, base_year or "", "employment") if base_year else None
    proj_col  = _find(cols, "projected", "employment")
    pchg_col  = _find(cols, "percent", "change")
    total_col = _find(cols, "total", "openings")
    exits_col = _find(cols, "openings", "exits")
    trans_col = _find(cols, "openings", "transfers")
    chg_col   = _find(cols, "openings", "change in employment")
    med_col   = _find(cols, "median", "wage")
    mean_col  = _find(cols, "mean", "wage")
    edu_col   = _find(cols, "education")
    exp_col   = _find(cols, "work experience")
    ojt_col   = _find(cols, "training")

    raw = raw[raw[code_col].notna() & raw[lvl_col].notna()].copy()

    df = pd.DataFrame()
    df["state_fips"] = [state_fips.zfill(2)] * len(raw)
    # The published book is statewide-only; stamp the Telerik area identifiers so
    # downstream state/region splits behave identically.
    df["areatype"] = "01"
    df["areaname"] = "Kansas"
    df["soc_code"]  = raw[code_col].astype(str).str.strip()
    df["occ_title"] = raw[title_col].astype(str).str.strip()
    df["codelevel"] = raw[lvl_col].map(lambda v: _LEVEL_TO_CODELEVEL.get(_norm(v)))
    df["base_year"] = base_year
    df["proj_year"] = proj_year
    df["base_emp"]  = _num(raw[base_col]) if base_col else pd.NA
    df["proj_emp"]  = _num(raw[proj_col]) if proj_col else pd.NA
    # Percent change ships as a decimal fraction here; the Telerik export and
    # every downstream consumer use percent.
    df["pct_change"] = (_num(raw[pchg_col]) * 100).round(4) if pchg_col else pd.NA
    df["annual_openings"]  = _num(raw[total_col]) if total_col else pd.NA
    df["annual_exits"]     = _num(raw[exits_col]) if exits_col else pd.NA
    df["annual_transfers"] = _num(raw[trans_col]) if trans_col else pd.NA
    df["annual_change"]    = _num(raw[chg_col]) if chg_col else pd.NA
    # Flags are absent from this book — filled by the demand join when available.
    df["in_demand"] = pd.Series([pd.NA] * len(raw), dtype="Int64")
    df["demand_rank"] = pd.Series([pd.NA] * len(raw), dtype="Int64")
    df["green_job"] = pd.Series([pd.NA] * len(raw), dtype="Int64")
    df["regional_in_demand"] = pd.Series([pd.NA] * len(raw), dtype="Int64")
    df["regional_green"] = pd.Series([pd.NA] * len(raw), dtype="Int64")
    # New in the published book — no Telerik equivalent.
    df["median_annual_wage"] = _num(raw[med_col]) if med_col else pd.NA
    df["mean_annual_wage"]   = _num(raw[mean_col]) if mean_col else pd.NA
    df["education"]       = raw[edu_col].astype(str).str.strip() if edu_col else None
    df["work_experience"] = raw[exp_col].astype(str).str.strip() if exp_col else None
    df["on_the_job_training"] = raw[ojt_col].astype(str).str.strip() if ojt_col else None
    df["sector"] = df["soc_code"].str[:2].map(SOC2_TO_SECTOR)
    return df.reset_index(drop=True)


def is_published_workbook(path: Path) -> bool:
    """True for a real .xlsx; the legacy KDOL export is an HTML table named .xls."""
    return path.suffix.lower() == ".xlsx"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="20")
    ap.add_argument("--input", default="data/occproj__202201002032.xls")
    ap.add_argument("--demand-file", default=None,
                    help="KDOL 'Occupational Employment Demand' .xlsx supplying the "
                         "in-demand / rank flags the published projections book omits")
    ap.add_argument("--output-dir", default="data/outputs")
    args = ap.parse_args()

    xls = Path(args.input)
    if not xls.exists():
        print(f"Input not found: {xls}", file=sys.stderr)
        sys.exit(1)
    sf = args.state.zfill(2)
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)

    published = is_published_workbook(xls)
    if published:
        df = parse_published(xls, args.state)
        df["projection_source"] = "KDOL_State_Occupational_Published"
        print(f"Parsed {len(df)} occupational-projection rows from published workbook "
              f"{xls.name} (vintage {df['base_year'].iloc[0]}-{df['proj_year'].iloc[0]}, "
              f"statewide only)")
        # The published book has no demand flags; graft them on from the companion
        # Occupational Employment Demand book so the in-demand layer survives.
        if args.demand_file:
            dpath = Path(args.demand_file)
            if dpath.exists():
                flags, dvintage = load_demand_flags(dpath)
                df = df.drop(columns=["in_demand", "demand_rank",
                                      "regional_in_demand"]).merge(
                    flags[["soc_code", "in_demand", "demand_rank",
                           "emerging_demand", "high_wage", "regional_in_demand"]],
                    on="soc_code", how="left")
                for c in ("in_demand", "regional_in_demand",
                          "emerging_demand", "high_wage"):
                    df[c] = df[c].fillna(0).astype("Int64")
                matched = int((df["demand_rank"].notna()).sum())
                print(f"  Demand flags joined from {dpath.name} "
                      f"(vintage {dvintage}): {int(df['in_demand'].sum())} in-demand, "
                      f"{matched}/{len(flags)} SOC codes matched a ranked occupation")
            else:
                print(f"  !! demand file not found: {dpath} — in-demand layer will be empty")
        else:
            print("  !! no --demand-file given — in-demand layer will be empty")
    else:
        df = parse(xls, args.state)
        df["projection_source"] = "KDOL_State_Occupational"
    df["state_fips"] = sf

    # State-level vs sub-state region
    keep_cols = [c for c in [
        "state_fips", "areatype", "areaname", "soc_code", "occ_title",
        "codelevel", "base_year", "proj_year", "base_emp", "proj_emp",
        "pct_change", "annual_openings", "annual_exits", "annual_transfers",
        "annual_change", "in_demand", "demand_rank", "green_job",
        "regional_in_demand", "regional_green", "sector", "projection_source",
        # published-only extras
        "emerging_demand", "high_wage", "median_annual_wage", "mean_annual_wage",
        "education", "work_experience", "on_the_job_training",
    ] if c in df.columns]
    df = df[keep_cols]

    state  = df[df["areatype"] == "01"].copy()
    region = df[df["areatype"] == "15"].copy()

    state_out  = out / f"ks_occ_proj_state_s{sf}.parquet"
    state.to_parquet(state_out, index=False)
    print(f"Saved: {state_out.name}  ({len(state)} state-SOC rows, "
          f"{state['soc_code'].nunique()} unique SOC)")

    region_out = out / f"ks_occ_proj_region_s{sf}.parquet"
    if not region.empty:
        region.to_parquet(region_out, index=False)
        print(f"Saved: {region_out.name} ({len(region)} region-SOC rows, "
              f"{region['areaname'].nunique()} regions)")
    else:
        # Never clobber a good regional file with an empty one: KDOL's published
        # workbook is statewide-only, so the regional layer can only come from a
        # Telerik occproj export. Leaving the prior file in place means it keeps
        # its own (older) vintage, which its base_year/proj_year columns record.
        print(f"  Note: {xls.name} is statewide-only — no regional rows to write.")
        if region_out.exists():
            prior = pd.read_parquet(region_out)
            vint = (f"{prior['base_year'].iloc[0]}-{prior['proj_year'].iloc[0]}"
                    if len(prior) else "unknown")
            print(f"        {region_out.name} left intact at its existing "
                  f"{vint} vintage ({len(prior)} rows).")

    # In-demand list (KDOL Workforce Innovation Board flag = 1)
    # demand_rank is empty in current KDOL exports; sort by annual_openings instead
    if "in_demand" in state.columns:
        in_demand = state[(state["in_demand"] == 1)
                          & (state["annual_openings"].notna())
                          & (state["codelevel"] == 6)] \
            .sort_values("annual_openings", ascending=False)
        if not in_demand.empty:
            top_out = out / f"ks_occ_in_demand_top_s{sf}.parquet"
            in_demand.to_parquet(top_out, index=False)
            print(f"Saved: {top_out.name} ({len(in_demand)} in-demand detail occupations)")
            print("\nTop 15 in-demand KS occupations (by annual openings):")
            print(in_demand.head(15)[
                ["occ_title", "sector", "annual_openings", "pct_change", "base_emp"]
            ].to_string(index=False))

    # Sector rollup — only sum 6-digit detail rows to avoid double-counting
    detail = state[state["codelevel"] == 6].copy() if "codelevel" in state.columns else state.copy()
    sector_roll = (
        detail[detail["sector"].notna()]
        .groupby(["sector", "base_year", "proj_year", "projection_source"],
                  as_index=False)
        .agg(base_emp_total=("base_emp", "sum"),
             proj_emp_total=("proj_emp", "sum"),
             annual_openings=("annual_openings", "sum"),
             n_occupations=("soc_code", "nunique"))
    )
    sector_roll["emp_change_pct"] = (
        (sector_roll["proj_emp_total"] - sector_roll["base_emp_total"])
        / sector_roll["base_emp_total"] * 100
    ).round(1)
    sector_out = out / f"ks_occ_by_sector_s{sf}.parquet"
    sector_roll.to_parquet(sector_out, index=False)
    print(f"\nSaved: {sector_out.name}")
    print(sector_roll.to_string(index=False))


if __name__ == "__main__":
    main()
