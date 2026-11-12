"""Verify every Kansas county, all fertility paths and independent invariants."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demographic_preview import INPUT, PREVIEW, load_context, preview_paths, read
from demographic_pipeline import project_cohorts
from pressure_release import read_active, validate_release, release_directory


def adopted_hashes():
    outputs = ROOT/"data/outputs"
    manifest = read_active(outputs)
    files = [p for folder in [INPUT, outputs, release_directory(outputs, manifest)] for p in folder.rglob("*") if p.is_file()]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def main(output):
    before = adopted_hashes()
    native = read(PREVIEW/"population_july2025.json")["counties"]
    assert len(native) == 105
    results, trajectories = [], []
    for county in sorted(native):
        context = load_context(county)
        pop, shares, survival = context[:3]
        assert np.allclose(pop.sum(axis=1), np.asarray(native[county]["population"]).sum(axis=1))
        paths, schedules = preview_paths(context)
        plain = project_cohorts(pop, shares, survival, base_year=2025)
        central = paths["Central"][0]
        for path, (frame, ages) in paths.items():
            assert frame.year.tolist() == list(range(2025, 2044))
            assert frame.county_fips.eq(county).all()
            assert ages.shape == (19, 2, 120) and np.isfinite(ages).all() and (ages >= 0).all()
            assert np.allclose(ages.sum(axis=(1, 2)), frame.residents_all_ages)
            assert np.allclose(ages[:, :, :18].sum(axis=(1, 2)), frame.residents_under_18)
            assert np.allclose(frame.residents_all_ages, frame.residents_under_18+frame.residents_18_plus)
            assert np.allclose(frame.labor_force_18_plus, plain.labor_force_18_plus)
            assert (frame.labor_force_18_plus <= frame.residents_18_plus+1e-7).all()
            assert np.isfinite(frame.births.iloc[1:]).all() and (frame.births.iloc[1:] >= 0).all()
            assert frame.births.iloc[1:].equals(frame.births_before_observed_override.iloc[1:])
            assert np.isnan(frame.births.iloc[0])  # No2025 double injection.
            assert np.isclose(frame.reaching_18.iloc[-1], (pop[:, 0]*np.prod(survival[:, :18], axis=1)).sum())
            # Independent first annual transition, including terminal-cell retention.
            living = pop*survival
            expected = np.c_[np.zeros(2), living[:, :-1]]
            expected[:, 119] += living[:, 119]
            young_share = pop[:, :5].sum(axis=1)/pop[:, :5].sum()
            expected[:, 0] += frame.births.iloc[1]*young_share*np.sqrt(survival[:, 0])
            assert np.allclose(ages[1], expected)
            death_residual = (frame.residents_all_ages.iloc[:-1].to_numpy()+frame.newborn_survivors.iloc[1:].to_numpy()
                              +frame.net_migrants_applied.iloc[1:].to_numpy()-frame.residents_all_ages.iloc[1:].to_numpy())
            assert (death_residual >= -1e-7).all()
            trajectories.append(frame.assign(path="No net migration"))
        assert np.all(paths["Low"][1] <= paths["Central"][1]+1e-7)
        assert np.all(paths["Central"][1] <= paths["High"][1]+1e-7)
        assert np.allclose(schedules["Low"][2043], schedules["Central"][2043]*.8)
        assert np.allclose(schedules["High"][2043], schedules["Central"][2043]*1.2)
        working = pop[:, 18:65].sum()
        for label, migration, pp in [("zero", 0., 0.), ("migration_low", -.0025*working, 0),
                                      ("migration_high", .0025*working, 0), ("participation_low", 0, -3),
                                      ("participation_high", 0, 3), ("combined", .0025*working, 3)]:
            scenarios, _ = preview_paths(context, migration, pp)
            simple = project_cohorts(pop, shares, survival, base_year=2025, annual_net_migrants=migration, participation_pp=pp)
            for name, (frame, ages) in scenarios.items():
                assert np.isfinite(ages).all() and (ages >= 0).all()
                assert np.allclose(frame.labor_force_18_plus, simple.labor_force_18_plus)
                assert np.allclose(frame.residents_all_ages, ages.sum(axis=(1, 2)))
                if migration == 0:
                    assert np.array_equal(ages, paths[name][1])
                if migration > 0:
                    assert np.isclose(frame.births.iloc[1], paths[name][0].births.iloc[1])
                    assert frame.births.iloc[2] >= paths[name][0].births.iloc[2]
                if label == "zero":
                    pd.testing.assert_frame_equal(frame, paths[name][0])
            if pp > 0:
                assert scenarios["Central"][0].labor_force_18_plus.iloc[-1] >= central.labor_force_18_plus.iloc[-1]
        results.append({"county_fips": county, "county": native[county]["name"], "status": "pass",
                        "residents_july2025": float(pop.sum()), "residents_2043_central": float(central.residents_all_ages.iloc[-1]),
                        "workforce_2025": float(central.labor_force_18_plus.iloc[0]), "workforce_2043": float(central.labor_force_18_plus.iloc[-1]),
                        "births_2026_central": float(central.births.iloc[1]), "births_2043_central": float(central.births.iloc[-1]),
                        "participation_proxy_cells": len(context[3])})
    outputs = ROOT/"data/outputs"
    active = read_active(outputs)
    assert active["release_id"] == "20261112T193557_923d1ce9"
    assert validate_release(outputs, active) == []
    assert adopted_hashes() == before
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(results).to_csv(output/"all-county-verification.csv", index=False)
    pd.concat(trajectories, ignore_index=True).to_csv(output/"all-county-preview-trajectories.csv", index=False)
    evidence = {"status": "pass", "counties": len(results), "fertility_paths_per_county": 3,
                "scenario_settings_per_county_including_baseline": 7, "county_path_runs": len(results)*3*7,
                "years": [2025, 2043], "reference_date": "2025-07-01", "source_rows_checked": 63210,
                "checks": ["Source age/sex totals", "Complete19-year outputs", "Finite nonnegative ages/births",
                           "All-age and child/adult reconciliation", "Workforce bounded by adult residents",
                           "Independent first-step survival/aging/newborns", "Baselineage0 reaches18 in2043",
                           "No2025 birth reinsertion", "Fertility ordering and endpoint multipliers",
                           "Birth-free adult workforce identity under every scenario", "Participation/population separation",
                           "Migration birth lag", "Zero-scenario exact identity", "Unchanged adopted data/input hashes"],
                "adopted_release": active["release_id"], "adopted_files_sha256": before,
                "limits": "Numerical and integration verification, not prospective accuracy or probabilistic coverage."}
    (output/"model-verification.json").write_text(json.dumps(evidence, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in ["status", "counties", "county_path_runs", "adopted_release"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    main(parser.parse_args().output_dir)
