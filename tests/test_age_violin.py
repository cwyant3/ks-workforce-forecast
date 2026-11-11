import numpy as np
import pytest

from demographic_pipeline import project_cohorts
from dashboard.age_violin import weighted_density, band_summary, scale_for_paths, age_violin_figure


def test_density_preserves_population_weights_instead_of_equal_weighting_ages():
    counts = np.zeros(120)
    counts[30], counts[45] = 100, 1000
    ages, density = weighted_density(counts, 25, 54)
    assert np.trapezoid(density, ages) == pytest.approx(1100)
    assert density[np.abs(ages-45).argmin()] > 9*density[np.abs(ages-30).argmin()]


def test_slider_states_move_children_into_adult_ranges_and_keep_summaries_consistent():
    pop = np.zeros((2, 120))
    pop[0, 17], pop[1, 30] = 50, 100
    summary, states = project_cohorts(pop, np.ones_like(pop)*.5, np.ones_like(pop), return_age_states=True)
    assert states.shape == (19, 2, 120)
    assert states[0, 0, 17] == 50 and states[1, 0, 18] == 50 and states[18, 0, 35] == 50
    assert band_summary(states[0]).iloc[0].Residents == 50
    assert band_summary(states[18]).iloc[0].Residents == 0
    for row, state in zip(summary.itertuples(), states):
        assert state[:, 18:].sum() == pytest.approx(row.residents_18_plus)
        assert band_summary(state).Residents.sum() == pytest.approx(state.sum())
    states[0, 0, 17] = 999
    assert states[1, 0, 18] == 50


def test_common_scale_halves_width_when_population_halves():
    state = np.ones((2, 120))*20
    scale = scale_for_paths(np.stack([state, state/2]))
    full = age_violin_figure(state, 2024, scale)
    half = age_violin_figure(state/2, 2042, scale)
    assert np.ptp(full.data[0].x) == pytest.approx(2*np.ptp(half.data[0].x))
    assert full.layout.yaxis.range == half.layout.yaxis.range
    assert '2042' in half.layout.title.text


def test_percent_change_versus_baseline_is_signed_per_band():
    base = np.zeros((2, 120))
    base[0, 10], base[0, 30], base[0, 70] = 100, 200, 50
    now = np.zeros((2, 120))
    now[0, 10], now[0, 30], now[0, 70] = 50, 300, 50
    summary = band_summary(now, base)
    change = dict(zip(summary["Age range"], summary["Change vs baseline (%)"]))
    assert change["0–17"] == pytest.approx(-50) and change["25–54"] == pytest.approx(50)
    assert change["65+"] == pytest.approx(0) and np.isnan(change["18–24"])
    fig = age_violin_figure(now, 2030, scale_for_paths(np.array([now])), base, 2024)
    texts = [a.text for a in fig.layout.annotations]
    assert "<b>▼ −50.0%</b>" in texts and "<b>▲ +50.0%</b>" in texts and "<b>n/a</b>" in texts
    assert not age_violin_figure(base, 2024, 1.0, base, 2024).layout.annotations


def test_empty_band_keeps_zero_label_without_inventing_distribution():
    state = np.zeros((2, 120))
    state[0, 50] = 100
    fig = age_violin_figure(state, 2042, scale_for_paths(np.array([state])))
    assert len(fig.data) == 4
    assert fig.layout.xaxis.ticktext[0] == '0–17<br>0'
    assert np.isnan(band_summary(state).iloc[0]['Median age'])
