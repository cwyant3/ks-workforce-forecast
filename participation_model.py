"""Participation context and the existing approximate 18–64 workforce model.

The baseline uses the ACS B23001 civilian labor-force count (half of the
16–19 age band plus 20–64). This is an age-allocation approximation, not
an exact published 18–64 observation. An ACS-rate-only legacy input is
labelled a proxy; missing ACS participation stays missing. Published exact
16+ observations live in the separate supply-pressure layer.

SSA counts/disability_adjusted_pop remain historical context fields and
are never deducted from the participation baseline. LAUS annual counts
are contextual, not age-compatible numerators for the 18–64 model.

Cohort projections remain unchanged; project_effective_workforce scales
copies using the corrected baseline and preserves unknown adjustments.
"""

import pandas as pd
from pathlib import Path


def build_participation_table(
    acs_df: pd.DataFrame,
    ssa_df: pd.DataFrame | None = None,
    laus_df: pd.DataFrame | None = None,
    baseline_year_only: bool = False,
) -> pd.DataFrame:
    """
    Combine the approximate ACS civilian count with SSA and LAUS context.

    Parameters
    ----------
    acs_df            : output of fetch_acs.fetch_all() — must contain
                        county_fips, year, pop_working_age, state_fips
    ssa_df            : output of fetch_ssa_disability.fetch_ssa_disability()
                        with disability_adjusted_pop and disability_rate_pct
                        (or None to skip Layer 2)
    laus_df           : optional output of fetch_laus.compute_lfpr() with
                        labor_force context columns
    baseline_year_only: if True, return only the most recent ACS year

    Returns
    -------
    One row per county/year; SSA context is not deducted from participation.
    """
    # ── Layer 1: ACS working-age population ──────────────────────────────────
    acs_cols = ["state_fips", "county_fips", "year", "pop_working_age"]
    for col in [
        "acs_lf_status_pop_18_64",
        "acs_civilian_labor_force_18_64",
        "acs_armed_forces_18_64",
        "acs_lfpr_pct",
        "acs_civilian_labor_force_18_64_moe",
        "acs_age_basis",
    ]:
        if col in acs_df.columns:
            acs_cols.append(col)
    if "acs_period_midpoint_year" in acs_df.columns:
        acs_cols.append("acs_period_midpoint_year")

    base = acs_df[acs_cols].copy()
    base = base.rename(columns={"pop_working_age": "working_age_pop"})

    if baseline_year_only:
        base = base[base["year"] == base["year"].max()]

    # ── Layer 2: SSA disability adjustment ───────────────────────────────────
    has_ssa = ssa_df is not None and not ssa_df.empty

    if has_ssa:
        ssa_cols = ["county_fips", "year",
                    "ssdi_18_64", "ssi_18_64", "total_disabled_18_64",
                    "disability_rate_pct", "disability_adjusted_pop"]
        ssa_use = ssa_df[[c for c in ssa_cols if c in ssa_df.columns]].copy()

        # Map SSA year to nearest ACS year for merging
        acs_years = sorted(base["year"].unique())

        def _nearest(y):
            return min(acs_years, key=lambda a: abs(a - y))

        ssa_use["_merge_year"] = ssa_use["year"].apply(_nearest)
        ssa_use = ssa_use.drop(columns=["year"]).rename(
            columns={"_merge_year": "year"}
        )

        base = base.merge(ssa_use, on=["county_fips", "year"], how="left")
    else:
        for col in ["ssdi_18_64", "ssi_18_64", "total_disabled_18_64",
                    "disability_rate_pct"]:
            base[col] = None

    # disability_adjusted_pop: use SSA-derived value, else fall back to Layer 1
    if "disability_adjusted_pop" not in base.columns:
        base["disability_adjusted_pop"] = base["working_age_pop"]
    else:
        base["disability_adjusted_pop"] = base["disability_adjusted_pop"].where(
            base["disability_adjusted_pop"].notna(),
            base["working_age_pop"],
        )

    # ── Optional LAUS context ─────────────────────────────────────────────────
    has_laus = laus_df is not None and not laus_df.empty

    if has_laus:
        laus_cols = ["county_fips", "year", "labor_force", "lfpr_pct", "lfpr_source"]
        laus_use  = laus_df[[c for c in laus_cols if c in laus_df.columns]].copy()

        # LAUS year → nearest ACS year
        laus_use["_merge_year"] = laus_use["year"].apply(
            lambda y: min(sorted(base["year"].unique()), key=lambda a: abs(a - y))
        )
        laus_agg = (
            laus_use.groupby(["county_fips", "_merge_year"], as_index=False)
            .agg({c: "mean" for c in ["labor_force", "lfpr_pct"] if c in laus_use.columns})
            .rename(columns={"_merge_year": "year"})
        )
        base = base.merge(laus_agg, on=["county_fips", "year"], how="left")
    else:
        base["labor_force"] = None

    if "acs_lfpr_pct" in base.columns and base["acs_lfpr_pct"].notna().any():
        base["lfpr_pct"] = base["acs_lfpr_pct"]
        base["lfpr_source"] = "ACS_B23001_civilian_18_64"
    elif "lfpr_pct" not in base.columns:
        base["lfpr_pct"] = None
        base["lfpr_source"] = None
    elif "lfpr_source" not in base.columns:
        base["lfpr_source"] = "LAUS_labor_force_over_ACS_18_64_proxy"

    # ACS already measures participation, including disability's relationship
    # with employment. Subtracting SSA recipients before applying overall LFPR
    # imposes an unsupported second decrement. Prefer the compatible ACS count.
    # Retain legacy SSA columns for context/migration, never as this denominator.
    observed = pd.to_numeric(base.get("acs_civilian_labor_force_18_64",
                             pd.Series(float("nan"), index=base.index)), errors="coerce")
    observed = observed.where(observed >= 0)
    acs_rate = pd.to_numeric(base.get("acs_lfpr_pct",
                            pd.Series(float("nan"), index=base.index)), errors="coerce")
    acs_rate = acs_rate.where(acs_rate.between(0, 100))
    proxy = base["working_age_pop"] * acs_rate / 100
    base["effective_labor_force"] = observed.fillna(proxy).round(0).astype("Int64")
    base["participation_method"] = "unavailable"
    base.loc[proxy.notna(), "participation_method"] = "ACS_rate_applied_to_population_proxy"
    base.loc[observed.notna(), "participation_method"] = "ACS_B23001_age_approximation"
    base["participation_universe"] = "Civilian labor force, approximate ages 18–64"
    base["acs_age_basis"] = "18–64 approximation: half of 16–19 plus ages 20–64"
    base["ssa_role"] = "context_only_no_participation_deduction"
    if "acs_civilian_labor_force_18_64_moe" in base:
        base["effective_labor_force_moe"] = base["acs_civilian_labor_force_18_64_moe"].where(observed.notna())

    # Metadata column: which layers were actually populated
    def _layers(row) -> str:
        parts = ["ACS"]
        if has_ssa and pd.notna(row.get("disability_rate_pct")):
            parts.append("SSA_CONTEXT")
        if pd.notna(row.get("acs_lfpr_pct")):
            parts.append("ACS_LFPR")
        elif has_laus and pd.notna(row.get("lfpr_pct")):
            parts.append("LAUS")
        if has_laus and pd.notna(row.get("labor_force")):
            parts.append("LAUS_CONTEXT")
        return "+".join(parts) if len(parts) > 1 else "ACS_only"

    base["layers_used"] = base.apply(_layers, axis=1)

    col_order = [
        "state_fips", "county_fips", "year",
        "working_age_pop",
        "acs_lf_status_pop_18_64", "acs_civilian_labor_force_18_64",
        "acs_armed_forces_18_64", "acs_lfpr_pct",
        "ssdi_18_64", "ssi_18_64", "total_disabled_18_64",
        "disability_rate_pct", "disability_adjusted_pop",
        "labor_force", "lfpr_pct", "lfpr_source",
        "effective_labor_force", "layers_used",
        "effective_labor_force_moe", "participation_method", "participation_universe",
        "acs_age_basis", "ssa_role",
    ]
    return base[[c for c in col_order if c in base.columns]].sort_values(
        ["county_fips", "year"]
    ).reset_index(drop=True)


def participation_summary(part_df: pd.DataFrame) -> pd.DataFrame:
    """
    Return the most recent year's participation estimates per county,
    with a computed 'adjustment_factor' showing how much the effective
    labor force shrinks relative to the raw ACS working-age population.

    Useful for dashboard KPI cards and county comparison tables.
    """
    latest_year = part_df["year"].max()
    snap = part_df[part_df["year"] == latest_year].copy()

    snap["adjustment_factor"] = (
        snap["effective_labor_force"] / snap["working_age_pop"].replace(0, pd.NA)
    ).round(4)

    snap["adjustment_factor_pct"] = (snap["adjustment_factor"] * 100).round(2)

    return snap.sort_values("county_fips").reset_index(drop=True)


def project_effective_workforce(
    part_df: pd.DataFrame,
    proj_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Scale cohort model projections by the county's participation adjustment factor.

    Multiplies each projected year's p50 / mean / pXX by the ratio:
      effective_labor_force / working_age_pop  (from most recent participation data)

    This converts the cohort model's working-age population projection into
    a projected effective labor force without re-running the simulation.

    Parameters
    ----------
    part_df : output of build_participation_table()
    proj_df : output of cohort_model.run_all_counties()

    Returns
    -------
    proj_df copy with added columns: eff_p50, eff_mean, eff_p25, eff_p75,
    participation_adj_factor
    """
    # Per-county adjustment factor from most recent participation snapshot
    adj = (
        participation_summary(part_df)[["county_fips", "adjustment_factor"]]
        .set_index("county_fips")["adjustment_factor"]
        .to_dict()
    )

    result = proj_df.copy()
    result["participation_adj_factor"] = result["county_fips"].map(adj)

    pct_cols = ["p5", "p10", "p25", "p50", "p75", "p90", "p95", "mean"]
    for col in pct_cols:
        if col in result.columns:
            result[f"eff_{col}"] = (
                result[col] * result["participation_adj_factor"]
            ).round(0).astype("Int64")

    return result
