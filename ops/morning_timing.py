"""Observe existing refreshes and the authenticated API without changing inference."""
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import csv
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid

KST = ZoneInfo('Asia/Seoul')
ROOT = Path('/root/eccles-regime')
PROSPECTIVE_START = '2026-11-20'


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('w') as f:
            json.dump(value, f, indent=2, allow_nan=False)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        tmp.chmod(0o640)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


API_CHECK = '''import json,os
from datetime import datetime,timezone
from urllib.request import Request,urlopen
from urllib.error import HTTPError
out={}
for name,path in [('ready','/health/ready'),('latest','/v1/regime/latest')]:
    req=Request('http://127.0.0.1:18771'+path,headers={'Authorization':'Bearer '+os.environ['ECCLES_API_KEY']})
    try:
        with urlopen(req,timeout=8) as r: code,obj=r.status,json.load(r)
    except HTTPError as r: code,obj=r.code,json.load(r)
    except Exception as e:
        out[name]={'error_type':type(e).__name__};continue
    view=obj.get('status',obj)
    safe={k:view.get(k) for k in ['expected_as_of','available_as_of','data_status','fresh']}
    if 'result' in obj and obj['result']:
        safe['result']={k:obj['result'].get(k) for k in ['as_of','published_at','run_id','model_version']}
    out[name]={'http_status':code,'body':safe}
out['checked_at']=datetime.now(timezone.utc).isoformat()
print(json.dumps(out))
'''


def api_check():
    try:
        r = subprocess.run(['docker', 'exec', '-i', 'eccles-api', 'python', '-'],
                           input=API_CHECK, text=True, capture_output=True, timeout=25)
        if r.returncode:
            return {'checked_at': now(), 'error_type': 'APIProbeProcessError'}
        return json.loads(r.stdout)
    except Exception as e:
        return {'checked_at': now(), 'error_type': type(e).__name__}


def api_ready(api, target):
    for key in ['ready', 'latest']:
        item = api.get(key, {}); body = item.get('body', {})
        if item.get('http_status') != 200 or body.get('data_status') != 'current':
            return False
        if body.get('fresh') is not True or body.get('expected_as_of') != target or body.get('available_as_of') != target:
            return False
    return api['latest']['body'].get('result', {}).get('as_of') == target


def source_observations(snapshot, manifest, specs, target):
    records = {r['source']: r for r in manifest.get('records', [])}
    out = []
    for spec in specs:
        sid = spec['id']
        if not re.fullmatch(r'[a-z0-9_]+', sid): raise ValueError('Invalid source id')
        row = records.get(sid)
        obs = {'source': sid, 'provider': spec['provider'], 'symbol': spec['symbol'],
               'target_present': None, 'observed_at': None, 'last_value_date': None}
        if row:
            obs.update(observed_at=row.get('observed_at'), last_value_date=row.get('last'))
            p = snapshot / (sid + '.csv')
            if p.exists():
                with p.open() as f:
                    obs['target_present'] = any(r.get('date', '')[:10] == target and r.get('close') not in [None, '', 'NaN', 'nan'] for r in csv.DictReader(f))
            fallback = row.get('fallback', {})
            if target in fallback.get('dates', []):
                obs['observed_at'] = fallback.get('observed_at') or obs['observed_at']
                obs['fallback_provider'] = fallback.get('provider')
                obs['fallback_symbol'] = fallback.get('symbol')
        obs['observation_state'] = ('present' if obs['target_present'] is True else
                                    'missing_date' if obs['target_present'] is False else 'not_observed')
        out.append(obs)
    return out


def rollup(summary, event):
    target = event.get('expected_as_of')
    if not target: return summary
    date_kst = datetime.fromisoformat(event['trigger_started_at']).astimezone(KST).date().isoformat()
    if not summary:
        summary = {'observation_date_kst': date_kst, 'expected_as_of': target,
                   'phase': 'prospective' if date_kst >= PROSPECTIVE_START else 'commissioning',
                   'deadline_kst': date_kst + 'T10:00:00+09:00', 'event_count': 0,
                   'first_api_ready_observed_at': None, 'result_published_at': None,
                   'last_api_not_ready_observed_at': None, 'sources': {}}
    summary['event_count'] += 1
    summary['last_event_at'] = event['recorded_at']
    summary['last_event_id'] = event['invocation_id']
    for row in event['sources']:
        dst = summary['sources'].setdefault(row['source'], {'first_present_observed_at': None,
              'last_missing_before_first_present_at': None, 'latest_observation_state': 'not_observed'})
        dst['latest_observation_state'] = row['observation_state']
        dst['latest_observed_at'] = row['observed_at']
        if row['observation_state'] == 'present' and dst['first_present_observed_at'] is None:
            dst['first_present_observed_at'] = row['observed_at']
        if row['observation_state'] == 'missing_date' and dst['first_present_observed_at'] is None:
            dst['last_missing_before_first_present_at'] = row['observed_at']
    api = event['api']; checked = api.get('checked_at')
    if event['api_ready_for_target'] and summary['first_api_ready_observed_at'] is None:
        summary['first_api_ready_observed_at'] = checked
        summary['result_published_at'] = api['latest']['body']['result'].get('published_at')
    elif not event['api_ready_for_target'] and summary['first_api_ready_observed_at'] is None:
        # A transport failure is unknown, not proof of API/data unavailability.
        ready = api.get('ready', {})
        if ready.get('http_status') == 503 and ready.get('body', {}).get('expected_as_of') == target:
            summary['last_api_not_ready_observed_at'] = checked
    first = summary['first_api_ready_observed_at']
    summary['deadline_status'] = ('not_yet_observed' if first is None else
        'observed_before_10' if datetime.fromisoformat(first) < datetime.fromisoformat(summary['deadline_kst']) else
        'first_observed_at_or_after_10')
    # Keep the original 10:00 fields as a historical comparator.
    summary['deadline_09_kst'] = date_kst + 'T09:00:00+09:00'
    summary['deadline_09_status'] = ('not_yet_observed' if first is None else
        'observed_before_09' if datetime.fromisoformat(first) < datetime.fromisoformat(summary['deadline_09_kst']) else
        'first_observed_at_or_after_09')
    present = [r.get('first_present_observed_at') for r in summary['sources'].values()]
    summary['all_sources_first_present_observed_at'] = max(present) if len(present) == 18 and all(present) else None
    summary['delivery_target_current_kst'] = '09:00'
    summary['last_missing_sources'] = [s['source'] for s in event['sources'] if s['target_present'] is False]
    summary['last_unobserved_sources'] = [s['source'] for s in event['sources'] if s['target_present'] is None]
    summary['last_service_result'] = event['service_result']
    summary['last_attempt_status'] = event.get('attempt_status')
    return summary


def readable(summary):
    def kst(t): return datetime.fromisoformat(t).astimezone(KST).strftime('%H:%M:%S') if t else 'Not observed'
    out = ['# Morning timing', '', f"Date: {summary['observation_date_kst']} KST. Required US-session date: {summary['expected_as_of']}.",
           f"Phase: {summary['phase']}. Historical comparator: before 10:00 KST. Status: {summary['deadline_status']}.", '',
           f"First verified API ready: {kst(summary['first_api_ready_observed_at'])} KST.",
           f"Result publication timestamp: {summary['result_published_at'] or 'Not observed'}.", '',
           '| Source | Last missing before first presence, KST | First presence observed, KST | Latest state |',
           '| --- | --- | --- | --- |']
    for sid, row in summary['sources'].items():
        out.append(f"| {sid} | {kst(row['last_missing_before_first_present_at'])} | {kst(row['first_present_observed_at'])} | {row['latest_observation_state']} |")
    out += ['', 'Before-09:00 observation: ' + summary.get('deadline_09_status', 'not_recorded'),
            'All 18 source first-presence timestamps available by: ' + kst(summary.get('all_sources_first_present_observed_at')) + ' KST.']
    out += ['', 'Times are observations made by scheduled polling. Exact provider publication falls within the polling interval when both bounds exist. A first observation after the deadline leaves earlier availability unverified. Commissioning records are excluded from prospective morning evaluation.', '']
    return '\n'.join(out)


def main():
    action = sys.argv[1]
    ident = os.environ.get('INVOCATION_ID', '')
    if not re.fullmatch('[a-f0-9]{32}', ident): raise ValueError('Missing systemd invocation id')
    dest = ROOT / 'runtime/timing'; dest.mkdir(parents=True, exist_ok=True)
    marker = dest / 'starts' / (ident + '.json')
    if action == 'start':
        atomic(marker, {'invocation_id': ident, 'trigger_started_at': now()})
        return
    if action != 'end': raise ValueError('Unknown action')
    start = json.loads(marker.read_text())
    with sqlite3.connect(f'file:{ROOT}/runtime/regimes.sqlite3?mode=ro', uri=True, timeout=10) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('SELECT payload FROM attempts WHERE started >= ? ORDER BY started DESC LIMIT 1', (start['trigger_started_at'],)).fetchall()
    attempt = json.loads(rows[0][0]) if rows else {}
    event = {**start, 'recorded_at': now(), 'service_result': os.environ.get('SERVICE_RESULT', 'unknown'),
             'run_id': attempt.get('run_id'), 'expected_as_of': attempt.get('expected_as_of'),
             'attempt_status': attempt.get('status'), 'refresh_started_at': attempt.get('started_at'),
             'refresh_finished_at': attempt.get('finished_at'), 'sources': []}
    if attempt.get('run_id'):
        rid = attempt['run_id']
        if not re.fullmatch(r'\d{8}T\d{6}Z-[a-f0-9]{8}', rid): raise ValueError('Invalid run id')
        version = attempt.get('model_version', '')
        if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', version): raise ValueError('Invalid model version')
        spec = json.loads((ROOT / 'models' / version / 'model.json').read_text())
        snap = ROOT / 'runtime/snapshots' / rid
        p = snap / 'sources.json'
        manifest = json.loads(p.read_text()) if p.exists() else {}
        event['sources'] = source_observations(snap, manifest, spec['sources'], attempt['expected_as_of'])
        event['msci_public_download'] = manifest.get('msci_public_download')
        event['msci_homepage_api'] = manifest.get('msci_homepage_api')
    poll_path = ROOT / 'runtime/msci-web/last-poll.json'
    if poll_path.exists():
        poll = json.loads(poll_path.read_text())
        if poll.get('started_at', '') >= start['trigger_started_at']:
            event['homepage_poll'] = {k:v for k,v in poll.items() if k != 'refresh'}
    event['api'] = api_check()
    event['api_ready_for_target'] = bool(event['expected_as_of'] and api_ready(event['api'], event['expected_as_of']))
    event['recorded_at'] = now()
    if event['refresh_started_at'] and event['refresh_finished_at']:
        event['refresh_seconds'] = (datetime.fromisoformat(event['refresh_finished_at']) - datetime.fromisoformat(event['refresh_started_at'])).total_seconds()
    with (dest / 'recorder.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        event_path = dest / 'events' / (ident + '.json')
        if event_path.exists(): return
        # Update the derived day file before the immutable receipt. A failure is journalled.
        if event['expected_as_of']:
            day = datetime.fromisoformat(start['trigger_started_at']).astimezone(KST).date().isoformat()
            daily = dest / 'daily' / (day + '_' + event['expected_as_of'] + '.json')
            summary = json.loads(daily.read_text()) if daily.exists() else None
            if not summary or summary.get('last_event_id') != ident:
                summary = rollup(summary, event)
                atomic(daily, summary)
            daily.with_suffix('.md').write_text(readable(summary))
            atomic(dest / 'latest-summary.json', summary)
        atomic(event_path, event)
        atomic(dest / 'latest.json', event)
    print(json.dumps({'timing_recorded': True, 'invocation_id': ident, 'run_id': event['run_id'],
                      'api_ready_for_target': event['api_ready_for_target']}))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(json.dumps({'timing_recorded': False, 'error_type': type(e).__name__}), file=sys.stderr)
        sys.exit(1)
