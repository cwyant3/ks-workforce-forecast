"""
Validate generated forecast outputs for structural and calculation issues.

Usage:
    python scripts/validate_outputs.py
    python scripts/validate_outputs.py --outputs data/outputs --state 20
"""

from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path

import pandas as pd


# ── JOLTS vintage-recency thresholds ──────────────────────────────────────
# JOLTS is monthly, published with a ~1-month reference lag (July data lands
# on ~September 1). So the newest reference month should normally sit 1–2
# months behind today. 120 days allows for the lag plus a release slip (the
# Dec 2025 reference month slipped from 2026-02-03 to 02-05 during the
# appropriations lapse) without tolerating a genuinely stuck layer.
#
# Sized deliberately loose. The failure this is here to catch is not a
# late release — it is a layer frozen for YEARS. This layer sat at
# 2023-12 for 31 months while every scheduled refresh reported success,
# because fetch_jolts.py held a hardcoded year list and returned its
# undated cache on sight. Nothing in this validator looked at JOLTS at
# all, so nothing failed. A recency assertion would have caught it in
# month two. See docs/data-refresh-log.md [2026-09-02].
JOLTS_MAX_STALE_DAYS = 120

JOLTS_SECTORS = {
    "Healthcare",
    "Hospitality & Entertainment",
    "IT/Computer Services",
    "Manufacturing",
    "Skilled Trades",
}


REQUIRED_SUMMARY_COLUMNS = {
    "county_fips",
    "county_name",
    "workforce_base",
    "pop_total_base",
    "wf_end_p10",
    "wf_end_p50",
    "wf_end_p90",
    "pct_change_end",
    "annual_retirements_end",
    "annual_entries_end",
    "state_fips",
}


def _failures_for_summary(path: Path, expected_state: str | None = None) -> list[str]:
    df = pd.read_csv(path, dtype={"county_fips": str, "state_fips": str})
    failures: list[str] = []

    missing = sorted(REQUIRED_SUMMARY_COLUMNS - set(df.columns))
    if missing:
        return [f"{path.name}: missing columns {missing}"]

    state_from_name = path.stem.rsplit("_s", 1)[-1].zfill(2)
    expected = (expected_state or state_from_name).zfill(2)
    states = {str(v).zfill(2) for v in df["state_fips"].dropna().unique()}
    if states != {expected}:
        failures.append(f"{path.name}: state_fips {sorted(states)} != expected {expected}")

    if df["county_fips"].duplicated().any():
        failures.append(f"{path.name}: duplicated county_fips")

    numeric_cols = [
        "workforce_base",
        "pop_total_base",
        "wf_end_p10",
        "wf_end_p50",
        "wf_end_p90",
        "annual_retirements_end",
        "annual_entries_end",
    ]
    if df[numeric_cols].isna().any().any():
        failures.append(f"{path.name}: missing numeric values")
    if (df[numeric_cols] < 0).any().any():
        failures.append(f"{path.name}: negative numeric values")
    if (df["workforce_base"] > df["pop_total_base"]).any():
        failures.append(f"{path.name}: workforce_base exceeds pop_total_base")
    if ((df["wf_end_p10"] > df["wf_end_p50"]) | (df["wf_end_p50"] > df["wf_end_p90"])).any():
        failures.append(f"{path.name}: P10/P50/P90 ordering violation")

    recalc_pct = ((df["wf_end_p50"] - df["workforce_base"]) / df["workforce_base"] * 100).round(2)
    if ((recalc_pct - df["pct_change_end"]).abs() > 0.011).any():
        failures.append(f"{path.name}: pct_change_end does not reconcile")

    return failures


def _failures_for_state_projection(path: Path) -> list[str]:
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        return [f"{path.name}: could not read parquet ({exc})"]

    failures: list[str] = []
    required = {"year", "p10", "p50", "p90", "pct_change_p50", "aggregate_method", "state_fips"}
    missing = sorted(required - set(df.columns))
    if missing:
        failures.append(f"{path.name}: missing columns {missing}")
    if {"p10", "p50", "p90"}.issubset(df.columns):
        if ((df["p10"] > df["p50"]) | (df["p50"] > df["p90"])).any():
            failures.append(f"{path.name}: P10/P50/P90 ordering violation")
    if "aggregate_method" in df.columns:
        bad = df["aggregate_method"].ne("percentile_of_aggregate_simulations").any()
        if bad:
            failures.append(f"{path.name}: aggregate_method is not simulation-based")
    return failures


def _failures_for_jolts(outputs: Path, today: _dt.date | None = None) -> list[str]:
    """
    Validate the two national JOLTS outputs.

    These are national files (no _s{fips} suffix) — every state in the
    dashboard reads the same pair, so they are checked once per run rather
    than per state.

    Absence is NOT a failure: run_forecast.py only writes these when invoked
    with --jolts, and the dashboard degrades gracefully to "not loaded".
    A file that is present but stale, truncated, or internally inconsistent
    IS a failure.
    """
    monthly_path = outputs / "jolts.parquet"
    annual_path = outputs / "jolts_vacancy_rates.parquet"

    if not monthly_path.exists() and not annual_path.exists():
        return []   # JOLTS layer not generated this run

    failures: list[str] = []
    today = today or _dt.date.today()

    # One file without the other means the pair was written inconsistently.
    for path in (monthly_path, annual_path):
        if not path.exists():
            failures.append(f"{path.name}: missing while the other JOLTS output is present")
    if failures:
        return failures

    try:
        monthly = pd.read_parquet(monthly_path)
        annual = pd.read_parquet(annual_path)
    except Exception as exc:
        return [f"jolts: could not read parquet ({exc})"]

    required_monthly = {"year", "month", "sector", "data_element", "measure", "value"}
    required_annual = {"year", "sector", "vacancy_rate_pct", "vacancy_rate_trend_slope"}
    for name, df, required in (
        ("jolts.parquet", monthly, required_monthly),
        ("jolts_vacancy_rates.parquet", annual, required_annual),
    ):
        if df.empty:
            failures.append(f"{name}: empty")
            continue
        missing = sorted(required - set(df.columns))
        if missing:
            failures.append(f"{name}: missing columns {missing}")
    if failures:
        return failures

    # ── Recency. The check that would have caught the 31-month freeze. ──
    newest = monthly.loc[monthly[["year", "month"]].apply(tuple, axis=1).idxmax()]
    newest_year, newest_month = int(newest["year"]), int(newest["month"])
    # Reference months are whole months; age from the END of that month.
    ref_end = (
        _dt.date(newest_year + (newest_month // 12), (newest_month % 12) + 1, 1)
        - _dt.timedelta(days=1)
    )
    age_days = (today - ref_end).days
    if age_days > JOLTS_MAX_STALE_DAYS:
        failures.append(
            f"jolts.parquet: newest reference month {newest_year}-{newest_month:02d} "
            f"is {age_days}d old (limit {JOLTS_MAX_STALE_DAYS}d) — the layer is "
            f"stale, not merely awaiting a release. Check for a hardcoded year "
            f"list or a cache served without a freshness check in fetch_jolts.py."
        )

    # ── Annual file must contain only COMPLETE years. ──
    # An annual average over a partial year is not an annual average, and the
    # dashboard takes year.max() as its headline vacancy rate and feeds each
    # year to the trend-slope regression as an equally-weighted point.
    months_per_year = monthly.groupby("year")["month"].nunique()
    complete = {int(y) for y, n in months_per_year.items() if n >= 12}
    annual_years = {int(y) for y in annual["year"].unique()}

    incomplete_in_annual = sorted(annual_years - complete)
    if incomplete_in_annual:
        detail = ", ".join(
            f"{y} ({int(months_per_year.get(y, 0))}/12 months)" for y in incomplete_in_annual
        )
        failures.append(
            f"jolts_vacancy_rates.parquet: annual averages include incomplete "
            f"years: {detail}"
        )

    # Complete years present monthly but absent from the annual file mean the
    # aggregation silently dropped data.
    if complete and (dropped := sorted(complete - annual_years)):
        failures.append(
            f"jolts_vacancy_rates.parquet: complete years {dropped} present in "
            f"jolts.parquet but missing from the annual averages"
        )

    # ── Sector coverage and value sanity. ──
    for name, df in (("jolts.parquet", monthly), ("jolts_vacancy_rates.parquet", annual)):
        absent = sorted(JOLTS_SECTORS - set(df["sector"].dropna().unique()))
        if absent:
            failures.append(f"{name}: missing dashboard sectors {absent}")

    # Scoped to the five dashboard sectors on purpose. The frame also carries
    # a "Total" row, and its null rate is CORRECT rather than a defect:
    # _build_series_ids requests only the total-nonfarm openings LEVEL
    # (JT?000000000000000JOL) as a scale reference, with no matching rate
    # series, so Total has openings_thousands and no percentages. Only the
    # five mapped supersectors are required to carry rates.
    sector_rows = annual[annual["sector"].isin(JOLTS_SECTORS)]
    rates = sector_rows["vacancy_rate_pct"]
    if rates.isna().any():
        bad = sorted(sector_rows.loc[rates.isna(), "sector"].unique())
        failures.append(f"jolts_vacancy_rates.parquet: null vacancy_rate_pct for {bad}")
    # JOLTS openings rates have never exceeded ~10% for any supersector; 25%
    # is a wide bound that only trips on a unit or parsing error.
    elif ((rates < 0) | (rates > 25)).any():
        failures.append(
            f"jolts_vacancy_rates.parquet: vacancy_rate_pct outside 0–25 "
            f"(min {rates.min()}, max {rates.max()}) — likely a unit or parse error"
        )

    # A single-year annual file cannot support a trend slope; the fetcher
    # needs >=3 years and returns None below that.
    if len(annual_years) >= 3:
        if sector_rows["vacancy_rate_trend_slope"].isna().any():
            bad = sorted(
                sector_rows.loc[
                    sector_rows["vacancy_rate_trend_slope"].isna(), "sector"
                ].unique()
            )
            failures.append(
                f"jolts_vacancy_rates.parquet: null vacancy_rate_trend_slope for "
                f"{bad} despite {len(annual_years)} years of data"
            )

    return failures


def validate(outputs: Path, state: str | None = None) -> list[str]:
    failures: list[str] = []
    summary_files = sorted(outputs.glob("county_summary_s*.csv"))
    if state:
        summary_files = [outputs / f"county_summary_s{state.zfill(2)}.csv"]
    for path in summary_files:
        if path.exists():
            failures.extend(_failures_for_summary(path, state))
        else:
            failures.append(f"{path.name}: missing")

    state_files = sorted(outputs.glob("state_projection_s*.parquet"))
    if state:
        state_files = [outputs / f"state_projection_s{state.zfill(2)}.parquet"]
    for path in state_files:
        if path.exists():
            failures.extend(_failures_for_state_projection(path))

    # National layers — checked once regardless of --state, since every
    # state's dashboard reads the same files.
    failures.extend(_failures_for_jolts(outputs))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", default=Path(__file__).resolve().parents[1] / "data" / "outputs",
                        type=Path)
    parser.add_argument("--state", default=None, help="Optional 2-digit state FIPS")
    args = parser.parse_args()

    failures = validate(args.outputs, args.state)
    if failures:
        print("Output validation failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Output validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
