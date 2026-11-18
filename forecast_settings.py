"""Which states run the refined working-age forecast (2026-11-18).

Colorado, Missouri, Nebraska and Oklahoma use, together:

  - the 2005-2009 ACS vintage, a third non-overlapping period for the
    migration model (2009 -> 2015 -> 2019 -> 2024);
  - the refined migration estimator (cohort_model.fit_migration_model with
    refined=True): survey error taken from published ACS margins, volatility
    that grows as counties get smaller, and a mean that tilts with county size
    around the population-weighted statewide rate;
  - a youth update that leaves out schools NCES classes as fully virtual,
    whose students do not live in the district office's county.

Kansas is deliberately absent. Its forecast sits behind adopted release
20261117T221214_8929b5f9, and moving it is a release decision, not a side
effect of a code change. docs/cohort-migration-model.md records what the same
settings would do to Kansas. Chris decided on 2026-11-18 to leave Kansas as
adopted, so do not add "20" here without a new decision.
"""

REFINED_STATES = frozenset({"08", "29", "31", "40"})
REFINED_EXTRA_ACS_YEARS = (2009,)


def refined(state_fips: str) -> bool:
    return str(state_fips).zfill(2) in REFINED_STATES


def extra_acs_years(state_fips: str) -> tuple[int, ...]:
    return REFINED_EXTRA_ACS_YEARS if refined(state_fips) else ()
