import shutil

import numpy as np
import pytest

from demographic_pipeline import project_cohorts
from demographic_preview import PREVIEW, load_context, pep_population, preview_paths, read
from fertility_model import fertility_schedules
from scripts.build_pep_population_inputs import normalize_population


def test_published_source_selection_and_invalid_rows_rejected():
    body = (PREVIEW/"cc-est2025-syasex-20.csv").read_bytes()
    expected = set(read(PREVIEW/"population_july2025.json")["counties"])
    result = normalize_population(body, expected)
    assert result == read(PREVIEW/"population_july2025.json")
    lines = [line for line in body.decode("utf-8-sig").splitlines() if line.strip()]
    for bad in ["\n".join(lines[:-1]), "\n".join(lines+[lines[1]])]:
        with pytest.raises(ValueError):
            normalize_population(bad.encode(), expected)
    fields = lines[1].split(",")
    fields[-3] = "-1"  # TOT_POP
    with pytest.raises(ValueError, match="reconcile"):
        normalize_population((lines[0]+"\n"+",".join(fields)).encode(), expected)


def test_preview_preserves_source_sex_totals_and_modeled_tail():
    context = load_context("20173")
    pop = context[0]
    source = np.array(read(PREVIEW/"population_july2025.json")["counties"]["20173"]["population"])
    assert np.array_equal(pop[:, :85], source[:, :85])
    assert np.allclose(pop[:, 85:].sum(axis=1), source[:, 85])
    assert np.allclose(pop.sum(axis=1), source.sum(axis=1))
    assert pop.sum() == pytest.approx(538433)


def test_tampered_source_fails_closed(tmp_path):
    for name in ["source_manifest.json", "population_july2025.json", "cc-est2025-syasex-20.csv"]:
        shutil.copyfile(PREVIEW/name, tmp_path/name)
    (tmp_path/"population_july2025.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        pep_population("20173", np.ones((2, 120)), tmp_path)


def test_preview_years_birth_timing_and_original_level_calibration():
    context = load_context("20173")
    paths, schedules = preview_paths(context)
    pop, shares, survival, _, _, _, _, _, county = context
    plain = project_cohorts(pop, shares, survival, base_year=2025)
    for summary, ages in paths.values():
        assert summary.year.tolist() == list(range(2025, 2044))
        assert summary.births.iloc[0] != summary.births.iloc[0]  # no2025 birth injection
        assert summary.births.iloc[1:].equals(summary.births_before_observed_override.iloc[1:])
        assert summary.county_fips.eq(county).all()
        assert np.allclose(summary.labor_force_18_plus, plain.labor_force_18_plus)
        # Baseline age0, rather than any post-baseline birth, reaches18 in2043.
        expected = (pop[:, 0]*np.prod(survival[:, :18], axis=1)).sum()
        assert summary.reaching_18.iloc[-1] == pytest.approx(expected)
        assert np.allclose(ages.sum(axis=(1, 2)), summary.residents_all_ages)
    assert schedules["Low"][2043][30]/schedules["Central"][2043][30] == pytest.approx(.8)
    assert schedules["High"][2043][30]/schedules["Central"][2043][30] == pytest.approx(1.2)
    assert paths["Central"][0].births.iloc[1] != pytest.approx(6645)  #2026 is modeled, not reused2025 anchor


def test_participation_only_leaves_population_and_births_unchanged():
    context = load_context("20071")
    baseline, _ = preview_paths(context)
    changed, _ = preview_paths(context, participation_pp=3)
    for path in baseline:
        assert np.allclose(baseline[path][1], changed[path][1])
        assert np.allclose(baseline[path][0].births.iloc[1:], changed[path][0].births.iloc[1:])
        assert changed[path][0].labor_force_18_plus.iloc[-1] > baseline[path][0].labor_force_18_plus.iloc[-1]


def test_preview_migration_birth_lag_and_zero_scenario_identity():
    context = load_context("20173")
    baseline, _ = preview_paths(context)
    zero, _ = preview_paths(context, 0, 0)
    migration, _ = preview_paths(context, 100)
    for path in baseline:
        assert np.array_equal(baseline[path][1], zero[path][1])
        assert migration[path][0].births.iloc[1] == pytest.approx(baseline[path][0].births.iloc[1])
        assert migration[path][0].births.iloc[2] > baseline[path][0].births.iloc[2]


def test_fertility_horizon_and_adjustment_validation():
    context = load_context("20173")
    for kwargs in [{"horizon": 19}, {"level_adjustment": -1}]:
        with pytest.raises(ValueError):
            fertility_schedules(context[4], context[5], **kwargs)
