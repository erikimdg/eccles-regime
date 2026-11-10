"""Daily point-in-time publication from an immutable source snapshot."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

import numpy as np
import pandas as pd

from . import __version__
from .features import build_features, expected_session, prepare_prices, sessions
from .model import digest
from .sources import acquire
from .storage import Store, active_bundle, load_version, exclusive, atomic_json, utcnow


def availability(bundle, prices, inputs, day):
    dt = pd.Timestamp(day)
    missing = [f for f in bundle.names if dt not in inputs.index or pd.isna(inputs.loc[dt, f])]
    closed, absent = [], []
    for s in bundle.spec["sources"]:
        key = s["id"]
        if dt in prices[key].index and pd.notna(prices[key].loc[dt]):
            continue
        cal = "XETR" if key == "vstoxx" else s["calendar"]
        if cal != "published" and dt not in sessions(cal, str(dt.date()), str((dt + pd.Timedelta(days=7)).date())):
            closed.append(key)
        else:
            absent.append(key)
    return dict(date=day, missing_features=missing, scheduled_closures=closed, absent_sources=absent,
                state="complete" if not missing else ("scheduled_closure" if closed and not absent else "missing_inputs"))


def make_payload(bundle, day, inputs, raw, snapshot_hash, observed_at, run_id, origin="live_original_sources"):
    if int(day[:4]) in bundle.spec.get("unpublished_years", []):
        raise ValueError("Year is reserved for warmup only")
    values = inputs.loc[day, bundle.names].to_numpy(dtype=float)
    description = bundle.describe(values, raw.loc[day].to_dict())
    return dict(model_version=bundle.version, service_version=__version__, model_sha256=bundle.hash,
                as_of=day, market="US close", observed_at=observed_at, published_at=utcnow(),
                run_id=run_id, snapshot_sha256=snapshot_hash, origin=origin,
                feature_vector_sha256=hashlib.sha256(values.astype("<f8").tobytes()).hexdigest(), **description)


def refresh(models, runtime, version=None, now=None, acquire_fn=acquire):
    with exclusive(runtime):
        b = load_version(models, version) if version else active_bundle(models, runtime)
        store = Store(runtime); store.register(b)
        now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
        end = expected_session(now)
        start = (pd.Timestamp(end) - pd.Timedelta(days=b.spec["warmup_calendar_days"])).date().isoformat()
        run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        root = Path(runtime) / "snapshots" / run_id
        attempt = dict(run_id=run_id, model_version=b.version, started_at=utcnow(), expected_as_of=end, status="running", published=0)
        store.attempt(attempt)
        try:
            raw_prices, manifest = acquire_fn(b.spec, start, end, root)
            if manifest["errors"]:
                attempt.update(status="source_error", errors=manifest["errors"])
            else:
                prices = prepare_prices(raw_prices, b.spec["sources"], start, end)
                inputs, raw = build_features(prices, b.spec)
                inputs.to_csv(root / "features.csv", index_label="date")
                raw.to_csv(root / "raw-features.csv", index_label="date")
                observed = utcnow()
                snapshot_hash = digest(root / "sources.json")
                complete = inputs.dropna(subset=b.names)
                # The first installation starts with the latest complete day. Later runs catch up
                # from the previous published day; research history is kept in separate artifacts.
                latest = store.latest(b.version)
                allowed = complete[(complete.index >= pd.Timestamp(b.spec["publication_start"])) &
                                   (~complete.index.year.isin(b.spec.get("unpublished_years", [])))]
                if latest:
                    allowed = allowed[allowed.index >= pd.Timestamp(latest["as_of"])]
                else:
                    allowed = allowed.tail(1)
                revisions = []
                for dt in allowed.index:
                    day = dt.date().isoformat()
                    payload = make_payload(b, day, inputs, raw, snapshot_hash, observed, run_id)
                    old = store.get(b.version, day)
                    if old and old["feature_vector_sha256"] != payload["feature_vector_sha256"]:
                        revisions.append(dict(date=day, preserved_regime=old["regime"], revised_snapshot_regime=payload["regime"], original_hash=old["feature_vector_sha256"], revised_hash=payload["feature_vector_sha256"]))
                    attempt["published"] += int(store.publish(payload))
                current = store.latest(b.version)
                quality = availability(b, prices, inputs, end)
                exclusions = [availability(b, prices, inputs, dt.date().isoformat()) for dt in inputs.index if dt >= pd.Timestamp(latest["as_of"] if latest else end) and inputs.loc[dt, b.names].isna().any()]
                atomic_json(root / "excluded-dates.json", exclusions)
                atomic_json(root / "revisions.json", revisions)
                attempt.update(status="current" if current and current["as_of"] == end else quality["state"],
                               available_as_of=current["as_of"] if current else None, availability=quality,
                               snapshot_sha256=snapshot_hash, source_dates={r["source"]: r["last"] for r in manifest["records"]},
                               revised_existing_days=revisions, excluded_dates=exclusions)
        except Exception as ex:
            # Keep secrets and untrusted provider text out of API diagnostics.
            attempt.update(status="pipeline_error", error_type=type(ex).__name__)
            store.attempt({**attempt, "finished_at": utcnow()})
            raise
        attempt["finished_at"] = utcnow()
        store.attempt(attempt)
        atomic_json(Path(runtime) / "last-run.json", attempt)
        return attempt


def status(store, bundle, now=None):
    expected = expected_session(now)
    current, attempt = store.latest(bundle.version), store.last_attempt(bundle.version)
    latest_date = current["as_of"] if current else None
    fresh = latest_date == expected
    error = bool(attempt and attempt["status"] in {"source_error", "pipeline_error"})
    return dict(model_version=bundle.version, expected_as_of=expected, available_as_of=latest_date,
                data_status="current" if fresh and not error else ("refresh_error" if error else (attempt["status"] if attempt and attempt["expected_as_of"] == expected else "stale")),
                fresh=fresh, last_attempt=attempt, checked_at=utcnow())
