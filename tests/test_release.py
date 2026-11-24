from pathlib import Path
import json
import shutil

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd
import pytest

from eccles_service.api import create_app
from eccles_service.features import expected_session, build_features, prepare_prices
from eccles_service.model import Bundle, digest
from eccles_service.pipeline import make_payload, refresh, status, availability
from eccles_service import sources
from eccles_service.sources import SourceError, series, trailing_fallback
from eccles_service.storage import Store, active_bundle, activate, load_version, exclusive

ROOT = Path(__file__).resolve().parents[1]
KEY = "test-credential-" + "a" * 40


@pytest.fixture
def setup(tmp_path):
    models = tmp_path / "models"; shutil.copytree(ROOT / "models", models)
    runtime = tmp_path / "runtime"
    activate(models, runtime, "1.0")
    b = active_bundle(models, runtime)
    return models, runtime, b


def sample(b, day="2026-11-05"):
    g = json.loads((b.path / "golden.json").read_text())
    x = pd.DataFrame([g["inputs"][0]], index=pd.to_datetime([day]), columns=b.names)
    return make_payload(b, day, x, x, "snapshot-test", "2026-11-06T07:00:00+09:00", "test", "test_fixture")


def test_exact_distance_forward_and_conservation(setup):
    _, _, b = setup
    x = np.asarray(json.loads((b.path / "golden.json").read_text())["inputs"])
    pred = b.infer(x)
    direct = ((pred["z"][:, None, :] - b.centers) ** 2).sum(2)
    np.testing.assert_array_equal(pred["state"], direct.argmin(1))
    np.testing.assert_allclose(pred["relevance"].sum(1), pred["margin"], atol=1e-12)
    np.testing.assert_allclose(pred["allocations"].sum(1), 1)
    with pytest.raises(ValueError): b.infer(np.full((1, 26), np.nan))


def test_changed_model_rejected(setup):
    models, runtime, _ = setup
    p = models / "1.0/model.json"; p.write_text(p.read_text() + " ")
    with pytest.raises(ValueError, match="checksum"): active_bundle(models, runtime)


@pytest.mark.parametrize("version", ["../1.0", "/tmp/1.0", "1.0/../../", "1.0\n", "0.0", "v1.0"])
def test_invalid_version_rejected(setup, version):
    with pytest.raises(ValueError): load_version(setup[0], version)


def test_api_auth_and_data_contract(setup):
    models, runtime, b = setup
    client = TestClient(create_app(models, runtime, KEY))
    assert client.get("/health/live").status_code == 200
    for path in ["/v1/models/current", "/v1/regime/latest", "/v1/status", "/openapi.json", "/health/ready"]:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    auth = {"Authorization": "Bearer " + KEY}
    assert client.get("/v1/regime/latest", headers=auth).status_code == 404
    store = Store(runtime); payload = sample(b); assert store.publish(payload)
    result = client.get("/v1/regime/latest", headers=auth).json()
    assert result["result"]["as_of"] == "2026-11-05"
    assert result["result"]["model_sha256"] == b.hash
    assert len(result["result"]["neon"]["features"]) == 26
    assert client.get("/v1/regimes/2025-01-02", headers=auth).status_code == 404
    assert client.get("/v1/regimes/invalid", headers=auth).status_code == 422
    assert client.get("/v1/regimes?start=2026-11-06&end=2026-11-01", headers=auth).status_code == 422
    assert client.get("/v1/regimes?start=2026-01-01&end=2026-11-10&limit=10000", headers=auth).status_code == 422
    assert client.get("/openapi.json", headers=auth).status_code == 200


def test_no_empty_auth(setup):
    with pytest.raises(RuntimeError): create_app(setup[0], setup[1], "")


def test_publication_immutable_and_warmup_blocked(setup):
    _, runtime, b = setup
    store = Store(runtime); p = sample(b)
    assert store.publish(p)
    assert not store.publish({**p, "regime": "C0"})
    assert store.get(b.version, p["as_of"]) == p
    with pytest.raises(ValueError, match="warmup"): sample(b, "2025-11-05")


def test_clock_weekend_close_grace_and_dst():
    assert expected_session("2026-11-09T20:59:00Z") == "2026-11-06"
    assert expected_session("2026-11-09T22:29:00Z") == "2026-11-06"
    assert expected_session("2026-11-09T22:30:00Z") == "2026-11-09"
    assert expected_session("2026-11-08T12:00:00Z") == "2026-11-06"
    assert expected_session("2026-07-06T21:30:00Z") == "2026-07-06"
    with pytest.raises(ValueError): expected_session("2026-11-09")


def test_new_model_activation_and_rollback(setup):
    models, runtime, b = setup
    store = Store(runtime); store.publish(sample(b))
    shutil.copytree(models / "1.0", models / "1.1")
    p = models / "1.1/model.json"; spec = json.loads(p.read_text()); spec["model_version"] = "1.1"; p.write_text(json.dumps(spec))
    p = models / "1.1/manifest.json"; m = json.loads(p.read_text()); m["model_version"] = "1.1"; m["files"]["model.json"] = digest(models / "1.1/model.json"); p.write_text(json.dumps(m))
    new = load_version(models, "1.1"); store.register(new)
    with pytest.raises(ValueError, match="Refresh"): activate(models, runtime, "1.1")
    assert active_bundle(models, runtime).version == "1.0"
    store.publish(sample(new)); activate(models, runtime, "1.1")
    assert active_bundle(models, runtime).version == "1.1"
    activate(models, runtime, "1.0")
    assert active_bundle(models, runtime).version == "1.0"
    assert store.latest("1.1")["model_version"] == "1.1"


def test_writer_exclusion(setup):
    with exclusive(setup[1]):
        with pytest.raises(RuntimeError):
            with exclusive(setup[1]): pass


def test_source_failure_preserves_latest_and_marks_status(setup):
    models, runtime, b = setup; store = Store(runtime); store.publish(sample(b))
    def failed(spec, start, end, dest):
        return {}, {"errors": [{"source": "vix", "error": "test outage"}]}
    out = refresh(models, runtime, now="2026-11-10T02:00:00Z", acquire_fn=failed)
    assert out["status"] == "source_error"
    assert store.latest("1.0")["as_of"] == "2026-11-05"
    assert status(store, b, "2026-11-10T02:00:00Z")["data_status"] == "refresh_error"


def test_missing_prices_invalidate_windows(setup):
    b = setup[2]
    idx = pd.bdate_range("2023-01-01", periods=400)
    prices = {s["id"]: pd.Series(100 * np.exp(np.arange(400) * .001 + np.sin(np.arange(400) * (.2+i*.013))*.01), index=idx) for i, s in enumerate(b.spec["sources"])}
    prices["sp500"].iloc[-5] = np.nan
    x, _ = build_features(prices, b.spec)
    assert pd.isna(x.iloc[-1]["rank252__return__sp500__21d"])
    assert pd.isna(x.iloc[-1]["rank252__corr__sp500__nasdaq100__21d"])
    assert pd.notna(x.iloc[-1]["msci_em_absolute_return21"])


def _official(last="2026-11-09"):
    idx = pd.bdate_range(end=last, periods=10)
    return pd.Series(np.linspace(18.0, 19.92, 10), index=idx)


def _patch_fmp(monkeypatch, rows):
    monkeypatch.setattr(sources, "fmp_rows", lambda symbol, start, end: (rows, b"fallback-blob", "https://fmp.test"))


def test_trailing_fallback_extends_official_series(monkeypatch):
    primary = _official()
    rows = [(d.date().isoformat(), float(v)) for d, v in primary.tail(4).items()] + [("2026-11-10", 19.46)]
    _patch_fmp(monkeypatch, rows)
    merged, info = trailing_fallback("vix", primary, "2026-11-10")
    assert merged.index.max() == pd.Timestamp("2026-11-10") and merged.loc["2026-11-10"] == 19.46
    assert len(merged) == len(primary) + 1 and merged.loc[primary.index].equals(primary)
    assert info["record"]["dates"] == ["2026-11-10"] and info["record"]["provider"] == "fmp"
    assert info["blob"] == b"fallback-blob"


def test_trailing_fallback_rejects_disagreement_and_thin_overlap(monkeypatch):
    primary = _official()
    good = [(d.date().isoformat(), float(v)) for d, v in primary.tail(4).items()]
    _patch_fmp(monkeypatch, good[:-2] + [(good[-2][0], good[-2][1] + 0.5)] + [("2026-11-10", 19.46)])
    with pytest.raises(SourceError, match="disagrees"):
        trailing_fallback("vix", primary, "2026-11-10")
    _patch_fmp(monkeypatch, good[-2:] + [("2026-11-10", 19.46)])
    with pytest.raises(SourceError, match="overlap"):
        trailing_fallback("vix", primary, "2026-11-10")


def test_trailing_fallback_skips_current_or_unlisted_sources(monkeypatch):
    primary = _official()
    _patch_fmp(monkeypatch, [("2026-11-10", 19.46)])
    s, info = trailing_fallback("vix", primary, "2026-11-09")
    assert s is primary and info is None
    s, info = trailing_fallback("sp500", primary, "2026-11-10")
    assert s is primary and info is None
    same = [(d.date().isoformat(), float(v)) for d, v in primary.tail(4).items()]
    _patch_fmp(monkeypatch, same)
    s, info = trailing_fallback("vix", primary, "2026-11-10")
    assert s is primary and info is None


def test_source_parser_rejects_duplicate_and_invalid():
    with pytest.raises(SourceError): series([("2026-01-02", 100), ("2026-01-02", 101)], "2026-01-01", "2026-11-10")
    with pytest.raises(SourceError): series([("2026-01-02", -1)], "2026-01-01", "2026-11-10")


def test_closed_session_classification(setup):
    b=setup[2]; day="2026-05-04"; dt=pd.Timestamp(day)
    p={s['id']:pd.Series([100.],index=[dt]) for s in b.spec['sources']}
    p['ftse100']=pd.Series(dtype=float,index=pd.DatetimeIndex([]))
    x=pd.DataFrame(1.,index=[dt],columns=b.names);x.loc[dt,'rank252__return__ftse100__21d']=np.nan
    result=availability(b,p,x,day)
    assert result['state']=='scheduled_closure' and result['scheduled_closures']==['ftse100']
