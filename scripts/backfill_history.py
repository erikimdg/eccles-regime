"""Publish verified historical inputs using the installed frozen service engine.

Operator-only command. No source acquisition, fitting, activation or API restart.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from eccles_service.model import digest
from eccles_service.pipeline import make_payload
from eccles_service.storage import Store, exclusive, load_version, utcnow


def read_frame(root, manifest, name, columns):
    path = root / name
    if path.is_symlink() or digest(path) != manifest['files'][name]:
        raise ValueError(f'Historical file checksum mismatch: {name}')
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    if (list(frame.columns) != columns or not isinstance(frame.index, pd.DatetimeIndex)
            or not frame.index.is_unique or not frame.index.is_monotonic_increasing
            or frame.index.tz is not None or not frame.index.equals(frame.index.normalize())
            or not len(frame) or not np.isfinite(frame.to_numpy(dtype=float)).all()):
        raise ValueError(f'Invalid historical frame: {name}')
    return frame


def backfill(models, runtime, archive, expected_manifest_sha256):
    archive = Path(archive)
    manifest_hash = digest(archive / 'manifest.json')
    if manifest_hash != expected_manifest_sha256:
        raise ValueError('Historical manifest checksum mismatch')
    manifest = json.loads((archive / 'manifest.json').read_text())
    if manifest['schema_version'] != 1 or manifest['origin'] != 'historical_replay':
        raise ValueError('Unsupported historical manifest')
    b = load_version(models, manifest['model_version'])
    if b.hash != manifest['model_sha256']:
        raise ValueError('Historical model hash mismatch')
    inputs = read_frame(archive, manifest, 'features.csv', b.names)
    raw = read_frame(archive, manifest, 'raw-features.csv', b.names)
    expected = read_frame(archive, manifest, 'expected.csv',
                          ['state', 'distance_C0', 'distance_C1', 'distance_C2'] + b.names)
    if not inputs.index.equals(raw.index) or not inputs.index.equals(expected.index):
        raise ValueError('Historical date alignment mismatch')
    periods = {}
    for part in manifest['periods']:
        name, start, end = part['name'], part['start'], part['end']
        if name not in {'training', 'validation'} or start > end:
            raise ValueError('Invalid historical period')
        training = b.spec['training']
        if name == 'training' and (start != training['start'] or end != training['end']):
            raise ValueError('Training range must match the frozen model')
        if name == 'validation' and start <= training['end']:
            raise ValueError('Validation overlaps training')
        dates = inputs.loc[start:end].index
        if len(dates) != part['count'] or not len(dates):
            raise ValueError('Historical period count mismatch')
        for dt in dates:
            if dt in periods or dt.year in b.spec.get('unpublished_years', []):
                raise ValueError('Overlapping period or warmup year')
            periods[dt] = name
    if len(periods) != len(inputs):
        raise ValueError('Unclassified historical dates')
    pred = b.infer(inputs.to_numpy())
    if not np.array_equal(pred['state'], expected['state'].to_numpy()):
        raise ValueError('Historical clustering differs from the saved research')
    for actual, saved in [(pred['distances'], expected[['distance_C0', 'distance_C1', 'distance_C2']].to_numpy()),
                          (pred['relevance'], expected[b.names].to_numpy()),
                          (pred['relevance'].sum(1), pred['margin'])]:
        if not np.allclose(actual, saved, rtol=1e-9, atol=1e-9):
            raise ValueError('Historical distance or NEON verification failed')
    run_id = 'history-' + manifest_hash[:16]
    inserted = skipped = 0
    with exclusive(runtime):
        store = Store(runtime)
        # Require an existing validated registration; do not activate or register models here.
        with store.connect() as conn:
            registered = conn.execute('SELECT hash FROM models WHERE version=?', (b.version,)).fetchone()
            if registered != (b.hash,):
                raise ValueError('Historical model is not registered')
            conn.execute('BEGIN IMMEDIATE')
            for dt in inputs.index:
                day = dt.date().isoformat()
                payload = make_payload(b, day, inputs, raw, manifest_hash, None, run_id,
                                       origin='historical_replay')
                payload.update(sample_period=periods[dt], in_sample=periods[dt] == 'training',
                               historical_vintage_verified=False)
                old = conn.execute('SELECT payload FROM results WHERE version=? AND day=?',
                                   (b.version, day)).fetchone()
                if old:
                    old = json.loads(old[0])
                    # Exact idempotent replay only. Never overwrite a published record.
                    comparable = {k: v for k, v in payload.items() if k not in {'published_at', 'service_version'}}
                    previous = {k: v for k, v in old.items() if k not in {'published_at', 'service_version'}}
                    if previous != comparable:
                        raise ValueError(f'Immutable historical record conflict: {day}')
                    skipped += 1
                    continue
                conn.execute('INSERT INTO results VALUES (?,?,?)',
                             (b.version, day, json.dumps(payload, ensure_ascii=False, allow_nan=False)))
                inserted += 1
    return dict(model_version=b.version, model_sha256=b.hash, manifest_sha256=manifest_hash,
                inserted=inserted, already_present=skipped, total=len(inputs),
                periods=manifest['periods'], verified_at=utcnow(), fits=0,
                max_distance_error=float(np.abs(pred['distances'] - expected.iloc[:, 1:4].to_numpy()).max()),
                max_relevance_error=float(np.abs(pred['relevance'] - expected[b.names].to_numpy()).max()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', required=True)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(backfill(args.models, args.runtime, args.archive, args.manifest_sha256), indent=2))
