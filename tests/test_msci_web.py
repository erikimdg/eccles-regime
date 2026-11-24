from pathlib import Path
from datetime import datetime,timezone,timedelta
import base64,copy,hashlib,io,json,os
import numpy as np
import pandas as pd
import pytest
from eccles_service import msci_web as w, sources, morning
from eccles_service.pipeline import refresh
from eccles_service.storage import load_version,activate,Store

ROOT=Path(__file__).resolve().parents[1]
REPLAYS=Path(os.environ.get('ECCLES_REPLAY_ROOT',str(ROOT.parent/'runs')))
R=REPLAYS/'2026-11-23-homepage-automation'
if not (R/'server-before.json').is_file():
    pytest.skip('Set ECCLES_REPLAY_ROOT for private recorded-source checks', allow_module_level=True)
BEFORE=json.loads((R/'server-before.json').read_text())
SPEC=BEFORE['model_spec']
PRICES={k:pd.read_csv(io.StringIO(v),index_col=0,parse_dates=True)['close'] for k,v in BEFORE['prices'].items()}

def capture(day):
    b=(R/'input'/('server-'+day+'.rsc')).read_bytes()
    return dict(schema='msci_public_homepage_v1',requested_date=day,currency='USD',variant='STRD',action=w.ACTION,
                url=w.URL,observed_at=datetime.now(timezone.utc).isoformat(),raw_base64=base64.b64encode(b).decode(),
                sha256=hashlib.sha256(b).hexdigest(),rows=w.parse(b,day))

def mutated(change):
    b=(R/'input/server-2026-11-20.rsc').read_text();lines=b.splitlines()
    obj=json.loads(next(s[2:] for s in lines if s.startswith('1:')))
    change(obj)
    return ('\n'.join('1:'+json.dumps(obj) if s.startswith('1:') else s for s in lines)+'\n').encode()

def test_original_48_values_and_future_absence():
    count=0
    for day in ['2026-11-17','2026-11-18','2026-11-19','2026-11-20']:
        cap=capture(day)
        for s in SPEC['sources']:
            if s['provider']=='msci':
                assert abs(cap['rows'][s['symbol']]['value']-PRICES[s['id']].loc[day]) <= w.TOLERANCE
                count+=1
    assert count==48
    assert w.present_codes(capture('2026-11-23'))==[]

@pytest.mark.parametrize('change',[
    lambda o:o.update(total=11),lambda o:o['data'].pop(),lambda o:o['data'][0].update(indexCode='990100'),
    lambda o:o['data'][0].update(indexName='MSCI USA Growth Index'),lambda o:o['data'][0].update(currencies=['EUR']),
    lambda o:o['data'][0].update(variants=['GRTR']),lambda o:o['data'][0].update(assetClass='FIXED_INCOME'),
    lambda o:o['data'][0].update(calcDate='not a date'),lambda o:o['data'][0]['performance'][0].update(value=-1),
    lambda o:o['data'][0]['performance'][0].update(value=True),lambda o:o['data'][0]['performance'][0].update(value=float('nan')),
    lambda o:o['data'][0]['performance'][0].update(value=6577.86123),lambda o:o['data'].__setitem__(1,o['data'][0]),
    lambda o:o['data'][0]['performance'].append(o['data'][0]['performance'][0])])
def test_reject_bad_response(change):
    with pytest.raises(ValueError):w.parse(mutated(change),'2026-11-20')

@pytest.mark.parametrize('blob',[b'<title>Challenge Validation</title>',b'0:{"a":"$@1"}\n1:null',b'{}'])
def test_invalid_action_and_challenge(blob):
    with pytest.raises(ValueError):w.parse(blob,'2026-11-20')

def test_partial_missing_stale_dates_no_fill():
    b=mutated(lambda o:o['data'][0]['performance'][0].update(value=None))
    rows=w.parse(b,'2026-11-20');assert rows['984000']['value'] is None
    cap=capture('2026-11-20');cap['rows']=rows
    assert len(w.present_codes(cap))==11
    cap['rows']['912400']['metadata_date']='2026-11-19'
    assert '912400' not in w.present_codes(cap)

def test_capture_identity_hash_and_age(tmp_path):
    c=capture('2026-11-20');w.save_capture(tmp_path,c)
    assert w.load_recent(tmp_path,'2026-11-20')==c
    for key,value in [('variant','GRTR'),('currency','EUR'),('requested_date','2026-11-19'),('sha256','bad')]:
        bad={**c,key:value}
        with pytest.raises(ValueError):w.check_capture(bad,'2026-11-20')
    c['observed_at']=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()
    with pytest.raises(ValueError):w.check_capture(c,'2026-11-20',120)

def test_all_12_extensions_four_batch_requests_preserve_history(tmp_path):
    calls=[];cache={}
    for s in SPEC['sources']:
        if s['provider']!='msci':continue
        before=PRICES[s['id']].loc[:'2026-11-19']
        new,info=w.extend(s,before,'2026-11-20',cache,tmp_path,lambda d:(calls.append(d) or capture(d)))
        pd.testing.assert_series_equal(before,new.loc[before.index],check_names=False)
        assert abs(new.loc['2026-11-20']-PRICES[s['id']].loc['2026-11-20'])<=w.TOLERANCE
        assert info['record']['overlap_count']==3
    assert calls==['2026-11-20','2026-11-17','2026-11-18','2026-11-19']

def test_current_primary_no_network(tmp_path):
    s=next(s for s in SPEC['sources'] if s['id']=='msci_us')
    p=PRICES['msci_us'];new,info=w.extend(s,p,'2026-11-20',{},tmp_path,lambda d:pytest.fail('network'))
    pd.testing.assert_series_equal(p,new);assert info is None

def test_disagreement_rejected(tmp_path):
    s=next(s for s in SPEC['sources'] if s['id']=='msci_us')
    with pytest.raises(ValueError,match='differs'):
        w.extend(s,PRICES['msci_us'].loc[:'2026-11-19']+1,'2026-11-20',{},tmp_path,capture)

def test_live_homepage_precedes_xls_and_provenance(monkeypatch,tmp_path):
    from eccles_service import msci_download
    monkeypatch.setattr(msci_download,'extend',lambda *a:pytest.fail('XLS called'))
    original=w.extend
    monkeypatch.setattr(w,'extend',lambda s,p,end,c,rt:original(s,p,end,c,rt,capture))
    monkeypatch.setattr(sources,'acquire_one',lambda s,*a:(PRICES[s['id']].loc[:'2026-11-19'] if s['provider']=='msci' else PRICES[s['id']],b'original','original'))
    monkeypatch.setenv('ECCLES_RUNTIME',str(tmp_path))
    prices,m=sources.acquire(SPEC,'2025-04-01','2026-11-20',tmp_path/'snapshot')
    assert not m['errors'] and len(m['records'])==18
    assert len(m['msci_homepage_api']['target_present'])==12
    assert sum(r.get('fallback',{}).get('provider')=='msci_homepage_api' for r in m['records'])==12
    assert (tmp_path/'snapshot/msci-homepage-evidence.json').exists()

def test_endpoint_publication_exact_model_immutable(monkeypatch,tmp_path):
    b=load_version(ROOT/'models','1.0');activate(ROOT/'models',tmp_path,'1.0')
    original=w.extend
    monkeypatch.setattr(w,'extend',lambda s,p,end,c,rt:original(s,p,end,c,rt,capture))
    monkeypatch.setattr(sources,'acquire_one',lambda s,*a:(PRICES[s['id']].loc[:'2026-11-19'] if s['provider']=='msci' else PRICES[s['id']],b'original','original'))
    monkeypatch.setenv('ECCLES_RUNTIME',str(tmp_path))
    out=refresh(ROOT/'models',tmp_path,now='2026-11-21T00:59:00Z')
    assert out['status']=='current'
    payload=Store(tmp_path).latest('1.0')
    assert payload['as_of']=='2026-11-20' and payload['origin']=='live_original_sources_with_msci_homepage_api'
    assert payload['model_sha256']==b.hash
    assert len(payload['input_quality']['msci_homepage_api']['affected_sources'])==12
    again=refresh(ROOT/'models',tmp_path,now='2026-11-21T01:00:00Z')
    assert again['published']==0 and Store(tmp_path).latest('1.0')==payload

def test_partial_workbook_missing_is_not_error():
    from eccles_service import msci_download
    raw=json.loads((REPLAYS/'2026-11-20-before09-original-feed/observations/20261121-raw.json').read_text())
    parsed=msci_download.parse_download(base64.b64decode(raw['downloads'][0]['raw_base64']),list(msci_download.NAMES),'2026-11-06','2026-11-20')
    assert sum(pd.Timestamp('2026-11-20') in s.index for s in parsed.values())==6

def test_morning_full_slots_and_trigger_cooldown():
    now=datetime(2026,11,21,0,59,tzinfo=timezone.utc)
    assert not morning.full_due(now)
    assert morning.full_due(now-timedelta(minutes=1))
    cap=capture('2026-11-20');last={'source_dates':{s['id']:'2026-11-19' for s in SPEC['sources']}}
    sig=morning.decision(last,None,'2026-11-20',cap,SPEC['sources'],{},now)
    assert len(sig)==64
    assert morning.decision(last,None,'2026-11-20',cap,SPEC['sources'],{'trigger_signature':sig,'triggered_at':now.isoformat()},now)=='waiting_full_retry'
    assert morning.decision({'status':'current'},{'as_of':'2026-11-20'},'2026-11-20',cap,SPEC['sources'],{},now)=='already_current'

def test_morning_auto_trigger_and_error_backoff(tmp_path):
    activate(ROOT/'models',tmp_path,'1.0')
    now=datetime(2026,11,20,23,59,tzinfo=timezone.utc)
    store=Store(tmp_path)
    store.attempt(dict(run_id='test-missing',model_version='1.0',started_at=now.isoformat(),
                       expected_as_of='2026-11-20',status='missing_inputs',
                       source_dates={s['id']:'2026-11-19' for s in SPEC['sources']}))
    calls=[]
    out=morning.run(ROOT/'models',tmp_path,now=now,fetch_fn=capture,
                    refresh_fn=lambda *a,**k:(calls.append(k) or {'status':'current'}))
    assert out['status']=='trigger_refresh' and len(calls)==1
    assert w.load_recent(tmp_path,'2026-11-20') is not None
    out=morning.run(ROOT/'models',tmp_path,now=now+timedelta(minutes=2),fetch_fn=capture,
                    refresh_fn=lambda *a,**k:pytest.fail('duplicate full refresh'))
    assert out['status']=='waiting_full_retry'
    def fail(day):raise ValueError('schema changed')
    out=morning.run(ROOT/'models',tmp_path,now=now+timedelta(minutes=3),fetch_fn=fail)
    assert out['status']=='homepage_error'
    out=morning.run(ROOT/'models',tmp_path,now=now+timedelta(minutes=4),fetch_fn=lambda *a:pytest.fail('backoff'))
    assert out['status']=='homepage_error_backoff'
