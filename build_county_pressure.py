"""Build a validated county pressure release without running the population forecast.

    python build_county_pressure.py --evidence-dir data/pressure_sources/2026-10-20
    python build_county_pressure.py --evidence-dir ... --adopt
    python build_county_pressure.py --rollback RELEASE_ID

The input folder contains public downloaded source artifacts, not processed
estimates. A failed build never changes the adopted pointer. Source discovery
and live adapters are separate from this reproducible adoption command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from data_contracts import normalize_observations, validate_observations
from demand_pressure import build_demand_indicators
from supply_pressure import build_supply_indicators
from training_supply import build_training_indicators
from fetch_ipeds import summarize_by_sector
from fetch_oes import build_oews_observations
from participation_model import build_participation_table, project_effective_workforce
from scripts.parse_manual_ks_occproj import build_regional_context
from pressure_release import stage_release, adopt_release, rollback_release, resolve_output, read_active

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "data/outputs"
PILOT = ["20015", "20035", "20077", "20079", "20095", "20173", "20191"]


def statewide_counties() -> list[str]:
    """All 105 Kansas county FIPS codes, from the versioned geography crosswalk."""
    from geography import county_area_relations
    return sorted(county_area_relations().county_fips5)


def read_optional(outputs, name):
    path = resolve_output(outputs, name)
    return pd.read_parquet(path) if path.exists() else None


def build(evidence_dir: Path, outputs: Path = OUTPUTS, *, correction_dir: Path | None = None,
          release_id: str | None = None, counties: list[str] | None = None) -> dict:
    """Stage a release. ``counties`` defaults to the 7-county pilot; pass
    ``statewide_counties()`` (CLI ``--statewide``) for all 105 Kansas counties."""
    evidence_dir = Path(evidence_dir)
    scope_counties = list(PILOT if counties is None else counties)
    # Required sources fail closed. An optional layer gets a visible availability
    # record rather than blocking independently valid sources.
    demand = build_demand_indicators(evidence_dir, scope_counties)
    supply = build_supply_indicators(evidence_dir)
    if supply.empty:
        raise ValueError("Resident supply evidence is required")
    training, detail = build_training_indicators(evidence_dir, scope_counties)
    pieces = [demand, supply, training]
    if any((evidence_dir / name).exists() for name in ("oews-wichita-2025-sample.json", "oews-kansascity-2025.json")):
        pieces.append(build_oews_observations(evidence_dir))
    lodes = evidence_dir / "lodes_observations.parquet"
    if lodes.exists():
        pieces.append(pd.read_parquet(lodes))
    else:
        pieces.append(pd.DataFrame([dict(source_id="lodes", measure_id="primary_job_commuting",
            geography_id=county, period_start="2023-01-01", period_end="2023-12-31",
            frequency="annual", estimate=None, unit="primary jobs", availability_status="not_reported",
            location_basis="home and workplace", job_universe="JT01", release_id="LODES8.4-20251202_1657",
            source_url="https://lehd.ces.census.gov/data/lodes/LODES8/ks/od/",
            coverage_note="Complete scoped OD extract not yet adopted; legacy main-only data are not a complete shed.") for county in scope_counties]))
    observations = normalize_observations(pd.concat(pieces, ignore_index=True))
    # Source payloads use mixed numeric/string flags. Preserve them as text,
    # rather than relying on Arrow's object-column type inference.
    for col in observations.select_dtypes(include="object"):
        if any(isinstance(v, str) for v in observations[col].dropna().head(1000)):
            observations[col] = observations[col].astype("string")
    errors = validate_observations(observations)
    if errors:
        raise ValueError("; ".join(errors))
    regional = build_regional_context(evidence_dir / "kansas-regional-2022-2032.xlsx")
    errors = validate_observations(regional)
    if errors:
        raise ValueError("Regional: " + "; ".join(errors))
    tables = {"pressure_observations.parquet": observations,
              "pressure_training_detail.parquet": detail,
              "pressure_regional.parquet": regional,
              "ipeds_s20.parquet": detail,
              "ipeds_by_sector_s20.parquet": summarize_by_sector(detail)}
    for source, target in (("lodes_flows.parquet", "pressure_lodes_flows.parquet"),
                           ("commute_snapshot.parquet", "commute_snapshot_s20.parquet")):
        if (evidence_dir / source).exists():
            tables[target] = pd.read_parquet(evidence_dir / source)
    # Preserve historical series; mark the old grain explicitly instead of
    # silently deleting prior years when the current CIP6 collection is added.
    # History comes from the LEGACY output files, never the adopted release:
    # read_optional resolves through the active manifest, which re-copied the
    # previous release's history and ignored a regenerated legacy file.
    legacy_detail = outputs / "ipeds_s20.parquet"
    v3_history = legacy_detail.exists() and "cip6" in pq.read_schema(legacy_detail).names
    history_note = ("CIP6 detail rebuilt 2026-11-08 from NCES bulk files (first majors; CIP 99 grand totals and "
                    "hierarchy subtotals excluded); collection 2025 uses the reviewed evidence detail." if v3_history else
                    "Legacy historical aggregation; current detailed reconciliation applies to collection 2025 only.")
    for name in ("ipeds_s20.parquet", "ipeds_by_sector_s20.parquet"):
        path = outputs / name
        old = pd.read_parquet(path) if path.exists() else None
        if old is not None and "year" in old:
            earlier = old[pd.to_numeric(old.year, errors="coerce") < 2025].copy()
            earlier["coverage_note"] = history_note
            tables[name] = pd.concat([earlier, tables[name]], ignore_index=True)

    acs = read_optional(outputs, "acs_combined_s20.parquet")
    proj = read_optional(outputs, "projections_s20.parquet")
    if acs is not None:
        participation = build_participation_table(acs,
            ssa_df=read_optional(outputs, "ssa_disability_s20.parquet"),
            laus_df=read_optional(outputs, "laus_s20.parquet"))
        tables["participation_s20.parquet"] = participation
        if proj is not None:
            tables["projections_effective_s20.parquet"] = project_effective_workforce(participation, proj)
        # Compare against the LEGACY participation file, never the adopted release:
        # read_optional resolves through the active manifest, so after the first
        # adoption it returned the corrected table and every change came out zero.
        legacy_path = outputs / "participation_s20.parquet"
        old = pd.read_parquet(legacy_path) if legacy_path.exists() else None
        if old is not None:
            comparison = old[["county_fips", "year", "effective_labor_force"]].rename(columns={"effective_labor_force":"previous_labor_force"})
            comparison = comparison.merge(participation[["county_fips", "year", "effective_labor_force"]], on=["county_fips", "year"], validate="one_to_one")
            comparison["change"] = comparison.effective_labor_force-comparison.previous_labor_force
            tables["pressure_participation_migration.parquet"] = comparison

    # Recomputed sector outputs are produced by the corrected model in a
    # separate reviewable staging run. Never pull a stale unreviewed override.
    if correction_dir:
        for name in ("sector_projections_s20.parquet", "state_sector_projection_s20.parquet"):
            path = correction_dir / name
            if not path.exists():
                raise ValueError(f"Missing corrected sector output: {name}")
            tables[name] = pd.read_parquet(path)
    else:
        active = read_active(outputs)
        for name in ("sector_projections_s20.parquet", "state_sector_projection_s20.parquet"):
            if name in active.get("files", {}):
                tables[name] = pd.read_parquet(resolve_output(outputs, name, active))

    sources = []
    for source, rows in observations.groupby("source_id"):
        sources.append({"source_id": source, "reference_period": str(rows.period_end.max()),
            "release_id": "; ".join(sorted(rows.release_id.dropna().unique())),
            "adoption_status": "validated observations" if rows.availability_status.eq("published").any() else "unavailable",
            "verification_date": "2026-10-20", "source_url": "; ".join(sorted(rows.source_url.dropna().unique()))})
    sources.extend([
        {"source_id":"apprenticeship", "adoption_status":"disabled: aggregate export/geography unresolved", "verification_date":"2026-10-20"},
        {"source_id":"pseo", "adoption_status":"disabled: Kansas not in verified coverage", "verification_date":"2026-10-20"},
        {"source_id":"bgi", "adoption_status":"disabled: applicable proprietary reuse terms unverified", "verification_date":"2026-10-20"}])
    inputs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in evidence_dir.iterdir() if p.is_file()}
    return stage_release(outputs, tables, sources=sources, release_id=release_id, scope={
        "state":"20", "pilot_counties":scope_counties,
        "coverage":("County demand, commuting and training evidence for all 105 Kansas counties; inspect per-measure availability."
                    if len(scope_counties) > len(PILOT) else
                    "County demand and training pilot; ACS all Kansas counties. Inspect per-measure availability."),
        # Record which legacy population forecast the effective table was built on,
        # rather than asserting it is unchanged (the KSDE youth update changed it 2026-11-08).
        "input_hashes":inputs,
        "population_forecast": ("projections_s20.parquet sha256 " + hashlib.sha256((outputs / "projections_s20.parquet").read_bytes()).hexdigest()
                                if (outputs / "projections_s20.parquet").exists() else "not available"),
        "geography_crosswalk_version":"2026-10-20",
        "regional_context":("KDOL projection regions 2022-2032 (all seven); OEWS May2025: Wichita sample, Kansas City MO-KS full inventory"
                            if len(scope_counties) > len(PILOT) else "South Central 2022-2032; Wichita OEWS May2025 sample only"),
        "sector_correction":"reviewed rebuild" if correction_dir else "retained from adopted release" if "sector_projections_s20.parquet" in tables else "not included; legacy sector forecast withheld in pressure comparisons",
        "composite_score":"not calculated"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--outputs", type=Path, default=OUTPUTS)
    parser.add_argument("--correction-dir", type=Path)
    parser.add_argument("--adopt", action="store_true")
    parser.add_argument("--statewide", action="store_true", help="Cover all 105 Kansas counties instead of the pilot")
    parser.add_argument("--rollback")
    args = parser.parse_args()
    if args.rollback:
        manifest = rollback_release(args.outputs, args.rollback)
        print(f"Rolled back to validated release {manifest['release_id']}")
        return
    if args.evidence_dir is None:
        parser.error("--evidence-dir is required for a build")
    manifest = build(args.evidence_dir, args.outputs, correction_dir=args.correction_dir,
                     counties=statewide_counties() if args.statewide else None)
    print(f"Staged {manifest['release_id']}: {len(manifest['files'])} validated tables")
    if args.adopt:
        adopt_release(args.outputs, manifest)
        print("Adopted. Population outputs and original files were preserved.")


if __name__ == "__main__":
    main()
