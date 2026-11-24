"""Light homepage polling between the existing full-source refresh slots."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import os
import pandas as pd
from .features import expected_session
from .storage import active_bundle, Store, atomic_json, exclusive
from .pipeline import refresh
from .msci_web import get_batch, present_codes, save_capture

FULL_SLOTS = {'07:40','08:00','08:20','08:40','08:50','08:55','08:58',
              '09:20','09:40','09:50','09:55','09:58','10:40','12:20','16:20'}


def full_due(now):
    local=now.astimezone(ZoneInfo('Asia/Seoul'))
    return local.strftime('%H:%M') in FULL_SLOTS or not (7*60+40 <= local.hour*60+local.minute <= 10*60+40)


def decision(last, latest, target, capture, specs, previous, now):
    if latest and latest['as_of'] == target and last and last.get('status') == 'current':
        return 'already_current'
    codes=set(present_codes(capture))
    missing={s['symbol'] for s in specs if s['provider']=='msci' and last.get('source_dates',{}).get(s['id']) != target}
    if not codes.intersection(missing) and missing:
        return 'waiting_homepage'
    # Avoid repeated full-history requests for unchanged partial coverage or other-source failures.
    signature=hashlib.sha256(json.dumps({'target':target,'usable':sorted(codes),
                                      'missing':sorted(missing)},sort_keys=True).encode()).hexdigest()
    if previous.get('trigger_signature') == signature:
        age=(now-datetime.fromisoformat(previous['triggered_at'])).total_seconds()
        if age < 300:
            return 'waiting_full_retry'
    return signature


def run(models, runtime, now=None, fetch_fn=get_batch, refresh_fn=refresh):
    now=datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None:raise ValueError('Morning clock requires timezone')
    target=expected_session(pd.Timestamp(now))
    b=active_bundle(models,runtime)
    store=Store(runtime)
    last=store.last_attempt(b.version) or {}
    if full_due(now) or last.get('expected_as_of') != target:
        return refresh_fn(models,runtime,now=pd.Timestamp(now))
    latest=store.latest(b.version)
    folder=Path(runtime)/'msci-web/polls'
    stamp=now.strftime('%Y%m%dT%H%M%S%fZ')
    event=dict(target=target,started_at=now.isoformat(),mode='homepage_poll')
    should_refresh=False
    # The same service unit serializes scheduled work; this also excludes manual writers.
    with exclusive(runtime):
        statepath=Path(runtime)/'msci-web/trigger-state.json'
        previous=json.loads(statepath.read_text()) if statepath.exists() else {}
        if latest and latest['as_of']==target and last.get('status')=='current':
            event['status']='already_current'
        elif previous.get('retry_after') and now < datetime.fromisoformat(previous['retry_after']):
            event['status']='homepage_error_backoff'
        else:
            try:
                capture=fetch_fn(target)
                save_capture(runtime,capture)
                event.update(observed_at=capture['observed_at'],present_codes=present_codes(capture),
                             missing_codes=[c for c in capture['rows'] if c not in present_codes(capture)],
                             sha256=capture['sha256'])
                verdict=decision(last,latest,target,capture,b.spec['sources'],previous,now)
                if verdict in {'already_current','waiting_homepage','waiting_full_retry'}:
                    event['status']=verdict
                else:
                    event['status']='trigger_refresh'
                    atomic_json(statepath,dict(trigger_signature=verdict,triggered_at=now.isoformat()))
                    should_refresh=True
            except Exception as ex:
                event.update(status='homepage_error',error_type=type(ex).__name__)
                atomic_json(statepath,{**previous,'retry_after':(now+timedelta(minutes=5)).isoformat()})
        event['finished_at']=datetime.now(timezone.utc).isoformat()
        atomic_json(folder/(stamp+'.json'),event)
        atomic_json(Path(runtime)/'msci-web/last-poll.json',event)
    if should_refresh:
        event['refresh']=refresh_fn(models,runtime,now=pd.Timestamp(now))
        atomic_json(folder/(stamp+'.json'),event)
    return event


if __name__=='__main__':
    result=run(os.environ.get('ECCLES_MODELS','models'),os.environ.get('ECCLES_RUNTIME','runtime'))
    print(json.dumps(result))
    if result.get('status') in {'source_error','pipeline_error'} or result.get('refresh',{}).get('status') in {'source_error','pipeline_error'}:
        raise SystemExit(1)
