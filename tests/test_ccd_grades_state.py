import fetch_ksde


def test_directory_uses_requested_state_and_drops_other_state_counties(monkeypatch):
    calls = []

    def fake_paginate(url, params):
        calls.append(params)
        return [{"leaid": "2900001", "county_code": "29095"}, {"leaid": "2900002", "county_code": "20091"}]

    monkeypatch.setattr(fetch_ksde, "_paginate", fake_paginate)
    mapping = fetch_ksde._fetch_leaid_county_map(2024, "29")
    assert calls[0]["fips"] == 29
    assert mapping == {"2900001": "095"}


def test_grades_carry_requested_state(monkeypatch):
    monkeypatch.setattr(fetch_ksde, "_fetch_grade", lambda year, grade, state_fips: [{"leaid": "2900001", "enrollment": 10}])
    monkeypatch.setattr(fetch_ksde.time, "sleep", lambda s: None)
    out = fetch_ksde._fetch_year_grades(2024, {"2900001": "095"}, "29")
    assert set(out.state_fips) == {"29"} and out.enrollment.sum() == 130
