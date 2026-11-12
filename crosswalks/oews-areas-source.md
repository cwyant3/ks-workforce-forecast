# Kansas OEWS pay-area mappings

Verified 2026-11-12 against the [BLS May 2025 area definitions](https://www.bls.gov/oes/current/msa_def.htm) and the [BLS OEWS area-code registry](https://download.bls.gov/pub/time.series/oe/oe.area). Kansas has seven metropolitan pay areas and one nonmetropolitan pay area in this vintage. Cross-state area values describe the entire published area.

| API area | Name | Kansas county members |
|---|---|---|
| 48620 | Wichita, KS | Butler, Harvey, Sedgwick, Sumner |
| 28140 | Kansas City, MO-KS | Johnson, Leavenworth, Linn, Miami, Wyandotte |
| 45820 | Topeka, KS | Jackson, Jefferson, Osage, Shawnee, Wabaunsee |
| 29940 | Lawrence, KS | Douglas |
| 31740 | Manhattan, KS | Geary, Pottawatomie, Riley |
| 41140 | St. Joseph, MO-KS | Doniphan |
| 27900 | Joplin, MO-KS | Cherokee |
| 2000006 | Kansas nonmetropolitan area | The remaining 85 counties, enumerated in the versioned county crosswalk |

The registry pads metro codes to seven digits (for example, 0045820). Nonmetro code 2000006 is already seven digits. Both are substate type `M` in the OE API; type `N` means national and is rejected. Do not substitute state series `S2000000` for the nonmetro area.

The six newly added area codes were independently probed through the BLS API for May 2025 all-occupation employment before mapping counties. The saved public response and the exact reviewed county names/codes are in the local evidence bundle `data/pressure_sources/2026-11-12-oews-kansas/` as `area-definition-api-probe.json` and `oews-area-definitions-2026-11-12.json`. No registration key is saved with responses.

`scripts/stage_oews_area.py` requests employment, employment RSE, mean annual wage, mean-wage RSE and median annual wage for the 776 detailed/total occupation candidates in the existing Kansas/Missouri state cache. Missing and suppressed area observations remain unavailable. This is the same candidate-universe method used for Kansas City. It does not add major-group totals or infer missing estimates. Each completed batch is retained locally for resumable retrieval; the merged response and occupation titles enter the source manifest.

Each county maps once. Area observations are selected once at native area grain and never allocated to or summed over member counties. KDOL projection-region assignments remain separate and unchanged. Wichita retains its existing four-SOC sample; this update adds the six previously missing areas.

## Items Requiring Verification

- None for the May 2025 area identities and Kansas county membership verified above. Future vintages require a fresh definition/registry review before remapping.
