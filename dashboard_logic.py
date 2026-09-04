"""Pure, unit-testable logic used by the Streamlit dashboard."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


TREND_GROWING = "Growing (>2%)"
TREND_STABLE = "Stable (−2% to +2%)"
TREND_DECLINING = "Declining (<−2%)"
TREND_OPTIONS = (TREND_GROWING, TREND_STABLE, TREND_DECLINING)


def trend_mask(changes: pd.Series, selected: Sequence[str]) -> pd.Series:
    """Return mutually exclusive trend selections for percentage changes.

    Stable includes both boundary values. Growing and declining begin outside
    that band, so a county cannot be assigned to two categories at once.
    """
    numeric = pd.to_numeric(changes, errors="coerce")
    mask = pd.Series(False, index=changes.index, dtype=bool)

    if TREND_GROWING in selected:
        mask |= numeric > 2
    if TREND_STABLE in selected:
        mask |= numeric.between(-2, 2, inclusive="both")
    if TREND_DECLINING in selected:
        mask |= numeric < -2

    return mask.fillna(False)
