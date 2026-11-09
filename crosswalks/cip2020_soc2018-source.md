# Official program-to-occupation relationships

Retrieved 2026-10-21 from the [NCES CIP 2020–SOC 2018 crosswalk](https://nces.ed.gov/ipeds/cipcode/Files/CIP2020_SOC2018_Crosswalk.xlsx). The original workbook is preserved as `CIP2020_SOC2018_Crosswalk.xlsx`. Original SHA256: `ba3d59a191b9d977a5c457a66b9348c4f2f7963aafacf72c0b80113b46bf0ab8`.

The shared preprocess script profiled all eight workbook sheets before reading. Normalization then scanned the entire primary `CIP-SOC` sheet using pandas/openpyxl, with string types preserving leading-zero CIP codes. The source sheet has 6,097 records, including 374 `NO MATCH` placeholder records. Excluding placeholders produces 5,723 unique program/occupation pairs across 1,948 CIPs and 687 SOCs. The source sheet has no duplicate code pairs. The normalized CSV is `cip2020_soc2018.csv`; its hash and row counts are recorded in `cip2020_soc2018-provenance.json`.

The reverse `SOC-CIP` sheet has 6,093 records. Full pair comparison identifies four primary-only matches: CIP 03.0204, Environmental/Natural Resource Economics, to SOC 11-9199, 19-3011, 19-4061 and 25-1063. There are no reverse-only matches. The reverse and new/added/unmatched worksheets are not concatenated with the primary sheet. The primary CIP-to-SOC direction supplies the dashboard's relationship set. `dashboard.training_pathways.normalize_crosswalk` records the normalization rules and can regenerate the CSV from this sheet.

These are many-to-many relationships, not allocation weights or evidence of placement. The dashboard selects related CIPs for one exact detailed SOC using membership filtering. It does not join each award onto every occupation, infer required credentials, subtract awards from annual openings or calculate a labor shortage. It preserves institution, CIP, award level, collection year and source status. Duplicate identical detail records are removed; conflicting reporting identities are refused. Latest county collection is selected before mapping so an absent current program does not silently reuse older awards.

Regional employment outlook remains in its published South Central geography and 2022–2032 horizon. Exact-SOC OEWS pay is shown only from an adopted area containing the selected county. Missing exact occupation pay does not fall back to broader occupational or industry wages. County awards describe institution reporting location, not graduate residence, available workers or program capacity.

Validation: seven focused tests passed. A Streamlit AppTest using the adopted release rendered Sedgwick welding and electrician selections, Cowley, Harper with no related reported awards, and an unsupported county without runtime exceptions. Long chart labels wrap while CIP codes retain distinct category positions and full titles remain in hover. Detailed institution rows are in an expander.

## Items Requiring Verification

- [VERIFY] The reason the four identified CIP 03.0204 matches are absent from the reverse worksheet is not explained by the workbook. The dashboard explicitly uses the primary `CIP-SOC` worksheet and preserves its matches; no invented match is introduced.
- Crosswalk relationships alone do not establish current licensing requirements, employer acceptance, enrollment capacity, completion-to-placement rates or local retention. Those remain outside this panel.
- Nonmetropolitan and additional exact-occupation pay estimates remain unavailable until independently adopted.
