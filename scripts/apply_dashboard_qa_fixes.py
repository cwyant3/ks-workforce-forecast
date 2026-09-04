"""Apply the reviewed dashboard QA fixes to dashboard/app.py.

This temporary branch-maintenance script exists because the connected GitHub
contents API replaces whole files and app.py is intentionally large. It is
removed after the branch commit is produced.
"""

from __future__ import annotations

from pathlib import Path


PATH = Path("dashboard/app.py")


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = PATH.read_text(encoding="utf-8")

    if "from dashboard_logic import TREND_OPTIONS, trend_mask" in text:
        print("Dashboard QA fixes already applied.")
        return

    text = _replace_once(
        text,
        "ROOT = Path(__file__).parent.parent\nsys.path.insert(0, str(ROOT))\n\nOUTPUT_DIR = ROOT / \"data\" / \"outputs\"",
        "ROOT = Path(__file__).parent.parent\nsys.path.insert(0, str(ROOT))\n\nfrom dashboard_logic import TREND_OPTIONS, trend_mask\n\nOUTPUT_DIR = ROOT / \"data\" / \"outputs\"",
        "dashboard logic import",
    )

    text = _replace_once(
        text,
        '''            trend_filter = st.multiselect(
                "Trend filter",
                ["Growing (>0%)", "Declining (<0%)", "Stable (±2%)"],
                default=["Growing (>0%)", "Declining (<0%)", "Stable (±2%)"],
            )''',
        '''            trend_filter = st.multiselect(
                "Trend filter",
                list(TREND_OPTIONS),
                default=list(TREND_OPTIONS),
            )''',
        "trend selector",
    )

    text = _replace_once(
        text,
        '''        mask = pd.Series([False] * len(disp), index=disp.index)
        if "Growing (>0%)" in trend_filter:
            mask |= disp["% Change"] > 0
        if "Declining (<0%)" in trend_filter:
            mask |= disp["% Change"] < 0
        if "Stable (±2%)" in trend_filter:
            mask |= disp["% Change"].abs() <= 2
        disp = disp[mask]''',
        '''        disp = disp[trend_mask(disp["% Change"], trend_filter)]''',
        "trend filtering",
    )

    replacements = {
        'demand_sources.append("KDOL UI claims")':
            'demand_sources.append("KDOL LMIS labor-force statistics")',
        '"KDOL UI claims",\n            "loaded" if kdol_df is not None and not kdol_df.empty else "not loaded",\n            "Kansas-only current labor market pulse",':
            '"KDOL LMIS labor-force statistics",\n            "loaded" if kdol_df is not None and not kdol_df.empty else "not loaded",\n            "Kansas-only current labor-market conditions",',
        '- KDOL UI claims are Kansas-only and should be framed as a pulse, not a forecast.':
            '- KDOL LMIS labor-force statistics are Kansas-only current-condition measures, not a forecast.',
        '| 7 | KDOL UI | Demand Pressure | `--kdol` | Kansas county UI claims by industry (KS-only) |':
            '| 7 | KDOL LMIS | Demand Pressure | `--kdol` | Kansas monthly labor force, employment, unemployment, and LFPR statistics (KS-only) |',
        '- KDOL UI claims are available only for Kansas; no stable public download API exists':
            '- KDOL LMIS labor-force statistics are Kansas-only current-condition measures, not future vacancies',
        'population down to the effective labor force, exposing the two\n    structural decrements (SSA disability and ACS non-participation).':
            'population down to a modeled labor-force scenario, exposing the SSA\n    beneficiary-count adjustment and the ACS participation adjustment.',
        '"Less: disability<br>(SSA)",':
            '"SSA beneficiary<br>scenario adjustment",',
        '"Less: not in<br>labor force (ACS)",':
            '"ACS participation<br>adjustment",',
        '"Effective<br>Labor Force"],':
            '"Modeled Available<br>Labor Force"],',
        'text=f"{state_name} — Effective Labor Force vs. Working-Age Population ({year})",':
            'text=f"{state_name} — Modeled Available Labor Force Scenario ({year})",',
        'st.markdown(f"### Statewide Available Workforce — {selected_state}")':
            'st.markdown(f"### Statewide Available Workforce Planning Scenario — {selected_state}")',
        '"After Disability Adj. (SSA)",':
            '"After SSA Scenario Adj.",',
        '"Effective Labor Force",\n                _fmt(elf_stats["elf"]),':
            '"Modeled Available Labor Force",\n                _fmt(elf_stats["elf"]),',
        '"Structural Gap",\n                _fmt(elf_stats["gap"]),':
            '"Modeled Availability Gap",\n                _fmt(elf_stats["gap"]),',
        '''                "Effective labor force = working-age population, less people with federal "
                "disability determinations (SSA, where county data is available), times the "
                "ACS civilian labor-force participation rate. The gap is structural — it is "
                "who is not available to work today, before any forecast or county detail."''':
            '''                "Planning scenario = working-age population, less aggregate county SSA "
                "beneficiary counts where available, times the ACS civilian labor-force participation "
                "rate. This is not an individual employability measure: receiving SSDI or SSI does "
                "not establish that a person cannot or does not work. Review possible overlap between "
                "the SSA and ACS adjustments before treating this scenario as a definitive labor-supply count."''',
        '"The statewide effective-labor-force view needs the participation model "':
            '"The statewide modeled-availability view needs the participation model "',
        'st.markdown("**Effective Labor Force (Participation Model)**")':
            'st.markdown("**Modeled Available Labor Force (Planning Scenario)**")',
        '"Disability Rate (SSA)",':
            '"SSA Beneficiary Rate",',
        "f'{gap_pct:.0f}% structural gap '":
            "f'{gap_pct:.0f}% modeled availability gap '",
        '"Effective Labor Force",\n                    _fmt(eff_lf)':
            '"Modeled Available Labor Force",\n                    _fmt(eff_lf)',
        '### Effective Labor Force (Participation Model)':
            '### Modeled Available Labor Force (Planning Scenario)',
        '''2. **Minus SSA disability** (SSDI + SSI, 18–64) — removes individuals with federal
   disability determinations (`disability_adjusted_pop`)
3. **× ACS B23001 civilian labor force participation rate** — accounts for structural non-participation
   (`effective_labor_force`)''':
            '''2. **SSA beneficiary-count scenario adjustment** (SSDI + SSI, 18–64) — subtracts
   aggregate county beneficiary counts for planning sensitivity (`disability_adjusted_pop`)
3. **× ACS B23001 civilian labor force participation rate** — applies observed aggregate participation
   (`effective_labor_force`)''',
        '| 9 | SSA Disability | Available Workforce | `--ssa` | SSDI + SSI beneficiary counts; adjusts effective workforce |':
            '| 9 | SSA Disability | Available Workforce | `--ssa` | Aggregate SSDI + SSI beneficiary counts used as a planning-scenario adjustment |',
        '- Participation model uses a static adjustment factor from the most recent data year;':
            '- Participation scenario may overlap SSA beneficiary nonparticipation with the ACS LFPR adjustment; validate the estimand before using it as a definitive available-worker count\n- Participation model uses a static adjustment factor from the most recent data year;',
        '- Do not compare residence-based population directly to worksite employment without commute context.':
            '- Do not compare residence-based population directly to worksite employment without commute context.\n- Do not infer individual work capacity or employability from aggregate SSA benefit status.',
    }

    missing: list[str] = []
    for old, new in replacements.items():
        if old not in text:
            missing.append(old[:100])
        else:
            text = text.replace(old, new)

    if missing:
        raise SystemExit(f"Missing reviewed dashboard patterns: {missing!r}")

    PATH.write_text(text, encoding="utf-8")
    print("Applied dashboard QA fixes.")


if __name__ == "__main__":
    main()
