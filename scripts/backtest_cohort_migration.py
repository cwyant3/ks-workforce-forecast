"""Out-of-sample check of the cohort model's ranges.

Fits the migration model on ACS vintages through a base year, forecasts a
later vintage from the base-year population, and compares with that vintage.

    python scripts/backtest_cohort_migration.py 20 08 29 31 40
    python scripts/backtest_cohort_migration.py 08 29 31 40 --window 2015:2019

The default window is the 2015-2019 ACS to the 2020-2024 ACS. Each state
runs the estimator its forecast uses (forecast_settings.py).

Limits. Two windows at most, and they disagree: 2019 -> 2024 spans the
pandemic and the 2020 Census re-base, 2015 -> 2019 spans neither. Kansas has
one period before 2019, so its persistent share and statewide spread are the
assumed defaults; states with the 2005-2009 vintage fit two periods for the
2019 base and one for the 2015 base. The later figure is a survey estimate;
"like-for-like" coverage adds its sampling error to the forecast.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cohort_model import CountyCohortModel, fit_migration_model  # noqa: E402
from forecast_settings import refined  # noqa: E402

N_SIM, SEED = 2000, 7


def backtest_state(state_fips: str, base_year: int = 2019, target_year: int = 2024) -> dict:
    acs = pd.read_parquet(ROOT / "data" / "outputs" / f"acs_combined_s{state_fips}.parquet")
    use_refined = refined(state_fips)
    params, info = fit_migration_model(acs[acs.year <= base_year], refined=use_refined)
    if params is None:
        raise ValueError(f"State {state_fips}: too little history through {base_year}")
    seeds = np.random.SeedSequence(SEED).spawn(acs.county_fips.nunique() + 1)
    common = np.random.default_rng(seeds[-1]).standard_normal(N_SIM)
    rows, state = [], np.zeros(N_SIM)
    for i, (fips, group) in enumerate(acs.groupby("county_fips")):
        base = group[group.year == base_year].iloc[0].to_dict()
        actual = float(group[group.year == target_year].pop_working_age.iloc[0])
        model = CountyCohortModel(base, [], n_sim=N_SIM, base_year=base_year,
                                  migration=params.loc[fips].to_dict())
        _, sims = model.project(base_year + 1, target_year, include_simulations=True,
                                random_seed=int(seeds[i].generate_state(1)[0]), common_shock=common)
        wf = sims["wf"][:, -1]
        state += wf
        observed = wf * np.exp(model.base_cv * np.random.default_rng(seeds[i].spawn(1)[0]).standard_normal(N_SIM))
        q = lambda values, p: float(np.percentile(values, p))
        rows.append({"size": float(base["pop_working_age"]), "actual": actual, "p50": q(wf, 50),
                     "in80": q(wf, 10) <= actual <= q(wf, 90),
                     "in80_like": q(observed, 10) <= actual <= q(observed, 90),
                     "in90_like": q(observed, 5) <= actual <= q(observed, 95)})
    d = pd.DataFrame(rows)
    error = (d.p50 / d.actual - 1).abs() * 100
    return {"state": state_fips, "window": f"{base_year}->{target_year}",
            "estimator": "refined" if use_refined else "pooled",
            "periods": len(info["chain_years"]) - 1, "counties": len(d),
            "median_abs_pct_error": round(float(error.median()), 2),
            "p90_abs_pct_error": round(float(error.quantile(.9)), 2),
            "state_total_error_pct": round(float((d.p50.sum() / d.actual.sum() - 1) * 100), 2),
            "state_actual_in_80": bool(np.percentile(state, 10) <= d.actual.sum() <= np.percentile(state, 90)),
            "cover80_pct": round(float(d.in80.mean() * 100), 1),
            "cover80_like_for_like_pct": round(float(d.in80_like.mean() * 100), 1),
            "cover90_like_for_like_pct": round(float(d.in90_like.mean() * 100), 1),
            "persistent_share_assumed": info["persistent_share_assumed"]}


if __name__ == "__main__":
    args = sys.argv[1:]
    base_year, target_year = 2019, 2024
    if "--window" in args:
        at = args.index("--window")
        base_year, target_year = (int(v) for v in args[at + 1].split(":"))
        del args[at:at + 2]
    results = []
    for state_fips in (args or ["20"]):
        try:
            results.append(backtest_state(state_fips, base_year, target_year))
        except ValueError as exc:
            print(f"skipped: {exc}")
    if results:
        print(pd.DataFrame(results).to_string(index=False))
