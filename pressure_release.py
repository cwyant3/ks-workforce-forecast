"""Immutable pressure releases, verified manifests, atomic adoption and rollback.

Legacy outputs remain unchanged. A release may override a validated subset of
legacy names; readers resolve all files against one captured manifest.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager

import pandas as pd
import numpy as np

from data_contracts import validate_observations

POINTER = "pressure-current.json"


def validate_tables(tables: dict[str, pd.DataFrame]) -> list[str]:
    """Check adopted overrides as well as the normalized observation layer."""
    errors = []
    for name, table in tables.items():
        if table.empty:
            errors.append(f"{name}: empty table")
            continue
        if name in ("pressure_observations.parquet", "pressure_regional.parquet"):
            errors.extend(f"{name}: {e}" for e in validate_observations(table))
        required = set()
        grain = []
        if name in ("sector_projections_s20.parquet", "state_sector_projection_s20.parquet"):
            required = {"sector", "year", "emp_proj", "emp_ci_lo", "emp_ci_hi", "emp_base", "base_year", "method", "note"}
            grain = ["sector", "year"]
            if name.startswith("sector_"):
                required |= {"county_fips", "state_fips", "county_name"}
                grain.insert(0, "county_fips")
        elif name == "participation_s20.parquet":
            required = {"county_fips", "year", "effective_labor_force", "participation_method", "ssa_role"}
            grain = ["county_fips", "year"]
        elif name == "projections_effective_s20.parquet":
            required = {"county_fips", "year", *[f"eff_p{x}" for x in (5, 10, 25, 50, 75, 90, 95)]}
            grain = ["county_fips", "year"]
        elif name == "ipeds_by_sector_s20.parquet":
            required = {"state_fips", "county_fips", "year", "sector", "completions"}
            grain = ["state_fips", "county_fips", "year", "sector"]
        elif name == "commute_snapshot_s20.parquet":
            required = {"county_fips", "year", "inbound_coverage_complete", "total_jobs_at_worksite", "job_type"}
            grain = ["county_fips", "year", "job_type"]
        missing = required-set(table.columns)
        if missing:
            errors.append(f"{name}: missing required fields {sorted(missing)}")
            continue
        if grain and table.duplicated(grain).any():
            errors.append(f"{name}: duplicate grain")
        if name.endswith("_s20.parquet") and "county_fips" in table and not table.county_fips.astype(str).str.fullmatch(r"\d{3}").all():
            errors.append(f"{name}: invalid three-digit legacy county FIPS")
        if name.endswith("_s20.parquet") and "state_fips" in table and not table.state_fips.astype(str).eq("20").all():
            errors.append(f"{name}: non-Kansas override")
        if "emp_proj" in required:
            values = table[["emp_ci_lo", "emp_proj", "emp_ci_hi"]].apply(pd.to_numeric, errors="coerce")
            # An unavailable whole projection stays null; partial intervals are invalid.
            valid = values.notna().any(axis=1)
            selected = values.loc[valid]
            if not np.isfinite(selected.to_numpy()).all() or (selected < 0).any().any() or (selected.emp_ci_lo > selected.emp_proj).any() or (selected.emp_proj > selected.emp_ci_hi).any():
                errors.append(f"{name}: invalid employment interval")
            if name.startswith("sector_") and table.county_fips.nunique() != 105:
                errors.append(f"{name}: expected all 105 Kansas counties")
    if "pressure_observations.parquet" not in tables:
        errors.append("Pressure observations are required")
    return errors


@contextmanager
def _adoption_lock(outputs: Path):
    outputs.mkdir(parents=True, exist_ok=True)
    lock = outputs / ".pressure-adoption.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("Another pressure adoption holds the lock") from exc
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_active(outputs: Path) -> dict:
    pointer = outputs / POINTER
    if not pointer.exists():
        return {}
    data = json.loads(pointer.read_text(encoding="utf-8"))
    if not re.fullmatch(r"[A-Za-z0-9_-]+", data.get("release_id", "")):
        raise ValueError("Invalid adopted release identifier")
    return data


def release_directory(outputs: Path, manifest: dict) -> Path:
    return outputs.parent / "pressure_releases" / manifest["release_id"]


def resolve_output(outputs: Path, name: str, manifest: dict | None = None) -> Path:
    if Path(name).name != name:
        raise ValueError("Output name must be a basename")
    current = read_active(outputs) if manifest is None else manifest
    if name in current.get("files", {}):
        return release_directory(outputs, current) / name
    return outputs / name


def output_cache_token(outputs: Path, manifest: dict | None = None) -> str:
    current = read_active(outputs) if manifest is None else manifest
    # Also invalidate legacy readers after an existing non-pressure refresh.
    stats = [(p.name, p.stat().st_size, p.stat().st_mtime_ns)
             for p in sorted(outputs.glob("*")) if p.is_file()]
    return hashlib.sha256(json.dumps([current, stats], sort_keys=True).encode()).hexdigest()


def stage_release(outputs: Path, tables: dict[str, pd.DataFrame], *,
                  sources: list[dict], scope: dict, release_id: str | None = None) -> dict:
    observations = tables.get("pressure_observations.parquet")
    if observations is None:
        raise ValueError("Pressure observations are required")
    errors = validate_tables(tables)
    if errors:
        raise ValueError("; ".join(errors))
    release_id = release_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", release_id):
        raise ValueError("Invalid release identifier")
    directory = outputs.parent / "pressure_releases" / release_id
    directory.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, table in tables.items():
        if Path(name).name != name or not name.endswith(".parquet"):
            raise ValueError("Only named parquet tables can be staged")
        path = directory / name
        table.to_parquet(path, index=False)
        files[name] = {"sha256": sha256(path), "rows": len(table), "columns": list(table.columns)}
    source_coverage = []
    for (source, measure), rows in observations.groupby(["source_id", "measure_id"]):
        valid = rows[rows.availability_status.eq("published")]
        source_coverage.append({"source_id": source, "measure_id": measure,
                                "latest_reference": str(rows.period_end.max()),
                                "latest_usable": str(valid.period_end.max()) if len(valid) else None,
                                "statuses": rows.availability_status.value_counts().to_dict()})
    manifest = {"schema_version": 1, "release_id": release_id,
                "created_utc": datetime.now(timezone.utc).isoformat(), "scope": scope,
                "files": files, "sources": sources, "coverage": source_coverage,
                "previous_release_id": read_active(outputs).get("release_id")}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    return manifest


def validate_release(outputs: Path, manifest: dict) -> list[str]:
    failures = []
    if not re.fullmatch(r"[A-Za-z0-9_-]+", manifest.get("release_id", "")):
        return ["Invalid release identifier"]
    directory = release_directory(outputs, manifest)
    for name, spec in manifest["files"].items():
        if Path(name).name != name:
            failures.append("Unsafe filename")
            continue
        path = directory / name
        if not path.exists() or sha256(path) != spec["sha256"]:
            failures.append(f"Hash mismatch or missing: {name}")
    if not failures:
        try:
            tables = {name: pd.read_parquet(directory / name) for name in manifest["files"]}
            failures.extend(validate_tables(tables))
        except Exception as exc:
            failures.append(f"Unreadable release: {exc}")
    return failures


def adopt_release(outputs: Path, manifest: dict) -> None:
    failures = validate_release(outputs, manifest)
    if failures:
        raise ValueError("; ".join(failures))
    # Compare-and-swap: do not overwrite a concurrent adoption.
    with _adoption_lock(outputs):
        if read_active(outputs).get("release_id") != manifest.get("previous_release_id"):
            raise RuntimeError("Active release changed; restage against the current manifest")
        _write_pointer(outputs, manifest)


def _write_pointer(outputs: Path, manifest: dict) -> None:
    outputs.mkdir(parents=True, exist_ok=True)
    temp = outputs / (".pressure-" + uuid.uuid4().hex + ".json")
    temp.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temp, outputs / POINTER)


def rollback_release(outputs: Path, release_id: str) -> dict:
    if release_id == "legacy":
        with _adoption_lock(outputs):
            pointer = outputs / POINTER
            if pointer.exists():
                os.replace(pointer, outputs / ("pressure-retired-" + uuid.uuid4().hex + ".json"))
        return {"release_id": "legacy"}
    if not re.fullmatch(r"[A-Za-z0-9_-]+", release_id):
        raise ValueError("Invalid rollback identifier")
    manifest = json.loads((outputs.parent / "pressure_releases" / release_id / "manifest.json").read_text())
    failures = validate_release(outputs, manifest)
    if failures:
        raise ValueError("; ".join(failures))
    with _adoption_lock(outputs):
        _write_pointer(outputs, manifest)
    return manifest
