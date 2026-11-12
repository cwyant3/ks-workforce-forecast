"""Responsive metric rows without changing ordinary chart/selector columns."""
import streamlit as st


METRIC_GRID_CSS = """
<style>
/* The keyed container scopes reflow to metric rows, including with a sidebar. */
[class*="st-key-metric-grid-"] [data-testid="stHorizontalBlock"] {
    display: grid !important;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr));
    align-items: start;
}
[class*="st-key-metric-grid-"] [data-testid="stColumn"] {
    width: 100% !important;
    min-width: 0 !important;
}
[class*="st-key-metric-grid-"] h2,
[class*="st-key-metric-grid-"] .metric-card .value {
    white-space: nowrap;
}
</style>
"""


def metric_columns(count, key):
    """Keep Streamlit card contents native; let available width choose the rows."""
    with st.container(key=f"metric-grid-{key}"):
        return st.columns(count)
