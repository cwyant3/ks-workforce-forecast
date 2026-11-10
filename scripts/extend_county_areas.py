"""Extend crosswalks/county_areas_20261020.csv from pilot counties to all 105 Kansas counties.

Projection regions come from the KDOL regional workbook's county map. OEWS pay
areas outside Wichita are not adopted, so they stay blank rather than guessed.
Run once; it only appends counties that are missing.
"""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "crosswalks/county_areas_20261020.csv"
WORKBOOK = ROOT / "data/pressure_sources/2026-10-20/kansas-regional-2022-2032.xlsx"
REGIONS = {"Kansas City": "KS-KC", "North Central": "KS-NC", "Northeast": "KS-NE", "Northwest": "KS-NW",
           "South Central": "KS-SC", "Southeast": "KS-SE", "Southwest": "KS-SW"}

current = pd.read_csv(CSV, dtype=str).fillna("")
cmap = pd.read_excel(WORKBOOK, sheet_name="Map - Projection Regions", dtype=str)
cmap = cmap[cmap.State.eq("KS")].copy()
cmap["county_name"] = cmap.County.str.replace(r"\s+County$", "", regex=True).str.strip()
proj = pd.read_parquet(ROOT / "data/outputs/projections_s20.parquet", columns=["county_fips", "county_name"]).drop_duplicates()
proj["county_fips5"] = "20" + proj.county_fips.astype(str).str.zfill(3)
proj["key"] = proj.county_name.str.replace(r"\s+County$", "", regex=True).str.strip().str.lower()
cmap["key"] = cmap.county_name.str.lower()
merged = proj.merge(cmap[["key", "Projection Region"]], on="key", how="outer", indicator=True)
assert (merged._merge == "both").all() and len(merged) == 105, merged[merged._merge != "both"]
url = current.projection_source_url.iloc[0]
new = pd.DataFrame({"county_fips5": merged.county_fips5,
                    "county_name": merged.county_name.str.replace(r"\s+County$", "", regex=True),
                    "projection_region": merged["Projection Region"],
                    "oews_area": "", "version": "2026-11-10",
                    "source_url": "https://www.bls.gov/oes/2025/may/ks_counties.htm",
                    "projection_region_id": merged["Projection Region"].map(REGIONS),
                    "projection_source_url": url,
                    "oews_area_name": "OEWS area pay not adopted in this release"})
assert new.projection_region_id.notna().all()
out = pd.concat([current, new[~new.county_fips5.isin(current.county_fips5)]], ignore_index=True)
out = out.sort_values("county_fips5")
assert len(out) == 105 and out.county_fips5.is_unique
out.to_csv(CSV, index=False)
print(out.groupby("projection_region_id").size())
