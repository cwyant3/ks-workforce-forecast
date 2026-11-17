"""Out-of-sample check of the cohort model's ranges.

Fits the migration model on ACS vintages through 2019, forecasts 2024 from the
2015-2019 baseline, and compares with the 2020-2024 ACS estimate.

    python scripts/backtest_cohort_migration.py 20 08 29 31 40

Limits: one five-year window (pandemic, 2020 Census re-base). With history
through 2019 there is one period, so the persistent share and the statewide
spread are the assumed defaults, not estimates. The 2024 figure is a survey
estimate; "like-for-like" coverage adds its sampling error to the forecast.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cohort_model import CountyCohortModel, fit_migration_model  # noqa: E402

N_SIM, SEED = 2000, 7


def backtest_state(state_fips: str) -> dict:
    acs = pd.read_parquet(ROOT / "data" / "outputs" / f"acs_combined_s{state_fips}.parquet")
    params, info = fit_migration_model(acs[acs.year <= 2019])
    if params is None:
        raise ValueError(f"State {state_fips}: too little history through 2019")
    seeds = np.random.SeedSequence(SEED).spawn(acs.county_fips.nunique() + 1)
    common = np.random.default_rng(seeds[-1]).standard_normal(N_SIM)
    rows, state = [], np.zeros(N_SIM)
    for i, (fips, group) in enumerate(acs.groupby("county_fips")):
        base = group[group.year == 2019].iloc[0].to_dict()
        actual = float(group[group.year == 2024].pop_working_age.iloc[0])
        model = CountyCohortModel(base, [], n_sim=N_SIM, base_year=2019,
                                  migration=params.loc[fips].to_dict())
        _, sims = model.project(2020, 2024, include_simulations=True,
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
    return {"state": state_fips, "counties": len(d),
            "median_abs_pct_error": round(float(error.median()), 2),
            "p90_abs_pct_error": round(float(error.quantile(.9)), 2),
            "state_total_error_pct": round(float((d.p50.sum() / d.actual.sum() - 1) * 100), 2),
            "state_actual_in_80": bool(np.percentile(state, 10) <= d.actual.sum() <= np.percentile(state, 90)),
            "cover80_pct": round(float(d.in80.mean() * 100), 1),
            "cover80_like_for_like_pct": round(float(d.in80_like.mean() * 100), 1),
            "cover90_like_for_like_pct": round(float(d.in90_like.mean() * 100), 1),
            "persistent_share_assumed": info["persistent_share_assumed"]}


if __name__ == "__main__":
    print(pd.DataFrame([backtest_state(s) for s in (sys.argv[1:] or ["20"])]).to_string(index=False))
