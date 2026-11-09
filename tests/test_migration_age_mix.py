import numpy as np
import pytest

from demographic_pipeline import project_cohorts
from migration_age_mix import (county_mixes, county_shares, in_mover_age_mix, metro_mix,
                               working_age_net_migrants)

BANDS = ["1 to 4 years", "5 to 17 years", "18 and 19 years", "20 to 24 years", "25 to 64 years",
         "65 to 74 years", "75 years and over"]
ORIGINS = ["Same house 1 year ago", "Moved within same county",
           "Moved from different county within same state", "Moved from different state", "Moved from abroad"]


def synthetic(counts):
    """Labels/record shaped like B07001; counts[origin] lists one value per band."""
    labels, record, n = {}, {}, 1
    for origin in ORIGINS:
        labels[f"B07001_{n:03d}E"] = {"label": f"Estimate!!Total:!!{origin}:"}
        n += 1
        for band, value in zip(BANDS, counts.get(origin, [0] * len(BANDS))):
            labels[f"B07001_{n:03d}E"] = {"label": f"Estimate!!Total:!!{origin}:!!{band}"}
            record[f"B07001_{n:03d}E"] = str(value)
            record[f"B07001_{n:03d}M"] = "10"
            n += 1
    return record, labels


def test_share_counts_only_ages_18_to_64_from_outside_the_county():
    record, labels = synthetic({
        "Moved within same county": [999] * 7,  # excluded: not a border crossing
        "Moved from different county within same state": [5, 10, 5, 10, 40, 20, 10],
        "Moved from different state": [0, 0, 0, 10, 10, 0, 0],
        "Moved from abroad": [0, 0, 0, 0, 0, 0, 0],
    })
    mix = in_mover_age_mix(record, labels)
    assert mix["in_movers"] == 120
    assert mix["in_movers_18_64"] == 75
    assert mix["share_18_64"] == pytest.approx(75 / 120)
    assert 0 < mix["share_moe90"] < 1
    state_only = in_mover_age_mix(record, labels, ("other_state", "abroad"))
    assert state_only["share_18_64"] == 1


def test_suppressed_or_empty_in_movers_withhold_the_share():
    record, labels = synthetic({"Moved from different state": [0] * 7})
    with pytest.raises(ValueError, match="No in-movers"):
        in_mover_age_mix(record, labels)
    record, labels = synthetic({"Moved from different state": [-666666666] + [1] * 6})
    with pytest.raises(ValueError, match="Suppressed"):
        in_mover_age_mix(record, labels)


def test_tracked_acs_inputs_reproduce_the_published_metro_share():
    # 2020-24 ACS B07001, Wichita KS Metro (48620): 12,694 of 17,294 movers from
    # another state or abroad were ages 18-64.
    metro = metro_mix(("other_state", "abroad"))
    assert (metro["in_movers_18_64"], metro["in_movers"]) == (12694, 17294)
    assert len(county_mixes()) == 105
    shares = county_shares()
    for fips in ("20015", "20079", "20173", "20191"):  # Wichita MSA counties use their own share
        assert shares[fips]["basis"] == "county"
    pooled = {mix["share_applied"] for mix in shares.values() if mix["basis"] == "kansas_pooled"}
    assert len(pooled) == 1 and 0.6 < pooled.pop() < 0.8
    assert all(0.5 < mix["share_applied"] < 0.9 for mix in shares.values())


def test_working_age_conversion_validates_inputs():
    assert working_age_net_migrants(-1000, .7) == pytest.approx(-700)
    for bad in (0, 1.2, np.nan):
        with pytest.raises(ValueError):
            working_age_net_migrants(100, bad)


def arrays():
    population = np.zeros((2, 120))
    population[:, 20:60] = 100
    population[:, 5] = 50
    survival = np.full((2, 120), .99)
    return population, np.full((2, 120), .6), survival


def test_all_age_migrants_enter_cohorts_only_at_their_working_age_share():
    pop, shares, survival = arrays()
    adjusted = project_cohorts(pop, shares, survival, horizon=10, all_age_net_migrants=1000, working_age_share=.73)
    direct = project_cohorts(pop, shares, survival, horizon=10, annual_net_migrants=730)
    naive = project_cohorts(pop, shares, survival, horizon=10, annual_net_migrants=1000)
    np.testing.assert_allclose(adjusted.labor_force_18_plus, direct.labor_force_18_plus)
    assert adjusted.iloc[1:].net_migrants_applied.tolist() == pytest.approx([730] * 10)
    assert adjusted.migrant_working_age_share.iloc[-1] == .73
    assert adjusted.all_age_net_migrants_assumption.iloc[-1] == 1000
    assert np.isnan(direct.migrant_working_age_share.iloc[-1])
    base = project_cohorts(pop, shares, survival, horizon=10)
    gain = adjusted.labor_force_18_plus - base.labor_force_18_plus
    naive_gain = naive.labor_force_18_plus - base.labor_force_18_plus
    assert gain.iloc[-1] == pytest.approx(.73 * naive_gain.iloc[-1], rel=1e-9)


def test_negative_all_age_migration_uses_the_same_share():
    pop, shares, survival = arrays()
    adjusted = project_cohorts(pop, shares, survival, horizon=5, all_age_net_migrants=-500, working_age_share=.7)
    direct = project_cohorts(pop, shares, survival, horizon=5, annual_net_migrants=-350)
    np.testing.assert_allclose(adjusted.labor_force_18_plus, direct.labor_force_18_plus)


@pytest.mark.parametrize("kwargs", [
    dict(all_age_net_migrants=100),
    dict(working_age_share=.7),
    dict(all_age_net_migrants=100, working_age_share=0),
    dict(all_age_net_migrants=100, working_age_share=1.5),
    dict(all_age_net_migrants=np.inf, working_age_share=.7),
    dict(all_age_net_migrants=100, working_age_share=.7, annual_net_migrants=50),
])
def test_all_age_migration_inputs_are_validated(kwargs):
    pop, shares, survival = arrays()
    with pytest.raises(ValueError):
        project_cohorts(pop, shares, survival, horizon=3, **kwargs)
