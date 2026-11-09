import pandas as pd
import pytest

from dashboard.award_industries import (NO_MIX, NO_OCCUPATION, OTHER_INDUSTRIES,
                                        allocate_awards, industry_weights)


def national(rows):
    return pd.DataFrame([{"naics": "000000", "o_group": "detailed", "occ_code": c, "tot_emp": str(e)}
                         for c, e in rows])


def by_sector(rows):
    return pd.DataFrame([{"naics": n, "occ_code": c, "tot_emp": e} for c, n, e in rows])


def test_weights_keep_untracked_employment_and_sum_to_one():
    w = industry_weights(by_sector([("51-4121", "31-33", 60), ("51-4121", "23", 20),
                                    ("41-2031", "72", 300), ("41-2031", "31-33", 900)]),
                         national([("51-4121", 100), ("41-2031", 1000), ("25-2021", 50)]))
    sums = w.groupby("occupation_code").weight.sum()
    assert sums.round(9).eq(1).all()
    weld = w[w.occupation_code.eq("51-4121")].set_index("industry").weight
    assert weld["31-33"] == pytest.approx(.6) and weld[OTHER_INDUSTRIES] == pytest.approx(.2)
    # Tracked shares above 100% are rescaled rather than inflating awards.
    assert OTHER_INDUSTRIES not in set(w[w.occupation_code.eq("41-2031")].industry)
    # An occupation with no tracked-industry employment falls entirely in other industries.
    assert w[w.occupation_code.eq("25-2021")].industry.tolist() == [OTHER_INDUSTRIES]


def test_allocation_conserves_awards_and_labels_residuals():
    crosswalk = pd.DataFrame({"program_code": ["48.0508", "48.0508", "48.0508", "51.3801"],
                              "occupation_code": ["51-4121", "51-4122", "51-4121", "29-1141"]})
    weights = pd.DataFrame({"occupation_code": ["51-4121", "51-4121"],
                            "industry": ["31-33", OTHER_INDUSTRIES], "weight": [.75, .25]})
    detail = pd.DataFrame({"program_code": ["48.0508", "48.0508", "24.0101", "51.3801"],
                           "completions": [10, 30, 7, 5]})
    mix = allocate_awards(detail, crosswalk, weights)
    assert mix.sum() == pytest.approx(52)
    # 40 welding awards split equally across two distinct occupations (duplicate link ignored).
    assert mix["31-33"] == pytest.approx(15) and mix[OTHER_INDUSTRIES] == pytest.approx(5)
    assert mix[NO_MIX] == pytest.approx(20 + 5)
    assert mix[NO_OCCUPATION] == pytest.approx(7)


def test_program_share_matches_industry_bar():
    from dashboard.award_industries import program_industry_share
    crosswalk = pd.DataFrame({"program_code": ["48.0508", "48.0508", "51.3801"],
                              "occupation_code": ["51-4121", "51-4122", "29-1141"]})
    weights = pd.DataFrame({"occupation_code": ["51-4121", "51-4121", "51-4122"],
                            "industry": ["31-33", OTHER_INDUSTRIES, "31-33"], "weight": [.75, .25, 1.0]})
    detail = pd.DataFrame({"program_code": ["48.0508", "51.3801", "24.0101"], "completions": [40, 5, 7]})
    share = program_industry_share(detail.program_code, crosswalk, weights, "31-33")
    assert share["48.0508"] == pytest.approx(.875)
    assert share["51.3801"] == 0 and share["24.0101"] == 0
    tied = (detail.completions * detail.program_code.map(share)).sum()
    assert tied == pytest.approx(allocate_awards(detail, crosswalk, weights)["31-33"])


def test_empty_detail_returns_empty():
    assert allocate_awards(pd.DataFrame({"program_code": [], "completions": []}),
                           pd.DataFrame({"program_code": [], "occupation_code": []}),
                           pd.DataFrame({"occupation_code": [], "industry": [], "weight": []})).empty
