import numpy as np
import pandas as pd
import pytest

from demographic_pipeline import (AGE_BANDS, DISPLAY_BANDS, single_age_population,
                                  participation_shares, project_cohorts)
from dashboard.demographic_chart import pipeline_figure, entrants_figure


def arrays():
    population = np.zeros((2, 120))
    population[:, 30] = 100
    survival = np.ones((2, 120))
    survival[:, 119] = 0
    return population, np.full((2, 120), .5), survival


def test_known_child_cohort_reaches_eighteen_once_in_eighteenth_year():
    pop, shares, survival = arrays()
    pop[0, 0] = 10
    result = project_cohorts(pop, shares, survival)
    assert result.year.tolist() == list(range(2024, 2043))
    assert result.reaching_18.sum() == 10
    assert result.iloc[-1].reaching_18 == 10
    assert result.iloc[-2].residents_18_plus == 200
    assert result.iloc[-1].residents_18_plus == 210
    assert result.iloc[-1].labor_force_18_plus == 105


def test_age_sixty_five_is_not_automatic_retirement():
    pop, shares, survival = arrays()
    pop[0, 64] = 40
    shares[:, 65:] = .25
    result = project_cohorts(pop, shares, survival)
    assert result.iloc[1].reaching_65 == 40
    assert result.iloc[1]['labor_force_65+'] == 10
    assert result.iloc[1].residents_18_plus == 240
    assert result.iloc[1].labor_force_18_plus == 110


def test_survival_and_annual_aging_conserve_known_cohort_mass():
    pop, shares, survival = arrays()
    survival[:] = .9
    survival[:, 119] = 0
    result = project_cohorts(pop, shares, survival)
    assert result.iloc[-1].residents_18_plus == pytest.approx(200*.9**18)
    for _, row in result.iterrows():
        assert sum(row[f'residents_{band}'] for band in DISPLAY_BANDS) == pytest.approx(row.residents_18_plus)
        assert sum(row[f'labor_force_{band}'] for band in DISPLAY_BANDS) == pytest.approx(row.labor_force_18_plus)


def test_migration_scenario_adds_people_once_and_participation_phases_in():
    pop, shares, survival = arrays()
    baseline = project_cohorts(pop, shares, survival)
    scenario = project_cohorts(pop, shares, survival, annual_net_migrants=10, participation_pp=10)
    assert scenario.iloc[0].labor_force_18_plus == baseline.iloc[0].labor_force_18_plus
    assert scenario.iloc[-1].residents_18_plus == 380
    assert scenario.iloc[-1].net_migrants_applied == 10
    assert scenario.iloc[-1].labor_force_18_plus == pytest.approx(380*.6)
    assert scenario.iloc[9].participation_change_pp == 5


def test_negative_migration_cannot_remove_more_than_available():
    pop, shares, survival = arrays()
    result = project_cohorts(pop, shares, survival, annual_net_migrants=-1000)
    assert (result.residents_18_plus >= 0).all()
    assert (result.net_migrants_applied >= -200).all()
    assert result.iloc[1].net_migrants_applied == -200
    assert result.iloc[2].net_migrants_applied == 0


def test_terminal_age_retains_survivors_in_open_ended_bucket():
    pop, shares, survival = arrays()
    pop[0, 119] = 10
    survival[:, 119] = .1
    result = project_cohorts(pop, shares, survival)
    assert result.iloc[1].residents_18_plus == 201
    assert result.iloc[2].residents_18_plus == pytest.approx(200.1)


@pytest.mark.parametrize('horizon', [0, 19, 18.5])
def test_horizon_cannot_require_unobserved_births(horizon):
    with pytest.raises(ValueError, match='1–18'):
        project_cohorts(*arrays(), horizon=horizon)


def test_single_age_allocation_preserves_sex_totals_and_uses_survivors_above85():
    record = {}
    for start in (3, 27):
        for offset, _ in enumerate(AGE_BANDS):
            record[f'B01001_{start+offset:03d}E'] = 100
    record['B01001_001E'] = 4600
    survivors = np.ones((2, 120))
    survivors[:, 85:90] = 2
    result = single_age_population(record, survivors)
    assert result.sum() == pytest.approx(4600)
    assert result[0, 0] == 20
    assert result[0, 85] == 2*result[0, 100]
    assert result[:, 85:].sum() == pytest.approx(200)
    record['B01001_001E'] = 1
    with pytest.raises(ValueError, match='reconcile'):
        single_age_population(record, survivors)


def test_participation_uses_civilian_counts_and_discloses_proxy():
    labels, record, state = {}, {}, {}
    for sex, name in enumerate(('Male', 'Female')):
        for band, age in enumerate(('16 to 64 years', '65 years and over')):
            prefix = f'Estimate!!Total:!!{name}:!!{age}:'
            for offset, suffix, value in [(0, '', 100), (1, '!!In labor force:', 90),
                                           (2, '!!In labor force:!!Civilian:', 60)]:
                if band == 1 and offset == 2:
                    continue
                key = f'B23001_{sex*20+band*5+offset:03d}E'
                labels[key] = {'label': prefix+suffix}
                record[key] = value
                state[key] = value
    result = participation_shares(record, labels)
    assert result[0, 18] == .6 and result[0, 65] == .9
    record['B23001_000E'] = record['B23001_001E'] = record['B23001_002E'] = 0
    with pytest.raises(ValueError, match='no resident'):
        participation_shares(record, labels)
    rates, proxies = participation_shares(record, labels, state, True)
    assert rates[0, 18] == .6 and len(proxies) == 1


def test_chart_is_age_composition_not_a_probability_band():
    baseline = project_cohorts(*arrays())
    fig = pipeline_figure(baseline, baseline)
    assert len(fig.data) == 6
    assert not any('80%' in str(trace.name) for trace in fig.data)
    assert fig.data[0].stackgroup == 'residents'
    assert fig.data[-1].name == 'Selected scenario'
    assert list(entrants_figure(baseline).data[0].x) == list(range(2025, 2043))
