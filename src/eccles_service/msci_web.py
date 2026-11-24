"""Read the MSCI homepage's public index-directory action, with original checks."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
import base64
import hashlib
import json
import math
import re
import pandas as pd

URL = 'https://www.msci.com/indexes'
# Public read-action identifier in the homepage's published JavaScript, not a credential.
ACTION = '700450cf144fe2b0942cbbafc0c8d2b548c8e8259b'
NAMES = dict(zip(
    ['984000','982600','939200','912400','903600','928000','925000','975600','891800','106400','704856','704855'],
    ['USA','United Kingdom','Japan','Canada','Australia','Germany','France','Switzerland',
     'EM (Emerging Markets)','EMU','World Cyclical Sectors','World Defensive Sectors']))
TOLERANCE = 0.00050001


def arguments(day):
    if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        raise ValueError('Invalid requested date')
    datetime.strptime(day, '%Y-%m-%d')
    return [dict(filters={'index-type':['Equity'], 'currency':['USD'], 'variant':['STRD']},
                 page=[0,20], performanceVariant='STRD', performanceCurrency='USD',
                 calcDate=day, sortBy='featuredAsc'), None,
            dict(indexCodes=list(NAMES), featuredSortBy='indexCodes')]


def parse(blob, day):
    arguments(day)
    if len(blob) > 1_000_000:
        raise ValueError('Homepage response exceeds limit')
    lines = blob.decode('utf-8').splitlines()
    roots = [json.loads(line[2:]) for line in lines if line.startswith('0:')]
    if len(roots) != 1 or not re.fullmatch(r'\$@[a-f0-9]+', roots[0].get('a','')):
        raise ValueError('Unrecognized homepage read-action envelope')
    key = roots[0]['a'][2:] + ':'
    bodies = [json.loads(line[len(key):]) for line in lines if line.startswith(key)]
    if len(bodies) != 1 or not isinstance(bodies[0], dict):
        raise ValueError('Homepage read action returned no data')
    body = bodies[0]
    if body.get('total') != 12 or body.get('page') != 0 or len(body.get('data',[])) != 12:
        raise ValueError('Incomplete index-directory identity coverage')
    out = {}
    for row in body['data']:
        code = row.get('indexCode')
        if code not in NAMES or code in out or row.get('indexName') != 'MSCI '+NAMES[code]+' Index':
            raise ValueError('Homepage index identity mismatch')
        if row.get('assetClass') != 'EQUITY' or 'USD' not in row.get('currencies',[]) or 'STRD' not in row.get('variants',[]):
            raise ValueError('Homepage index specification mismatch')
        metadata_date = row.get('calcDate')
        arguments(metadata_date)
        entries = {p['id']:p.get('value') for p in row.get('performance',[])}
        if len(entries) != len(row.get('performance',[])):
            raise ValueError('Duplicate performance field')
        value = entries.get('lastPrice')
        change = entries.get('lastPriceChangePct')
        if value is not None:
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('Invalid homepage price')
            if abs(value*1000-round(value*1000)) > 1e-6:
                raise ValueError('Unexpected homepage price precision')
            if isinstance(change,bool) or not isinstance(change,(int,float)) or not math.isfinite(change):
                raise ValueError('Missing or invalid daily change')
        # calcDate is directory freshness metadata, not the historical request date.
        out[code] = dict(value=value, daily_change=change, metadata_date=metadata_date)
    return out


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_batch(day):
    req = Request(URL, data=json.dumps(arguments(day)).encode(), method='POST', headers={
        'User-Agent':'ECCLES original-index collector', 'Content-Type':'text/plain;charset=UTF-8',
        'Accept':'text/x-component', 'Next-Action':ACTION, 'Origin':'https://www.msci.com'})
    with build_opener(NoRedirect()).open(req, timeout=12) as response:
        if response.headers.get_content_type() != 'text/x-component':
            raise ValueError('Homepage challenge or unexpected content type')
        blob = response.read(1_000_001)
    rows = parse(blob, day)
    return dict(schema='msci_public_homepage_v1', requested_date=day, currency='USD', variant='STRD',
                action=ACTION, url=URL, observed_at=datetime.now(timezone.utc).isoformat(),
                sha256=hashlib.sha256(blob).hexdigest(), raw_base64=base64.b64encode(blob).decode(), rows=rows)


def check_capture(capture, day, maximum_age=None):
    if capture.get('schema') != 'msci_public_homepage_v1' or capture.get('requested_date') != day or \
       capture.get('currency') != 'USD' or capture.get('variant') != 'STRD' or \
       capture.get('action') != ACTION or capture.get('url') != URL:
        raise ValueError('Invalid homepage capture contract')
    observed = datetime.fromisoformat(capture['observed_at'])
    if observed.tzinfo is None:
        raise ValueError('Missing observation timezone')
    age = (datetime.now(timezone.utc)-observed).total_seconds()
    if age < -60 or (maximum_age is not None and age > maximum_age):
        raise ValueError('Stale or future homepage capture')
    blob = base64.b64decode(capture['raw_base64'], validate=True)
    if hashlib.sha256(blob).hexdigest() != capture['sha256']:
        raise ValueError('Homepage evidence hash mismatch')
    if parse(blob, day) != capture['rows']:
        raise ValueError('Homepage capture differs from raw response')
    return capture


def present_codes(capture):
    day = capture['requested_date']
    return [c for c,r in capture['rows'].items() if r['metadata_date'] == day and r['value'] is not None]


def capture_path(runtime, day):
    arguments(day)
    return Path(runtime)/'msci-web'/day/'latest.json'


def load_recent(runtime, day):
    p = capture_path(runtime,day)
    if not p.exists() or p.is_symlink() or p.stat().st_size > 2_000_000:
        return None
    try:
        return check_capture(json.loads(p.read_text()), day, maximum_age=120)
    except (ValueError, KeyError, TypeError):
        return None


def save_capture(runtime, capture):
    from .storage import atomic_json
    day = capture['requested_date']
    check_capture(capture, day)
    p = capture_path(runtime,day)
    stamp = datetime.fromisoformat(capture['observed_at']).strftime('%Y%m%dT%H%M%S%fZ')
    atomic_json(p.with_name(stamp+'.json'),capture)
    atomic_json(p,capture)


def extend(source, primary, end, cache, runtime, fetch_fn=get_batch):
    if source['provider'] != 'msci' or pd.Timestamp(end) in primary.index:
        return primary, None
    code = source['symbol']
    if code not in NAMES or source.get('currency') != 'USD' or source.get('variant') != 'STRD':
        raise ValueError('Unqualified homepage source')
    if primary.index.max() >= pd.Timestamp(end):
        return primary, None
    if not cache:
        try:
            cap = load_recent(runtime,end) or fetch_fn(end)
            check_capture(cap,end,maximum_age=120)
            cache.update(capture=cap, prior={})
        except Exception as ex:
            cache['error'] = type(ex).__name__
    if 'error' in cache:
        raise ValueError('Homepage acquisition failed: '+cache['error'])
    cap=cache['capture']
    if code not in present_codes(cap):
        return primary, None
    dates = primary.loc[:pd.Timestamp(end)-pd.Timedelta(days=1)].tail(3).index
    if len(dates) != 3:
        raise ValueError('Insufficient homepage overlap')
    differences=[]
    for dt in dates:
        day=dt.date().isoformat()
        if day not in cache['prior']:
            try:
                cache['prior'][day] = check_capture(fetch_fn(day),day)
            except Exception as ex:
                cache['prior'][day] = {'error':type(ex).__name__}
        if 'error' in cache['prior'][day]:
            raise ValueError('Homepage overlap request failed')
        value=cache['prior'][day]['rows'][code]['value']
        if value is None:
            raise ValueError('Missing homepage overlap value')
        differences.append(abs(float(primary.loc[dt])-value))
    if max(differences)>TOLERANCE:
        raise ValueError('Homepage differs from original USD Price history')
    value=cap['rows'][code]['value']
    # Apply the one-day relationship only when the previous US session is present.
    from .features import sessions
    previous=sessions('XNYS', (pd.Timestamp(end)-pd.Timedelta(days=10)).date().isoformat(),end)
    previous=previous[previous<pd.Timestamp(end)][-1]
    if previous in primary.index:
        derived=value/float(primary.loc[previous])-1
        if abs(derived-cap['rows'][code]['daily_change'])>0.000005:
            raise ValueError('Homepage daily return inconsistent with original previous close')
    merged=pd.concat([primary,pd.Series([value],index=pd.DatetimeIndex([end]))]).sort_index()
    record=dict(provider='msci_homepage_api', symbol=code, url=URL, dates=[end], currency='USD', variant='STRD',
                observed_at=cap['observed_at'], precision_decimals=3, automatic_collection=True,
                capture_method='public_homepage_read_action', sha256=cap['sha256'],
                overlap_count=3, overlap_max_difference=max(differences),
                request_date=end, directory_metadata_date=cap['rows'][code]['metadata_date'])
    return merged, dict(record=record,blob=base64.b64decode(cap['raw_base64']))
