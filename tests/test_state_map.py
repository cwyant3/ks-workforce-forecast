import pandas as pd
import pytest

from dashboard import app


@pytest.mark.parametrize("state", ["20", "08", "31", "29", "40"])
def test_state_map_fits_custom_boundaries_without_changing_county_values(monkeypatch, state):
    counties = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": state + code, "properties": {},
         "geometry": {"type": "Polygon", "coordinates": [[
             [-101, 37], [-100, 37], [-100, 38], [-101, 38], [-101, 37]]]}}
        for code in ["001", "003"]
    ]}
    monkeypatch.setattr(app, "load_counties_geojson", lambda requested: counties)
    summary = pd.DataFrame({"county_fips": ["001", "003"],
                            "county_name": ["First County", "Second County"],
                            "pct_change_end": [-12.5, 3.0]})
    before = summary.copy(deep=True)
    figure = app.state_choropleth(summary, state, 2035)
    trace = figure.data[0]
    assert list(trace.locations) == [state + "001", state + "003"]
    assert list(trace.z) == [-12.5, 3.0]
    assert trace.geojson == counties
    # Albers USA never auto-fits; Mercator must frame the actual county locations.
    assert figure.layout.geo.projection.type == "mercator"
    assert figure.layout.geo.scope == "world"
    assert figure.layout.geo.fitbounds is False
    assert list(figure.layout.geo.lonaxis.range) == pytest.approx([-101.05, -99.95])
    assert list(figure.layout.geo.lataxis.range) == pytest.approx([36.95, 38.05])
    pd.testing.assert_frame_equal(summary, before)


def test_missing_boundaries_still_raise_instead_of_rendering_an_empty_map(monkeypatch):
    monkeypatch.setattr(app, "load_counties_geojson", lambda requested: None)
    summary = pd.DataFrame({"county_fips": ["001"], "county_name": ["First County"],
                            "pct_change_end": [0.0]})
    with pytest.raises(RuntimeError, match="boundary GeoJSON unavailable"):
        app.state_choropleth(summary, "20", 2035)
