# KS Workforce Forecast Dashboard — Build & Iteration Writeup

*How the dashboard was built, and how it is refined through AI-assisted,
prompt-driven iteration.*

**Author:** Chris Wyant (WSU Tech), with Claude Code
**Repo:** `github.com/cwyant3/ks-workforce-forecast` (branch `master`)
**Last updated:** 2026-07-20

---

## 1. What the dashboard is

A Streamlit + Plotly application that forecasts **workforce supply and demand**
for any U.S. state, county by county, and frames it for a workforce-development
audience. It combines:

- A **cohort-component population model** (ACS age cohorts → working-age
  population 18–64, projected with Monte Carlo AR(1) simulation bands).
- An **industry-sector employment model** built on BLS QCEW county data
  (log-linear OLS trend per county × sector, with 80% prediction intervals).
- A **participation layer** (ACS labor-force participation + SSA disability)
  that steps working-age population down to an *effective labor force*.
- Roughly **ten integrated public datasets** (ACS, QCEW, LAUS, JOLTS, IPEDS,
  CBP, LODES, OES, SSA, KDOL/KSDE) surfaced across a nine-tab dashboard.

The application is a single Streamlit script (`dashboard/app.py`, ~3,300 lines)
backed by a family of `fetch_*.py` data collectors and two model modules
(`cohort_model.py`, `sector_model.py`), orchestrated by `run_forecast.py`.

---

## 2. Architecture at a glance

```
run_forecast.py            ← CLI orchestrator (--all, --state)
├── fetch_acs.py           ← population cohorts (Census ACS)
├── fetch_qcew.py          ← county industry employment/wages (BLS QCEW)
├── fetch_laus / jolts / ipeds / cbp / lodes / oes / ssa / ...
├── cohort_model.py        ← population projection (Monte Carlo)
├── sector_model.py        ← per-county × sector employment projection (OLS)
└── writes → data/outputs/*.parquet  (one set per state FIPS)

dashboard/app.py           ← Streamlit UI; reads data/outputs/, renders tabs
data/                      ← *_cache/ (raw pulls) + outputs/ (model results)
                             geo/geojson-counties-fips.json (county boundaries)
```

**Key design choices that recur:**

- **Cache-on-exists everywhere.** Every fetcher writes a parquet cache and reads
  it back on subsequent runs, so a full rebuild doesn't re-hit the APIs.
- **Per-state outputs.** All model results are written as
  `{artifact}_s{FIPS}.parquet`, so states are independent and additive.
- **Model outputs are committed.** The tracked `data/outputs/*.parquet` let the
  dashboard load instantly on any clone or Streamlit Cloud deploy without
  regenerating.
- **Dynamic baseline year.** The QCEW anchor year is derived from the data, not
  hardcoded, so extending the year range doesn't silently rot labels.

---

## 3. The development model

This project is developed **conversationally**: the operator describes an intent
or a defect in plain language, and the change is implemented, verified, and
committed in a tight loop. The working pattern that has emerged:

1. **Diagnose before building.** A question ("is this number what I think it
   is?") is answered by tracing the actual code path, not by guessing.
2. **Plan the data flow explicitly** for anything touching the models.
3. **Implement against a short task list**, keeping edits surgical.
4. **Verify with real data** (compile-check, sanity-check numbers, smoke-test the
   figure) before declaring done.
5. **Launch for a human visual check** on anything visual.
6. **Commit with a descriptive message; push only when asked.**

The rest of this document walks through the two most recent iteration arcs at
the prompt level, because they are a faithful record of how the loop actually
runs. (The earlier build history in §6 is reconstructed from commit messages —
the prompt transcripts for those changes are not part of this record.)

---

## 4. Case study A — "Total Sector Employment" should mean *all* sectors

**Problem the operator suspected:** the Sector Exposure tab's *Working-Age
Population vs. Total Sector Employment* chart might be summing only WSU Tech's
focus sectors, not the whole economy — which would understate labor-market
pressure.

### Iteration A1 — Diagnostic question

> **Prompt:** *"…is the total sector Employment summed across all sectors…or
> just the sectors shown on the graph?"*

Rather than answer from memory, the code path was traced end to end (a search
agent located the project and the relevant lines). Finding:

- `sector_model.py` and `fetch_qcew.py` hardcode a five-item `SECTORS` list
  (Healthcare, Manufacturing, Hospitality & Entertainment, IT/Computer Services,
  Skilled Trades).
- The chart's `state_sector_df.groupby("year")["emp_proj"].sum()` therefore
  summed **only those five**. Retail, finance, public administration, wholesale,
  transportation, etc. were excluded from the pipeline entirely.

**Answer: (b)** — it was the focus sectors only. Confirmed with code citations.

### Iteration A2 — Plan, then build

> **Prompt:** *"I would like to fix this graph to [be] representative of all
> sectors… think through a plan on how to pull the appropriate data for all
> sectors by county per state, then provide a graph which will show true
> pressure based on TOTAL Sector Employment vs Working[-]age population."*
> *(followed by:)* *"Keep both lines on the chart."*

**Critical discovery during planning:** the true all-industries total was
*already being fetched* — QCEW row `naics="10"` (total, all ownership) flowed
into a `state_totals` DataFrame that was passed into `run_all_sectors()` **and
then never used**. The honest denominator was sitting there as dead code.

Plan executed against a four-item task list:

1. **`sector_model.py`** — new `project_total_employment()` that forecasts the
   true all-industries total using the *same* log-linear OLS + 80% prediction
   interval method as the per-sector model, so the two lines are comparable.
2. **`run_forecast.py` + `app.py`** — persist a new
   `state_total_projection_s{FIPS}.parquet`; add a loader that returns `None`
   when the file is absent (graceful fallback for un-regenerated states).
3. **Chart** — plot **both** lines: solid = total (all industries), dashed =
   five focus sectors for reference. Honest title/caption/axis, plus a footnote
   that the focus sectors are private-only (`own_code 5`) while the total
   includes government (`own_code 0`), so the focus line is an *approximate*
   subset.
4. **Backfill** the five already-forecast states (CO/KS/MO/NE/OK) from the
   existing QCEW cache — no re-downloads.

**Verification that made the fix worth it:** for Kansas the five focus sectors
are only **~50% of total employment** — the old chart was hiding half the
labor-demand picture.

| Year | Total (all industries) | Focus sectors | Focus share |
|------|-----------------------:|--------------:|------------:|
| 2026 | 1,412,664 | 704,940 | 49.9% |
| 2035 | 1,458,808 | 755,662 | 51.8% |

### Iteration A3 — Visual check

> **Prompt:** *"launch and let me do a visual check"*

A `.claude/launch.json` was added and Streamlit launched on `:8501`. (Minor
snag: the bare `streamlit` command wasn't on PATH in the shell — resolved by
running `python -m streamlit`.)

### Iteration A4 — Legend overlap

> **Prompt:** *"The legend on the new graph is overlapping the graph itself, can
> you move [it] up by the title so the graph is clearly visible."*

Adding the second line had grown the legend to seven entries (two lines, two
shaded PI bands, two baseline dots, population), which wrapped into the plot.
Fix: `showlegend=False` on the bands and baseline-dot markers (still visible on
the chart and in hover), leaving **three clean line entries**, centered as a
single row in an enlarged top margin (`t=80 → 130`).

### Iteration A5–A6 — Commit & push

> **Prompts:** *"commit this"* → *"push to master, then stop the server…"*

Discovered that `ks_workforce_forecast/` is a **separate git repo** (gitignored
from the parent vault) on `master`. Committed the three code files plus the five
backfilled parquets — deliberately excluding an unrelated stray parquet and the
local `.claude/` helper. Pushed to `origin/master`; stopped the server.

**Result:** commit `a32e5ea` — *"feat(dashboard): plot true all-industries
employment vs working-age population."*

---

## 5. Case study B — The county map wouldn't load

### Iteration B1 — Troubleshoot

> **Prompt:** *"Population Tab, Kansas — County Working Age Population Change:
> 2024 to 2035 map is not loading. Please troubleshoot."*

**Root cause found by reading the code, then testing the network:**

- `state_choropleth()` passed the county boundaries to Plotly as a
  **`raw.githubusercontent.com` URL**. That makes the *browser* fetch the file
  at render time.
- A connectivity test confirmed `raw.githubusercontent.com` **times out** from
  this machine, while jsDelivr, census.gov, and Google all respond → the WSU
  Tech network blocks GitHub raw specifically.
- Worse, the call-site `try/except` only guarded *figure construction* (which
  never fails with a URL string), not the browser fetch — so the map went blank
  with **no error message**.

**Fix — make it render offline:**

- Downloaded the identical GeoJSON via **jsDelivr** and cached it at
  `data/geo/geojson-counties-fips.json`.
- New `load_counties_geojson()` reads the local cache first, falls back to
  jsDelivr → GitHub raw, and **filters features to the selected state** (3 MB /
  3,221 counties → a few KB / 105 for KS).
- `state_choropleth()` now **embeds the geometry server-side**
  (`geojson=<dict>`, `featureidkey="id"`) so the browser makes **zero external
  requests**. It raises when geometry is truly unavailable, so the warning
  actually surfaces instead of a silent blank.

**Verification:** all **105/105** KS county FIPS matched the embedded feature
IDs; the figure built with the geometry embedded and no network. Same fix covers
CO/MO/NE/OK.

### Iteration B2–B3 — Commit, push, stop

> **Prompts:** *"commit and push"* → *"stop it"*

Committed `app.py` + the bundled GeoJSON (committing the 3.1 MB file so the map
works out-of-the-box on any clone / Streamlit Cloud deploy). Pushed; stopped the
server.

**Result:** commit `6e4d3a8` — *"fix(dashboard): render county map offline via
embedded GeoJSON."*

---

## 6. The longer arc (reconstructed from commit history)

The two case studies above are recent refinements. The dashboard itself was
built incrementally over ~45 commits. *The following is inferred from commit
messages, not from prompt records:*

- **2026-04-24 — Foundation.** Initial Kansas cohort-component forecast; Census
  API key handling; **any-US-state selector**; first round of chart-readability
  fixes (black legend/axis/colorbar text).
- **2026-04-24/25 — Industry layer.** Added the BLS QCEW sector forecast;
  improved trend fitting (log-linear, lower threshold, dropped the significance
  gate); redesigned the Industry Forecast tab into supply-vs-demand views;
  `requirements.txt` for Streamlit Cloud.
- **2026-04-27 — Scale-out.** A **10-dataset integration + 9-tab dashboard**
  (Phases 1–4); an `--all` CLI flag to run every fetcher in one command; a
  dedicated code-review pass (dynamic base year, CI caveat, FIPS guards).
- **2026-04-28 → 05-21 — Data correctness & depth.** BLS series-ID format fixes
  (JOLTS/LAUS); state-aware funnel copy + local logo; a full data refresh with
  four dataset-gap fixes and five new dashboard layers.
- **2026-06-03 → 06-08 — Modeling refinements.** Re-baselined sectors to the
  latest QCEW year with dynamic labels; corrected ACS B23001 labor-force offsets
  (LFPR ~1% → ~79%); added the statewide **effective-labor-force** anchor.
- **2026-06-29 — Multi-state parity.** SSA/IPEDS fixes; a Projections-Central
  fetcher; **full-parity outputs for four neighbor states (CO/NE/MO/OK)**; a
  guard so regeneration can't clobber full per-state data with a cohort-only run.
- **2026-07-01 & 07-07 — This document's case studies** (commits `a32e5ea`,
  `6e4d3a8`).

A consistent thread runs through the history: **fix the data correctly at the
source, keep charts honest about what they show, and make the thing run for
someone who isn't the author** (offline maps, committed outputs, Cloud-ready).

---

## 7. Patterns worth reusing

- **Trace, don't guess.** Every "is this right?" was answered by reading the
  actual code path and, where relevant, testing the actual network.
- **Look for dead inputs.** The biggest fix (Case A) was enabling data the
  pipeline already fetched but discarded — no new source needed.
- **Verify with numbers, not vibes.** "Focus sectors = ~50% of total" is what
  justified the change; "105/105 counties matched" is what proved the map fix.
- **Fail loudly, degrade gracefully.** Raise so warnings surface; return `None`
  so older states still render the fallback.
- **Make it portable.** Bundle boundaries, commit model outputs, don't depend on
  a specific network being reachable at render time.
- **Keep the human in the visual loop.** Launch and hand off for a look before
  committing anything that changes a chart.
