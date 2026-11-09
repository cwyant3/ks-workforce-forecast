"""Working-age share of in-movers, from ACS B07001 (mobility in the past year by age).

The cohort engine (demographic_pipeline.project_cohorts) adds or removes
residents ages 18-64. Published net-migration totals such as Census PEP count
migrants of all ages. Feeding an all-age total straight into the engine treats
every child and retiree as a working-age adult and overstates the labor-force
effect. This module converts an all-age total to its 18-64 part with the age
mix of people who moved into the county in the past year.

Limit: B07001 describes in-movers only. It does not observe out-movers or net
migration by age, so the same share is applied to positive and negative net
totals. The 5-year ACS pools 2020-2024 movers; small counties carry wide MOEs.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

INPUT_DIR = Path(__file__).resolve().parent / "data" / "demographic_inputs"
WORKING_AGE = (18, 64)
ORIGINS = {
    "same_county": "Moved within same county",
    "other_ks_county": "Moved from different county within same state",
    "other_state": "Moved from different state",
    "abroad": "Moved from abroad",
}
# Net migration for a county counts anyone crossing its border, so the default
# in-mover pool is every mover from outside the county.
DEFAULT_ORIGINS = ("other_ks_county", "other_state", "abroad")


def _age_band(label: str) -> tuple[int, int]:
    numbers = [int(n) for n in re.findall(r"\d+", label)]
    if "and over" in label and len(numbers) == 1:
        return numbers[0], 119
    if len(numbers) == 2:
        return numbers[0], numbers[1]
    raise ValueError(f"Unrecognized B07001 age band: {label}")


def _variables(labels: dict, origins) -> dict[str, tuple[int, int]]:
    """Map estimate variables to age bands for the requested mover origins."""
    wanted = {ORIGINS[o] for o in origins}
    found, seen = {}, set()
    for variable, meta in labels.items():
        if not re.fullmatch(r"B07001_\d{3}E", variable):
            continue
        parts = [p.rstrip(":") for p in meta["label"].split("!!")]
        if len(parts) == 4 and parts[2] in wanted:
            found[variable] = _age_band(parts[3])
            seen.add(parts[2])
    if seen != wanted:
        raise ValueError(f"B07001 labels lack mover origins: {sorted(wanted - seen)}")
    return found


def in_mover_age_mix(record: dict, labels: dict, origins=DEFAULT_ORIGINS) -> dict:
    """Return in-mover totals, the 18-64 count, its share and an approximate 90% MOE.

    The MOE follows the Census proportion formula with root-sum-of-squares MOEs
    for the summed cells; it is a guide to precision, not an exact interval.
    """
    total = working = total_moe2 = working_moe2 = 0.0
    for variable, (low, high) in _variables(labels, origins).items():
        estimate = float(record[variable])
        if estimate < 0:
            raise ValueError("Suppressed B07001 cell; working-age share withheld.")
        moe = record.get(variable[:-1] + "M")
        moe = float(moe) if moe not in (None, "") and float(moe) >= 0 else 0.0
        total += estimate
        total_moe2 += moe ** 2
        if low >= WORKING_AGE[0] and high <= WORKING_AGE[1]:
            working += estimate
            working_moe2 += moe ** 2
    if total <= 0:
        raise ValueError("No in-movers recorded; working-age share unavailable.")
    share = working / total
    radicand = working_moe2 - share ** 2 * total_moe2
    if radicand < 0:  # Census guidance: fall back to the ratio formula.
        radicand = working_moe2 + share ** 2 * total_moe2
    return {"in_movers": total, "in_movers_18_64": working, "share_18_64": share,
            "share_moe90": math.sqrt(radicand) / total}


def _rows(name: str) -> list[dict]:
    payload = json.loads((INPUT_DIR / name).read_text(encoding="utf-8"))
    return [dict(zip(payload[0], row)) for row in payload[1:]]


def load_labels() -> dict:
    return json.loads((INPUT_DIR / "acs2024_5yr_B07001_labels.json").read_text(encoding="utf-8"))["variables"]


def county_mixes(origins=DEFAULT_ORIGINS) -> dict[str, dict]:
    """In-mover age mix for each Kansas county, keyed by five-digit FIPS."""
    labels = load_labels()
    return {row["state"] + row["county"]: in_mover_age_mix(row, labels, origins)
            for row in _rows("acs2024_5yr_B07001_response.json")}


MIN_IN_MOVERS = 1000
MAX_SHARE_MOE = 0.10


def county_shares(origins=DEFAULT_ORIGINS, min_in_movers=MIN_IN_MOVERS, max_moe=MAX_SHARE_MOE) -> dict[str, dict]:
    """Working-age share to apply per county, with its basis.

    A county uses its own share when its sample is large enough (at least
    ``min_in_movers`` estimated in-movers and a 90% MOE no wider than
    ``max_moe``). Otherwise it uses the pooled Kansas share (all counties'
    in-movers summed); tiny counties' own shares range from about 16% to 100%.
    """
    mixes = county_mixes(origins)
    pooled = sum(m["in_movers_18_64"] for m in mixes.values()) / sum(m["in_movers"] for m in mixes.values())
    result = {}
    for fips, mix in mixes.items():
        own = mix["in_movers"] >= min_in_movers and mix["share_moe90"] <= max_moe
        result[fips] = {**mix, "share_applied": mix["share_18_64"] if own else pooled,
                        "basis": "county" if own else "kansas_pooled"}
    return result


def metro_mix(origins=DEFAULT_ORIGINS) -> dict:
    """In-mover age mix for the Wichita, KS Metro Area (CBSA 48620)."""
    rows = _rows("acs2024_5yr_B07001_msa48620_response.json")
    if len(rows) != 1:
        raise ValueError("Expected one Wichita metro B07001 row.")
    return in_mover_age_mix(rows[0], load_labels(), origins)


def working_age_net_migrants(all_age_net_migrants: float, share_18_64: float) -> float:
    """Convert an all-age net migrant total to its modeled 18-64 part."""
    if not math.isfinite(all_age_net_migrants) or not math.isfinite(share_18_64):
        raise ValueError("Migration inputs must be finite.")
    if not 0 < share_18_64 <= 1:
        raise ValueError("The working-age share must be in (0, 1].")
    return all_age_net_migrants * share_18_64
