"""Headless integration tests for the Streamlit dashboard.

These tests exercise the committed dashboard and output files as a user would:
load the application, verify the default selection, and switch through every
state whose forecast outputs are committed in this repository.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


COMMITTED_STATES = ("Colorado", "Kansas", "Missouri", "Nebraska", "Oklahoma")
DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"


def _state_selectbox(app: AppTest):
    matches = [widget for widget in app.selectbox if widget.label == "Select state"]
    assert len(matches) == 1, (
        "Expected exactly one sidebar state selector; "
        f"found labels {[widget.label for widget in app.selectbox]}"
    )
    return matches[0]


def _assert_no_app_exception(app: AppTest) -> None:
    errors = [str(exc.value) for exc in app.exception]
    assert not errors, "Dashboard raised exception(s):\n" + "\n".join(errors)


def _load_app() -> AppTest:
    return AppTest.from_file(DASHBOARD_PATH, default_timeout=180).run()


def test_dashboard_loads_default_kansas_view() -> None:
    app = _load_app()

    _assert_no_app_exception(app)
    state = _state_selectbox(app)
    assert state.value == "Kansas"
    assert len(state.options) == 51  # 50 states plus the District of Columbia


def test_dashboard_renders_every_committed_state() -> None:
    app = _load_app()
    _assert_no_app_exception(app)

    for state_name in COMMITTED_STATES:
        _state_selectbox(app).select(state_name)
        app.run(timeout=180)
        _assert_no_app_exception(app)
        assert _state_selectbox(app).value == state_name
