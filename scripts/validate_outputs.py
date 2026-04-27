"""
Validate generated forecast outputs for structural and calculation issues.

Usage:
    python scripts/validate_outputs.py
    python scripts/validate_outputs.py --outputs data/outputs --state 20
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


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
