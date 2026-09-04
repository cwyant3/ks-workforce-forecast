"""Unit tests for dashboard-only transformation and filter logic."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from dashboard_logic import (
    TREND_DECLINING,
    TREND_GROWING,
    TREND_OPTIONS,
    TREND_STABLE,
    trend_mask,
)


CHANGES = pd.Series([-3.0, -2.0, -0.1, 0.0, 0.1, 2.0, 3.0, None])
DASHBOARD_SOURCE = (
    Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
).read_text(encoding="utf-8")


def _selected_values(selection: list[str]) -> list[float]:
    return CHANGES[trend_mask(CHANGES, selection)].dropna().tolist()


def test_trend_categories_are_mutually_exclusive_at_boundaries() -> None:
    assert _selected_values([TREND_DECLINING]) == [-3.0]
    assert _selected_values([TREND_STABLE]) == [-2.0, -0.1, 0.0, 0.1, 2.0]
    assert _selected_values([TREND_GROWING]) == [3.0]


def test_selecting_all_trends_includes_every_numeric_county_once() -> None:
    mask = trend_mask(CHANGES, TREND_OPTIONS)

    assert mask.sum() == CHANGES.notna().sum()
    assert CHANGES[mask].tolist() == CHANGES.dropna().tolist()


def test_empty_trend_selection_returns_no_rows() -> None:
    assert not trend_mask(CHANGES, []).any()


def test_dashboard_uses_tested_trend_logic() -> None:
    assert "list(TREND_OPTIONS)" in DASHBOARD_SOURCE
    assert 'trend_mask(disp["% Change"], trend_filter)' in DASHBOARD_SOURCE
    assert "Growing (>0%)" not in DASHBOARD_SOURCE
    assert "Declining (<0%)" not in DASHBOARD_SOURCE


def test_dashboard_labels_kdol_and_ssa_scenarios_accurately() -> None:
    assert "KDOL UI claims" not in DASHBOARD_SOURCE
    assert "Kansas county UI claims by industry" not in DASHBOARD_SOURCE
    assert "KDOL LMIS labor-force statistics" in DASHBOARD_SOURCE
    assert "This is not an individual employability measure" in DASHBOARD_SOURCE
    assert "Do not infer individual work capacity" in DASHBOARD_SOURCE
    assert "Modeled Available Labor Force" in DASHBOARD_SOURCE
