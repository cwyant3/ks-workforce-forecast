"""IPEDS bulk files live on two NCES paths; downloads must fall back on a 404 only."""
import pytest
import requests

import fetch_ipeds as fi


def _http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} error", response=response)


def test_candidate_urls_try_primary_then_legacy_path():
    urls = fi.candidate_urls(fi.HD_URL.format(year=2016))
    assert urls == ["https://nces.ed.gov/ipeds/complete-data-files/HD2016.zip",
                    "https://nces.ed.gov/ipeds/datacenter/data/HD2016.zip"]


def test_download_falls_back_to_legacy_path_on_404(monkeypatch):
    calls = []

    def fake_get(url, label):
        calls.append(url)
        if "complete-data-files" in url:
            raise _http_error(404)
        return b"zip-bytes"

    monkeypatch.setattr(fi, "_http_get_zip", fake_get)
    assert fi._http_get_zip_any(fi.candidate_urls(fi.COMPLETIONS_URL.format(year=2015)), "C2015_A") == b"zip-bytes"
    assert [u.split("/ipeds/")[1] for u in calls] == ["complete-data-files/C2015_A.zip", "datacenter/data/C2015_A.zip"]


def test_download_does_not_mask_server_errors(monkeypatch):
    def fake_get(url, label):
        raise _http_error(503)

    monkeypatch.setattr(fi, "_http_get_zip", fake_get)
    with pytest.raises(requests.HTTPError, match="503"):
        fi._http_get_zip_any(fi.candidate_urls(fi.HD_URL.format(year=2019)), "HD2019")


def test_download_reports_404_when_no_path_has_the_file(monkeypatch):
    monkeypatch.setattr(fi, "_http_get_zip", lambda url, label: (_ for _ in ()).throw(_http_error(404)))
    with pytest.raises(requests.HTTPError, match="404"):
        fi._http_get_zip_any(fi.candidate_urls(fi.FLAGS_URL.format(year=2030)), "FLAGS2030")
