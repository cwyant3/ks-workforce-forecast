# KS Workforce Forecast Dashboard

A Streamlit + Plotly application that forecasts **workforce supply and demand**
for any U.S. state, county by county, and frames it for workforce-development
decision-making. It projects the working-age population forward a decade, steps
that population down to an *effective labor force*, projects industry employment
demand against it, and surrounds both with training-pipeline, commute, and
labor-market context drawn from ~13 public datasets.

Built by Chris Wyant (WSU Tech). Default state is **Kansas (FIPS 20)**; the model
runs for any state from the sidebar.

---

## Table of contents

1. [The core idea — the workforce funnel](#1-the-core-idea--the-workforce-funnel)
2. [Quick start](#2-quick-start)
3. [Repository layout](#3-repository-layout)
4. [How the numbers are calculated](#4-how-the-numbers-are-calculated)
5. [Data sources](#5-data-sources)
6. [The dashboard, tab by tab](#6-the-dashboard-tab-by-tab)
7. [How to read the charts and numbers](#7-how-to-read-the-charts-and-numbers)
8. [Operating the pipeline](#8-operating-the-pipeline)
9. [Deploying to Streamlit Cloud](#9-deploying-to-streamlit-cloud)
10. [Limitations and honest caveats](#10-limitations-and-honest-caveats)

---

## 1. The core idea — the workforce funnel

The dashboard is organized as a five-stage **funnel**, and the tabs follow it:

| Stage | Question | Tab |
|------:|----------|-----|
| **01 Population** | How many working-age people will the county have? | Population |
| **02 Available Workforce** | How many are *actually* available to work? | Available Workforce |
| **03 Demand Pressure** | How hard are employers competing for them? | Demand Pressure |
| **04 Sector Exposure** | Which industries drive that demand? | Sector Exposure |
| **05 Local Action** | What can training/education do about it? | Local Action |

Everything downstream is anchored to a **cohort-component population
projection**. Each later stage narrows or contextualizes that number: subtract
who can't or won't work (participation), compare it to projected jobs (sector
exposure), and connect it to training output and commuting patterns (local
action).

---

## 2. Quick start

### Prerequisites

- **Python 3.10+**
- A free **Census API key** — required to *generate* a new forecast (not needed
  to just view already-generated data). Get one at
  <https://api.census.gov/data/key_signup.html>.
- *(Optional)* a free **BLS API key** — raises rate limits for LAUS/JOLTS pulls.

### Install

```bash
cd ks_workforce_forecast
pip install -r requirements.txt
```

### Configure keys

Create a `.env` file in `ks_workforce_forecast/` (it is gitignored, never
committed):

```
CENSUS_API_KEY=your_census_key_here
BLS_API_KEY=your_bls_key_here      # optional
```

On Streamlit Cloud, put the same keys in the app's **Secrets** instead.

### Run the dashboard

```bash
streamlit run dashboard/app.py
```

Then open <http://localhost:8501>. If a state already has committed outputs
(Kansas and neighbors ship pre-generated), it renders immediately. If not, the
app shows a **"Generate Forecast for {state}"** button.

### Generate a forecast for a state from the CLI

```bash
# Kansas, population + sector layers (the default):
python run_forecast.py --state 20

# Any state, every data layer:
python run_forecast.py --state 08 --all
```

Outputs land in `data/outputs/` as `*_s{FIPS}.parquet` / `.csv`. See
[§8](#8-operating-the-pipeline) for all flags.

---

## 3. Repository layout

```
ks_workforce_forecast/
├── run_forecast.py          # CLI orchestrator (STEP 1..17)
├── refresh_dashboard.py     # monthly live-data refresh driver
├── cohort_model.py          # population projection (cohort-component + Monte Carlo)
├── participation_model.py   # working-age pop → effective labor force
├── sector_model.py          # per-county × sector employment projection (OLS)
├── fetch_*.py               # one data collector per source (ACS, QCEW, LAUS, …)
├── scripts/                 # manual-file parsers + output validator
├── tests/                   # calculation unit tests
├── dashboard/
│   ├── app.py               # the Streamlit UI (all 9 tabs)
│   ├── requirements.txt     # Streamlit Cloud deps
│   └── wsu-tech-logo.png
├── data/
│   ├── *_cache/             # raw pulls, cached per source (gitignored)
│   ├── geo/                 # bundled county-boundary GeoJSON (for offline maps)
│   └── outputs/             # model results the dashboard reads (committed)
└── docs/
    └── dashboard-build-process.md
```

Design principles that recur throughout:

- **Cache-on-exists.** Every fetcher writes a cache and returns it on the next
  run without re-hitting the source, so rebuilds are cheap. Pulling *fresh* data
  requires clearing caches first — that is what `refresh_dashboard.py` does.
- **Per-state, additive outputs.** Each state's results are independent
  `*_s{FIPS}.parquet` files; adding a state never touches another.
- **Committed model outputs.** `data/outputs/` is tracked so the app loads
  instantly on any clone or cloud deploy without regenerating.
- **Dynamic baseline year.** The QCEW anchor year is read from the data, not
  hardcoded, so extending the year range does not silently rot labels.

---

## 4. How the numbers are calculated

### 4.1 Population — cohort-component projection (`cohort_model.py`)

The population forecast is a **cohort-component model** run for every county.
Each simulated year advances every age cohort through five demographic
components:

1. **Survival** — age-specific annual mortality (CDC 2021 national life tables).
2. **Aging** — a fraction of each cohort advances into the next cohort.
3. **Entries** — the 15–17 cohort ages into 18–24 (the youth pipeline entering
   working age).
4. **Retirements** — the 60–64 cohort ages out of the workforce into 65+.
5. **Migration** — net annual migration, estimated per county.

**Migration estimation** uses an age-structured cohort-survival *residual*: age
the earlier ACS age structure forward without migration, compare to the later
observed working-age population, and annualize the difference. Overlapping ACS
5-year vintages are down-weighted to avoid double-counting.

**Uncertainty (the prediction bands)** comes from **Monte Carlo simulation** —
by default **2,000 runs** per county, each drawing a migration rate. Migration
is not an independent coin flip each year: it is an **AR(1) autocorrelated
process (φ = 0.3)**, reflecting that migration conditions persist year to year.
The spread of the 2,000 runs becomes the percentile bands.

**Output** is one row per (county, year) for the forecast window (default
**2026–2035**), with columns:

- `p5, p10, p25, p50, p75, p90, p95` — percentiles of simulated working-age
  population. **`p50` is the median (headline) projection**; `p10`–`p90` is the
  **80% prediction interval**; `p5`–`p95` is the 90% interval; `p25`–`p75` is the
  interquartile (50%) band.
- `retirements_p50`, `entries_p50` — median annual outflow (aging into 65+) and
  inflow (youth aging into 18–24).
- `mig_mean_pct`, `mig_std_pct` — estimated migration rate mean/std.
- `workforce_base`, `pop_total_base`, `base_year`, `pct_change_p50`, plus county
  identifiers.

Runs are reproducible: a fixed seed (`20260424`) is spawned per county.

### 4.2 Available workforce — effective labor force (`participation_model.py`)

Not everyone of working age is in the labor force. The participation model steps
the raw headcount down through **three layers**:

- **Layer 1 — Working-age population (18–64):** the ACS headcount (the cohort
  model's universe).
- **Layer 2 — minus SSA disability:** subtract federal SSDI + SSI disability
  determinations (ages 18–64) → `disability_adjusted_pop`.
- **Layer 3 — × labor-force participation rate:** multiply by the ACS civilian
  labor-force participation rate (LFPR, from ACS table B23001) →
  `effective_labor_force`.

$$\text{effective labor force} = (\text{working-age pop} - \text{disabled}_{18\text{–}64}) \times \frac{\text{LFPR}\%}{100}$$

Layers 2 and 3 are optional — if a source is missing the model falls through
(`layers_used` records which fired, e.g. `ACS+SSA+ACS_LFPR` vs `ACS_only`). The
gap between working-age population and effective labor force is **structural**,
not a vacancy count. The same per-county adjustment factor can scale the whole
population projection into an effective-labor-force projection.

### 4.3 Sector employment demand (`sector_model.py`)

Industry demand is projected per **county × sector** from BLS QCEW history,
using one of two methods chosen automatically:

- **Option B — independent county trend** (used when baseline-year employment
  ≥ 500 **and** ≥ 3 historical observations): a **log-linear OLS** trend fit on
  the county's own history, projected forward with an **80% prediction interval**.
  Log-linear (rather than straight-line) because employment grows
  multiplicatively and should never project negative.
- **Option A — state-share model** (fallback for small, sparse, or suppressed
  county-sectors): take the state-level sector projection and multiply by the
  county's historical share of state employment (or its demographic share if no
  employment history exists).

Wages are projected with linear OLS at county level, falling back to state.

The five tracked sectors are a curated subset relevant to WSU Tech's programs:

| Sector | NAICS |
|--------|-------|
| Healthcare | 62 |
| Manufacturing | 31–33 |
| Hospitality, Entertainment & Food Service | 71, 72 |
| Information & Professional Services | 51, 54 |
| Utilities, Construction & Repair Services | 22, 23, 81 |

### 4.4 Total (all-industries) employment

Separately from the five focus sectors, the pipeline projects **true
all-industries employment** (QCEW total, `naics=10`, all ownership) with the same
log-linear OLS + 80% PI method (`project_total_employment`). This is the honest
denominator for labor-market pressure — the Sector Exposure overlay plots it as
the solid line, with the five focus sectors as a dashed reference (for Kansas the
focus sectors are only ~50% of total employment).

### 4.5 Prediction intervals — what the bands mean

| Band | Meaning |
|------|---------|
| **50% (IQR, p25–p75)** | Half of simulations land in here — the "likely" core. |
| **80% (p10–p90)** | The default planning interval on most charts. |
| **90% (p5–p95)** | Wider tail coverage for risk-aware reads. |

Wider bands = more uncertainty (sparse history, volatile migration, small
county). A widening band into later years is expected and honest.

---

## 5. Data sources

All data is public. "Cache-on-exists" means the fetcher reuses its cache until
cleared; "Manual" means there is no working public API and a file must be placed
by hand (see [§8.4](#84-monthly-refresh-and-manual-sources)).

| # | Source | Agency / dataset | Grain | Role | Access |
|--:|--------|------------------|-------|------|--------|
| 1 | **ACS 5-year** | Census B01001 (age) + B23001 (LFPR) | County, yrs 2015–2024 | Population cohorts + participation | API (key) |
| 2 | **QCEW** | BLS Quarterly Census of Employment & Wages | County/state, 2015–2024 | Sector & total employment/wages | Bulk ZIP |
| 3 | **LAUS** | BLS Local Area Unemployment Statistics | County, annual | Labor force / unemployment | API |
| 4 | **JOLTS** | BLS Job Openings & Labor Turnover | **National only** | Vacancy-rate proxy by sector | API |
| 5 | **IPEDS** | NCES completions + institution chars | Institution→county, 2015–2023 | Training pipeline (supply) | Bulk CSV |
| 6 | **CBP** | Census County Business Patterns | County×NAICS, 2015–2023 | Establishment/firm-formation trend | API (key) |
| 7 | **LODES** | Census LEHD LODES8 origin-destination | County-to-county, 2015–2021 | Commute flows | Bulk CSV |
| 8 | **OES/OEWS** | BLS Occupational Employment & Wages | State + national industry | Occupation wage benchmarks | Bulk ZIP |
| 9 | **SSA** | SSA OASDI (SSDI+SSI), 18–64 | County, data year 2024 | Disability decrement (participation) | **Manual** |
| 10 | **KDOL labor force** | Kansas Dept. of Labor / KLIC (LMIS) | County, monthly | Current KS labor-market pulse | **Manual** (KS only) |
| 11 | **KSDE / CCD** | K-12 enrollment via Urban Institute API | District→county | Youth-cohort override | API (KS only) |
| 12 | **BLS Projections** | BLS national + KS employment projections | Sector; national 2025–35, KS 2024–34 | Demand outlook (display only) | **Manual** |
| 13 | **Projections Central** | DOL/ETA state occupation projections | State | Multi-state demand outlook | API |

> **Note:** JOLTS is **national only** — there is no sub-state JOLTS series, so
> the national supersector vacancy rate is shown as a proxy. BLS Projections are a
> **display layer** and do not feed the cohort model.

---

## 6. The dashboard, tab by tab

The sidebar has a **state selector**, a **Mode** toggle, and (in Full Explorer
mode) a county selector, prediction-band checkboxes, and a minimum-county-
population map filter.

- **Executive Narrative mode** — a curated presentation view with sensible
  defaults; suppresses unvalidated demand claims.
- **Full Explorer mode** — exposes every control and drill-down.

### Executive Narrative
A one-screen story: five headline KPIs (working-age population now and projected,
net change, counties declining, annual net flow), a five-card funnel summary, the
population fan chart, a county spotlight table, and a broad-sector net-jobs bar
chart. Includes **download buttons** for narrative and methodology notes.

### Population
The demographic foundation. KPIs (baseline vs projected working-age population,
counties growing/declining, annual retirements), the **population fan chart**
(median + prediction bands + ACS baseline diamond), a **county choropleth map**
of projected % change (red = decline, green = growth), and Top-10 growing /
declining county tables. *(The map renders offline from bundled county
boundaries.)*

### Available Workforce
Converts population into *available* workforce. A statewide **waterfall chart**:
working-age population → less SSA disability → less not-in-labor-force → effective
labor force, with the structural gap called out. Then a **county drill-down** with
participation KPIs, a county population forecast chart, and a year-by-year
projection table.

### Demand Pressure
Two blocks. **Outlook:** BLS JOLTS national vacancy rates by sector, BLS
employment-projection tables, and a **KS (KDOL) vs. BLS national** sector-outlook
comparison, plus Kansas in-demand occupations. **KS Labor Market Pulse**
(Kansas only): the most current labor-force / unemployment / LFPR figures with a
24-month trend chart and a county snapshot.

### Sector Exposure
The industry demand engine. State KPIs, a **jobs-today-vs-projected** grouped bar
chart by sector, the **Working-Age Population vs. Total Employment overlay**
(solid = all industries, dashed = five focus sectors), per-sector detail cards, a
county sector drill-down with an **Option A/B** method badge and a single-sector
deep-dive, employment/wage tables, and a CBP establishment-trend panel. Has its
own **"Generate Industry Forecast"** button if sector data isn't built yet.

### Local Action
What education/training can do. **Training Pipeline (IPEDS):** completions by
sector over time and by county — a *supply-side* proxy. **Commute Flows (LODES):**
a scatter of local-worker share vs. jobs, and a county commute-detail table with
top feeder counties.

### Explorer
A dense, single-county drilldown: population KPIs, sector exposure, local-action
signals, and the full annual projection — tables and KPI cards, no charts.

### Data
The full county summary table for the selected state, with trend filters, sort
options, and a **CSV download** of the entire dataset.

### Methodology
In-app documentation: data sources, components modeled, prediction-interval
definitions, the NAICS sector mapping, the Option A/B explanation, the
effective-labor-force layers, limitations, and how to add another state.

---

## 7. How to read the charts and numbers

- **The median (p50) is the projection; the band is the honesty.** Quote the p50
  as the number, but never without the interval — a county with a wide 80% band
  is genuinely uncertain (small population or volatile migration), and that is
  information, not noise.
- **"% change" on the map is median vs. baseline.** Green counties are projected
  to grow their working-age population by the base year → end year; red counties
  shrink. Rural counties often show the steepest declines.
- **Available ≠ population.** The Available Workforce waterfall shows the
  *structural* shortfall: people removed by disability determination or not
  participating in the labor force. This is a standing condition, **not** a count
  of open jobs.
- **Sector "net change" is not a labor gap.** The Sector Exposure bars show
  projected *employment change*, i.e., how many jobs the trend implies — not the
  number of unfilled vacancies. Diverging population-vs-employment lines *suggest*
  pressure; they do not by themselves measure a gap.
- **Total vs. focus-sector lines.** On the population-vs-employment overlay, the
  solid line (all industries) is the real demand denominator. The dashed line
  (five WSU Tech sectors) is a reference subset — and it is *private-only*
  (`own_code 5`), while the total includes government, so it is an approximate,
  not exact, subset.
- **Option A vs. Option B badge.** In the county sector deep-dive, **Option B**
  means the county had enough of its own history for an independent trend;
  **Option A** means the projection was borrowed from the state and scaled by the
  county's share (used for small/suppressed county-sectors). Treat Option A
  numbers as lower-confidence.
- **JOLTS is national.** The vacancy-rate chart is a national supersector proxy —
  read it as *directional* sector pressure, not a local rate.
- **IPEDS completions are a supply proxy.** They count credentials awarded, not
  job placements or whether graduates stay in-state.
- **Display-only layers.** BLS/KDOL projections and JOLTS are context; they do
  **not** change the cohort population model.

---

## 8. Operating the pipeline

### 8.1 Generate or regenerate a state

```bash
python run_forecast.py --state 20            # population + sector (defaults)
python run_forecast.py --state 20 --all      # every data layer
python run_forecast.py --state 08 --no-sectors   # population only
```

Key flags:

| Flag | Effect |
|------|--------|
| `--state 20` | State FIPS (default 20 = Kansas) |
| `--key ...` | Census API key (overrides `.env`) |
| `--sims 2000` | Monte Carlo runs per county |
| `--start 2026` / `--end 2035` | Forecast window |
| `--seed 20260424` | Reproducibility seed |
| `--no-sectors` | Skip the QCEW sector layer |
| `--all` | Turn on every optional dataset |
| `--laus --ipeds --lodes --oes --cbp --jolts --kdol --ksde --ssa --bls-proj --pc-proj` | Individual dataset layers |

### 8.2 What each step writes

The orchestrator runs numbered steps; each writes to `data/outputs/`:

- STEP 1 ACS → `acs_combined_s{fips}.parquet`
- STEP 2 cohort model → `projections_s{fips}.parquet`
- STEP 3 summary → `county_summary_s{fips}.csv`
- STEP 4 state aggregate → `state_projection_s{fips}.parquet`
- STEP 5–6 sectors → `sector_projections_s{fips}.parquet`,
  `state_sector_projection_s{fips}.parquet`, `state_total_projection_s{fips}.parquet`
- STEP 7 LAUS → `laus_s{fips}.parquet`
- STEP 8 IPEDS → `ipeds_by_sector_s{fips}.parquet`
- STEP 9 LODES → `commute_snapshot_s{fips}.parquet`
- STEP 10 OES → `oes_state_s{fips}.parquet`
- STEP 11 CBP → `cbp_estab_trends_s{fips}.parquet`
- STEP 12 JOLTS → `jolts_vacancy_rates.parquet`
- STEP 13 KDOL → `kdol_sector_pulse.parquet`
- STEP 15 SSA + participation → `participation_s{fips}.parquet`
- STEP 16 BLS projections → `bls_proj_sector_outlook.parquet`
- STEP 16b Projections Central → `pc_occ_by_sector_s{fips}.parquet`

### 8.3 Add another state

Pick the state's 2-digit FIPS and run `python run_forecast.py --state {FIPS}
--all`, then commit the new `data/outputs/*_s{FIPS}.parquet` files. The dashboard
picks it up from the sidebar automatically. Kansas-only layers (KDOL, KSDE) are
skipped for other states.

### 8.4 Monthly refresh and manual sources

Because fetchers are cache-on-exists, a plain rerun reproduces the same numbers.
To pull **fresh** data, use the refresh driver:

```bash
python refresh_dashboard.py --state 20
```

It clears the API-backed caches (LAUS, JOLTS, QCEW, IPEDS, LODES, OES, CBP, PC),
re-runs `run_forecast.py --all`, validates outputs, and flags stale **manual**
sources. Manual sources have **no working public API** and must be downloaded by
hand into the right cache folder, then parsed by the scripts in `scripts/`:

| Source | Place the file at | From |
|--------|-------------------|------|
| KDOL labor force | `data/kdol_cache/labforce__99999999.xls` or `.xlsx` | KLIC report builder (`klic.dol.ks.gov`) |
| SSA disability | `data/ssa_cache/oasdi_sc{YY}.xlsx` (e.g. `oasdi_sc25.xlsx`) | SSA Policy Statistics (OASDI-SC) |
| BLS national projections | `data/bls_proj_cache/bls_proj_national_{base}_{proj}.xlsx` (e.g. `bls_proj_national_2025_2035.xlsx`) | BLS Employment Projections |
| KS projections (3 workbooks) | `data/kdol_proj/*.xlsx` (published filenames) | KDOL LMIS |

**Name these files for their vintage, not generically.** Both the SSA and BLS
inputs are picked by a glob that takes the newest match, and the year in the
filename is what the parser reads the vintage from — `oasdi_sc25.xlsx` is parsed
as the 2025 edition (data year 2024), and `bls_proj_national_2025_2035.xlsx` as
the 2025–35 cycle. Dropping a new edition in under an old name, or under the
legacy vintage-less `bls_proj_national_manual.xlsx`, means the newest data gets
labelled with the wrong cycle or ignored entirely.

Caches **preserved** by refresh (never auto-cleared): `acs_cache`, `kdol_cache`,
`ssa_cache`, `bls_proj_cache`, `ksde_cache`.

### 8.5 Per-source refresh and the scheduled routines

A full refresh re-downloads everything, which is wasteful when only one agency
published. Two flags narrow it:

```bash
python refresh_dashboard.py --sources laus --states bloc   # one source, all 5 deployed states
python refresh_dashboard.py --sources none --states 20     # manual sources: re-parse + rerun KS
python refresh_dashboard.py --list-sources                 # print the source -> cache map
```

- `--sources` clears **only** the named source's cache, so every other layer is
  served from cache and the resulting git diff isolates the source that moved.
  The pipeline still runs `--all`; the deterministic layers recompute to
  identical outputs because the seed and their caches are unchanged.
- `--states bloc` covers KS, CO, MO, NE, OK — every state whose outputs are
  tracked in git and therefore served by the app. Refreshing Kansas alone leaves
  the neighbour states a vintage behind on the same series, which the
  dashboard's cross-state comparisons cannot reveal.

Each source has a scheduled routine that fires the day after that source
publishes, checks whether new data actually appeared, and refreshes only if so.
The calendar, the cron for each routine, and the release dates they were derived
from live in [docs/data-source-release-calendar.md](docs/data-source-release-calendar.md);
what each run did is appended to
[docs/data-refresh-log.md](docs/data-refresh-log.md).

**The routines never commit or push.** Streamlit Cloud builds from git, so
refreshed data reaches the public dashboard only after a human reviews the diff
and pushes it.

---

## 9. Deploying to Streamlit Cloud

1. Push this repo to GitHub.
2. At <https://share.streamlit.io> → **New app** → point to `dashboard/app.py`.
3. Add `CENSUS_API_KEY` (and optionally `BLS_API_KEY`) in the app's **Secrets**.

Because `data/outputs/` and the county-boundary GeoJSON are committed, the app
renders committed states immediately without regenerating — and the county map
works even on networks that block GitHub raw, since boundaries are embedded
server-side rather than fetched by the browser.

---

## 10. Limitations and honest caveats

- **Projections, not predictions.** Outputs are trend-and-simulation estimates
  for planning, not forecasts of certainty. Always read the prediction band with
  the median.
- **ACS is survey data.** Small counties have wide margins of error and volatile
  migration estimates; those counties get wider bands and often fall back to the
  state-share sector model (Option A).
- **Demand pressure ≠ vacancies.** Sector employment change and the
  population/employment divergence indicate *pressure*, not a measured labor gap
  or job-opening count.
- **JOLTS is national; OES industry is national.** Sub-state values for these are
  proxies.
- **Supply proxies.** IPEDS completions measure credentials awarded, not
  placements or retention.
- **Manual sources lag.** SSA, KDOL labor force, and BLS/KS projection files are
  refreshed by hand and may be several weeks or months old; the refresh tool
  flags them after 100 days.

---

*For the development history and how this dashboard is built and iterated, see
[`docs/dashboard-build-process.md`](docs/dashboard-build-process.md).*
