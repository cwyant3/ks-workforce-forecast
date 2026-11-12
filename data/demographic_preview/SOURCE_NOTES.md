# Census July 2025 population preview

This bundle supports the opt-in **Preview: Census 2025–2043**. It does not replace the adopted ACS 2024 baseline or the Kansas City cross-state comparison.

- Original: [CC-EST2025-SYASEX Kansas county CSV](https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/counties/asrh/cc-est2025-syasex-20.csv).
- [Official layout](https://www2.census.gov/programs-surveys/popest/technical-documentation/file-layouts/2020-2025/CC-EST2025-SYASEX.pdf).
- Acquired November 12, 2026. Source SHA-256: `905c29eee250704e6663bdc3b1460490ae96fae1e30c8a82c75071b69b9da354`.
- Source was processed/profiled through the vault's existing preprocessing tool before use. The adapter validates every data row: 105 counties × seven source year codes × 86 age rows = 63,210. No sampling. Keys must be unique, counts nonnegative and male + female = total. Counties must match the existing 105-county ACS inventory.
- Select YEAR=7, July 1, 2025, January 1, 2025 boundaries. Source ages 0–84 remain intact; the published 85+ sex totals are split by unchanged national life-table survivor weights at runtime. These are population estimates, not direct census counts. Terminal model age is 119+.
- Retain 2020–2024 ACS effective resident participation shares, SSA 2023 survival, pooled 2020–2024 fertility calibration and the original 2025 birth-level factor. Population/participation periods differ. The original ACS exposure remains the fertility calibration denominator. Future 2026–2043 births use the new female cohorts and national fertility schedules; no 2025 birth count is inserted again.
- Low/high fertility paths are analyst sensitivities, not probability bounds. No 2026 nowcast bridge or prospective accuracy claim is provided.

From the dashboard repository:

```powershell
& .venv/Scripts/python.exe scripts/build_pep_population_inputs.py --source PATH_TO_REVIEWED_CSV --output-dir data/demographic_preview
& .venv/Scripts/python.exe scripts/verify_demographic_preview.py --output-dir PATH_TO_EVIDENCE_FOLDER
```

The builder writes only its specified bundle. It does not adopt a release or rebuild other forecasts. Runtime checks both original and normalized hashes and refuses a mismatched bundle. A missing/invalid preview is reported explicitly; it is never silently substituted with an ACS population under the 2025 label.
