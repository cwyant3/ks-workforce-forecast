"""Unit tests for dashboard-only transformation and filter logic."""

from __future__ import annotations

import pandas as pd

from dashboard_logic import (
    TREND_DECLINING,
    TREND_GROWING,
    TREND_OPTIONS,
    TREND_STABLE,
    trend_mask,
)


CHANGES = pd.Series([-3.0, -2.0, -0.1, 0.0, 0.1, 2.0, 3.0, None])


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
