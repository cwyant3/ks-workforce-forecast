"""Estimated industry mix of reported credential awards (CIP -> SOC -> NAICS).

There is no official program-to-industry crosswalk. This estimate chains two
published relations and splits each award so it is counted exactly once:

1. CIP -> SOC: NCES CIP 2020-SOC 2018 crosswalk. An award is split equally
   across the detailed occupations its program maps to.
2. SOC -> NAICS: BLS OEWS national employment for the occupation in each
   tracked industry, divided by the occupation's national cross-industry
   employment. Employment outside the tracked industries is kept as
   "All other industries" rather than dropped.

Awards with no crosswalk occupation, or occupations with no national
employment estimate, stay in labelled residual groups. Totals therefore match
the reported award count. This is an allocation by national industry mix, not
a record of where graduates work, local placement or worker supply.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

OES_CACHE = Path(__file__).resolve().parents[1] / "data" / "oes_cache"
OTHER_INDUSTRIES = "All other industries"
NO_OCCUPATION = "No related occupation in crosswalk"
NO_MIX = "Occupation industry mix unavailable"


def industry_weights(by_sector: pd.DataFrame, national: pd.DataFrame) -> pd.DataFrame:
    """Return occupation_code, industry, weight; each occupation's weights sum to 1."""
    totals = national.loc[national.naics.astype(str).eq("000000")
                          & national.o_group.astype(str).eq("detailed"), ["occ_code", "tot_emp"]].copy()
    totals["total"] = pd.to_numeric(totals.tot_emp, errors="coerce")
    totals = totals.dropna(subset=["total"]).loc[lambda t: t.total > 0].drop_duplicates("occ_code")
    sectors = by_sector[["occ_code", "naics", "tot_emp"]].copy()
    sectors["emp"] = pd.to_numeric(sectors.tot_emp, errors="coerce").fillna(0).clip(lower=0)
    sectors = sectors.groupby(["occ_code", "naics"], as_index=False).emp.sum()
    merged = sectors.merge(totals[["occ_code", "total"]], on="occ_code")
    merged["weight"] = merged.emp / merged.total
    # Ownership and suppression differences can push tracked shares past 1; rescale, never exceed it.
    tracked = merged.groupby("occ_code").weight.transform("sum")
    merged.loc[tracked > 1, "weight"] = merged.weight / tracked
    weights = merged.rename(columns={"occ_code": "occupation_code", "naics": "industry"})[
        ["occupation_code", "industry", "weight"]]
    residual = (1 - weights.groupby("occupation_code").weight.sum()).clip(lower=0)
    untracked = totals.loc[~totals.occ_code.isin(weights.occupation_code), "occ_code"]
    residual = pd.concat([residual, pd.Series(1.0, index=untracked.values)])
    other = pd.DataFrame({"occupation_code": residual.index, "industry": OTHER_INDUSTRIES,
                          "weight": residual.values})
    result = pd.concat([weights, other], ignore_index=True)
    return result.loc[result.weight > 0].reset_index(drop=True)


@lru_cache(maxsize=1)
def load_industry_weights() -> tuple[pd.DataFrame, int]:
    by_sector = pd.read_parquet(OES_CACHE / "oes_by_sector.parquet")
    year = int(by_sector.year.max())
    national = pd.read_parquet(OES_CACHE / f"oes_national_{year}.parquet")
    return industry_weights(by_sector.loc[by_sector.year.eq(year)], national), year


def program_industry_share(programs, crosswalk: pd.DataFrame, weights: pd.DataFrame,
                           industry: str) -> pd.Series:
    """Estimated share (0-1) of each program's awards related to one industry.

    Uses the same equal occupation split and national mix as allocate_awards,
    so summing completions * share over programs equals that industry's bar.
    Programs with no crosswalk occupation or no mix get a share of 0.
    """
    programs = pd.Index(pd.unique(pd.Series(list(programs), dtype=str)))
    links = crosswalk[["program_code", "occupation_code"]].drop_duplicates()
    links = links.loc[links.program_code.isin(programs)].copy()
    links["share"] = 1 / links.groupby("program_code").occupation_code.transform("count")
    chosen = weights.loc[weights.industry.eq(industry), ["occupation_code", "weight"]]
    links = links.merge(chosen, on="occupation_code", how="left").fillna({"weight": 0})
    share = (links.share * links.weight).groupby(links.program_code).sum()
    return share.reindex(programs, fill_value=0.0)


def allocate_awards(detail: pd.DataFrame, crosswalk: pd.DataFrame,
                    weights: pd.DataFrame) -> pd.Series:
    """Split award rows across industries; the result sums to the input awards."""
    awards = detail[["program_code", "completions"]].copy()
    awards["completions"] = pd.to_numeric(awards.completions, errors="coerce")
    awards = awards.dropna(subset=["completions"]).groupby("program_code").completions.sum()
    if awards.empty:
        return pd.Series(dtype=float)
    links = crosswalk[["program_code", "occupation_code"]].drop_duplicates()
    links = links.loc[links.program_code.isin(awards.index)].copy()
    links["share"] = 1 / links.groupby("program_code").occupation_code.transform("count")
    links["awards"] = links.program_code.map(awards) * links.share
    pieces = [pd.Series({NO_OCCUPATION: awards[~awards.index.isin(links.program_code)].sum()})]
    mixed = links.merge(weights, on="occupation_code", how="left")
    pieces.append(pd.Series({NO_MIX: mixed.loc[mixed.weight.isna(), "awards"].sum()}))
    mixed = mixed.dropna(subset=["weight"])
    pieces.append((mixed.awards * mixed.weight).groupby(mixed.industry).sum())
    result = pd.concat(pieces)
    return result.loc[result > 0].sort_values(ascending=False)
