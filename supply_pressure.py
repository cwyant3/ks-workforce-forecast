"""Resident supply evidence, separate from demographic and sector forecasts."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from fetch_acs import parse_acs_supply
from fetch_laus import parse_laus_monthly
from data_contracts import normalize_observations


def build_supply_indicators(evidence_dir: Path) -> pd.DataFrame:
    """Build observations from saved public ACS/LAUS evidence, without fetching.

    Every saved ACS observation keeps its published five-year reference window.
    LAUS probes may overlap; conflicting duplicates fail instead of silently
    choosing a vintage. Missing geographic coverage is not zero-filled.
    """
    evidence_dir = Path(evidence_dir)
    frames = []
    for path in sorted(evidence_dir.glob("acs-*.json")):
        match = re.fullmatch(r"acs-(\d{4})-(s2301|b23025)-ks\.json", path.name)
        if not match:
            continue
        frame = parse_acs_supply(json.loads(path.read_text(encoding="utf-8-sig")),
                                 int(match[1]), match[2].upper())
        frame["source_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
        frame["source_evidence_file"] = path.name
        frames.append(frame)
    laus_frames = []
    for path in sorted(evidence_dir.glob("laus-*.json")):
        frame = parse_laus_monthly(json.loads(path.read_text(encoding="utf-8-sig")), "20")
        if not frame.empty:
            frame["source_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
            frame["source_evidence_file"] = path.name
            laus_frames.append(frame)
    if laus_frames:
        laus = pd.concat(laus_frames, ignore_index=True)
        keys = ["source_id", "measure_id", "geography_id", "period_start", "period_end"]
        for _, group in laus.groupby(keys, dropna=False):
            if group["source_value"].nunique(dropna=False) > 1:
                raise ValueError("Conflicting saved LAUS probes; choose a verified release explicitly")
        frames.append(laus.drop_duplicates(keys, keep="last"))
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined["parser_version"] = "supply-v1"
    # Exact 16+ subject participation, detailed civilian counts and LAUS remain
    # separate sources/universes rather than one synthetic available-worker sum.
    result = pd.concat([normalize_observations(frame, source_id=source)
                      for source, frame in combined.groupby("source_id", sort=False)],
                     ignore_index=True)
    result["moe_confidence_level"] = pd.to_numeric(result["moe_confidence_level"], errors="coerce")
    return result


def participation_migration(old: pd.DataFrame, corrected: pd.DataFrame) -> pd.DataFrame:
    """County/year before-after table for an explicit methodology migration."""
    keys = ["state_fips", "county_fips", "year"]
    before = old[keys + ["effective_labor_force"]].rename(
        columns={"effective_labor_force": "previous_effective_labor_force"})
    after = corrected[keys + ["effective_labor_force", "participation_method", "ssa_role"]]
    result = before.merge(after, on=keys, how="outer", validate="one_to_one")
    result["difference_people"] = result["effective_labor_force"] - result["previous_effective_labor_force"]
    result["difference_pct"] = result["difference_people"] / result["previous_effective_labor_force"].replace(0, pd.NA) * 100
    return result
