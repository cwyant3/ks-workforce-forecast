"""
participation_model.py
Combines ACS working-age population, ACS labor-force-status rates, SSA
disability counts, and optional LAUS context into an effective labor force
estimate.

This addresses model limitation #1: the cohort model tracks working-age
population headcounts but does not model who is actually available to work.

Three-layer stack (per county, per year):
  1. ACS working-age population (18-64)
  2. Optional SSA disability scenario adjustment
  3. ACS civilian labor-force participation rate, with LAUS as fallback/context

SSA records are aligned one-to-one to each ACS vintage using the ACS five-year
period midpoint. The aligned source year is retained for auditability.
"""

from __future__ import annotations

import pandas as pd


def _align_county_source_to_acs(
    base: pd.DataFrame,
    source: pd.DataFrame,
    value_columns: list[str],
    source_year_column: str,
) -> pd.DataFrame:
    """Select one period-aligned source record for each ACS county-year row.

    The ACS five-year period midpoint is the comparison target when available;
    otherwise the ACS vintage year is used. Ties prefer a non-future source and
    then the newest source year. This avoids many-to-many merges and preserves
    the source vintage used for every derived value.
    """
    output_columns = ["county_fips", "year", source_year_column, *value_columns]
    if source.empty:
        return pd.DataFrame(columns=output_columns)

    source_use = source[["county_fips", "year", *value_columns]].copy()
    source_use["county_fips"] = source_use["county_fips"].astype(str).str.zfill(3)
    source_use["year"] = pd.to_numeric(source_use["year"], errors="coerce")
    source_use = source_use.dropna(subset=["county_fips", "year"])
    source_use = source_use.drop_duplicates(["county_fips", "year"], keep="last")

    target_col = "acs_period_midpoint_year" if "acs_period_midpoint_year" in base.columns else "year"
    targets = base[["county_fips", "year", target_col]].drop_duplicates().copy()
    targets["county_fips"] = targets["county_fips"].astype(str).str.zfill(3)

    aligned: list[dict] = []
    for _, target in targets.iterrows():
        county = target["county_fips"]
        target_year = pd.to_numeric(target[target_col], errors="coerce")
        if pd.isna(target_year):
            target_year = pd.to_numeric(target["year"], errors="coerce")

        candidates = source_use[source_use["county_fips"] == county].copy()
        if candidates.empty or pd.isna(target_year):
            continue

        candidates["_distance"] = (candidates["year"] - float(target_year)).abs()
        candidates["_is_future"] = candidates["year"] > float(target_year)
        chosen = candidates.sort_values(
            ["_distance", "_is_future", "year"],
            ascending=[True, True, False],
        ).iloc[0]

        row = {
            "county_fips": county,
            "year": int(target["year"]),
            source_year_column: int(chosen["year"]),
        }
        for column in value_columns:
            row[column] = chosen[column]
        aligned.append(row)

    return pd.DataFrame(aligned, columns=output_columns)


def build_participation_table(
    acs_df: pd.DataFrame,
    ssa_df: pd.DataFrame | None = None,
    laus_df: pd.DataFrame | None = None,
    baseline_year_only: bool = False,
) -> pd.DataFrame:
    """Combine ACS, SSA, and optional LAUS data into a county-year table."""
    required = {"state_fips", "county_fips", "year", "pop_working_age"}
    missing = sorted(required - set(acs_df.columns))
    if missing:
        raise ValueError(f"ACS participation input is missing required columns: {missing}")

    acs_cols = ["state_fips", "county_fips", "year", "pop_working_age"]
    for col in [
        "acs_lf_status_pop_18_64",
        "acs_civilian_labor_force_18_64",
        "acs_armed_forces_18_64",
        "acs_lfpr_pct",
        "acs_period_midpoint_year",
    ]:
        if col in acs_df.columns:
            acs_cols.append(col)

    base = acs_df[acs_cols].copy()
    base["state_fips"] = base["state_fips"].astype(str).str.zfill(2)
    base["county_fips"] = base["county_fips"].astype(str).str.zfill(3)
    base["year"] = pd.to_numeric(base["year"], errors="raise").astype(int)
    base = base.rename(columns={"pop_working_age": "working_age_pop"})

    if baseline_year_only and not base.empty:
        base = base[base["year"] == base["year"].max()].copy()

    duplicate_acs = base.duplicated(["county_fips", "year"], keep=False)
    if duplicate_acs.any():
        keys = base.loc[duplicate_acs, ["county_fips", "year"]].drop_duplicates()
        raise ValueError(
            "ACS participation input contains duplicate county-year rows: "
            f"{keys.to_dict('records')[:5]}"
        )

    # Layer 2: align one SSA observation to each ACS period, never a many-to-many merge.
    has_ssa = ssa_df is not None and not ssa_df.empty
    ssa_value_columns = [
        "ssdi_18_64",
        "ssi_18_64",
        "total_disabled_18_64",
        "disability_rate_pct",
        "disability_adjusted_pop",
    ]
    if has_ssa:
        available = [column for column in ssa_value_columns if column in ssa_df.columns]
        ssa_aligned = _align_county_source_to_acs(
            base,
            ssa_df,
            available,
            source_year_column="ssa_source_year",
        )
        base = base.merge(ssa_aligned, on=["county_fips", "year"], how="left")
    else:
        base["ssa_source_year"] = pd.NA
        for column in ssa_value_columns:
            base[column] = pd.NA

    # Derive totals from component counts when the source did not supply a total.
    if "total_disabled_18_64" not in base.columns:
        base["total_disabled_18_64"] = pd.NA
    total_disabled = pd.to_numeric(base["total_disabled_18_64"], errors="coerce")
    component_columns = [column for column in ["ssdi_18_64", "ssi_18_64"] if column in base.columns]
    if component_columns:
        components = base[component_columns].apply(pd.to_numeric, errors="coerce")
        component_total = components.fillna(0).sum(axis=1).where(components.notna().any(axis=1))
        total_disabled = total_disabled.combine_first(component_total)

    base["total_disabled_18_64"] = total_disabled.round(0).astype("Int64")
    working_age = pd.to_numeric(base["working_age_pop"], errors="coerce")
    valid_disability = total_disabled.notna() & working_age.notna() & (working_age > 0)

    computed_rate = (total_disabled / working_age * 100).round(2).clip(0, 60)
    existing_rate = pd.to_numeric(base.get("disability_rate_pct"), errors="coerce")
    base["disability_rate_pct"] = computed_rate.where(valid_disability, existing_rate)

    computed_adjusted = (working_age - total_disabled).clip(lower=0).round(0)
    existing_adjusted = pd.to_numeric(base.get("disability_adjusted_pop"), errors="coerce")
    adjusted = computed_adjusted.where(valid_disability, existing_adjusted)
    base["disability_adjusted_pop"] = adjusted.fillna(working_age).round(0).astype("Int64")

    # Optional LAUS context. Annual values are averaged only after assigning them
    # to an ACS vintage, which keeps the merge one-to-one.
    has_laus = laus_df is not None and not laus_df.empty
    if has_laus:
        laus_cols = ["county_fips", "year", "labor_force", "lfpr_pct", "lfpr_source"]
        laus_use = laus_df[[c for c in laus_cols if c in laus_df.columns]].copy()
        laus_use["county_fips"] = laus_use["county_fips"].astype(str).str.zfill(3)
        acs_years = sorted(base["year"].unique())
        laus_use["_merge_year"] = laus_use["year"].apply(
            lambda y: min(acs_years, key=lambda a: abs(a - y))
        )
        numeric_laus = [c for c in ["labor_force", "lfpr_pct"] if c in laus_use.columns]
        laus_agg = (
            laus_use.groupby(["county_fips", "_merge_year"], as_index=False)
            .agg({c: "mean" for c in numeric_laus})
            .rename(columns={"_merge_year": "year"})
        )
        base = base.merge(laus_agg, on=["county_fips", "year"], how="left")
    else:
        base["labor_force"] = pd.NA

    # Prefer ACS LFPR row by row; use the LAUS proxy only where ACS is absent.
    laus_lfpr = (
        pd.to_numeric(base["lfpr_pct"], errors="coerce")
        if "lfpr_pct" in base.columns
        else pd.Series(pd.NA, index=base.index, dtype="Float64")
    )
    acs_lfpr = (
        pd.to_numeric(base["acs_lfpr_pct"], errors="coerce")
        if "acs_lfpr_pct" in base.columns
        else pd.Series(pd.NA, index=base.index, dtype="Float64")
    )
    base["lfpr_pct"] = acs_lfpr.combine_first(laus_lfpr)
    base["lfpr_source"] = pd.Series(pd.NA, index=base.index, dtype="object")
    base.loc[acs_lfpr.notna(), "lfpr_source"] = "ACS_B23001_civilian_18_64"
    base.loc[acs_lfpr.isna() & laus_lfpr.notna(), "lfpr_source"] = (
        "LAUS_labor_force_over_ACS_18_64_proxy"
    )

    # Apply LFPR only where available; otherwise preserve the Layer 2 estimate.
    effective = pd.to_numeric(base["disability_adjusted_pop"], errors="coerce")
    has_lfpr = base["lfpr_pct"].notna()
    effective.loc[has_lfpr] = (
        effective.loc[has_lfpr] * base.loc[has_lfpr, "lfpr_pct"] / 100
    ).round(0)
    base["effective_labor_force"] = effective.round(0).astype("Int64")

    def _layers(row) -> str:
        parts = ["ACS"]
        if pd.notna(row.get("ssa_source_year")) and pd.notna(row.get("disability_rate_pct")):
            parts.append("SSA")
        if pd.notna(row.get("acs_lfpr_pct")):
            parts.append("ACS_LFPR")
        elif pd.notna(row.get("lfpr_pct")):
            parts.append("LAUS")
        if has_laus and pd.notna(row.get("labor_force")):
            parts.append("LAUS_CONTEXT")
        return "+".join(parts) if len(parts) > 1 else "ACS_only"

    base["layers_used"] = base.apply(_layers, axis=1)

    duplicate_output = base.duplicated(["county_fips", "year"], keep=False)
    if duplicate_output.any():
        raise ValueError("Participation transformation produced duplicate county-year rows")

    col_order = [
        "state_fips", "county_fips", "year",
        "working_age_pop",
        "acs_lf_status_pop_18_64", "acs_civilian_labor_force_18_64",
        "acs_armed_forces_18_64", "acs_lfpr_pct",
        "ssa_source_year", "ssdi_18_64", "ssi_18_64", "total_disabled_18_64",
        "disability_rate_pct", "disability_adjusted_pop",
        "labor_force", "lfpr_pct", "lfpr_source",
        "effective_labor_force", "layers_used",
    ]
    return base[[c for c in col_order if c in base.columns]].sort_values(
        ["county_fips", "year"]
    ).reset_index(drop=True)


def participation_summary(part_df: pd.DataFrame) -> pd.DataFrame:
    """Return one latest-year participation record per county."""
    if part_df.empty:
        result = part_df.copy()
        result["adjustment_factor"] = pd.Series(dtype="Float64")
        result["adjustment_factor_pct"] = pd.Series(dtype="Float64")
        return result

    latest_year = part_df["year"].max()
    snap = part_df[part_df["year"] == latest_year].copy()
    duplicates = snap.duplicated("county_fips", keep=False)
    if duplicates.any():
        counties = sorted(snap.loc[duplicates, "county_fips"].astype(str).unique())
        raise ValueError(
            "Participation summary contains duplicate county rows for the latest year: "
            f"{counties[:10]}"
        )

    denominator = pd.to_numeric(snap["working_age_pop"], errors="coerce").replace(0, pd.NA)
    snap["adjustment_factor"] = (
        pd.to_numeric(snap["effective_labor_force"], errors="coerce") / denominator
    ).round(4)
    snap["adjustment_factor_pct"] = (snap["adjustment_factor"] * 100).round(2)

    return snap.sort_values("county_fips").reset_index(drop=True)


def project_effective_workforce(
    part_df: pd.DataFrame,
    proj_df: pd.DataFrame,
) -> pd.DataFrame:
    """Scale cohort projections by each county's latest participation factor."""
    summary = participation_summary(part_df)
    adj = (
        summary[["county_fips", "adjustment_factor"]]
        .set_index("county_fips")["adjustment_factor"]
        .to_dict()
    )

    result = proj_df.copy()
    result["county_fips"] = result["county_fips"].astype(str).str.zfill(3)
    result["participation_adj_factor"] = result["county_fips"].map(adj).fillna(1.0)

    pct_cols = ["p25", "p50", "p75", "p90", "mean"]
    for col in pct_cols:
        if col in result.columns:
            result[f"eff_{col}"] = (
                result[col] * result["participation_adj_factor"]
            ).round(0).astype("Int64")

    return result
