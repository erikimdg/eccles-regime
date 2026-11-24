import copy,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from eccles_service.sources import msci_homepage_extension,SourceError
from eccles_service import sources

@pytest.fixture(autouse=True)
def isolate_new_live_homepage(monkeypatch):
    from eccles_service import msci_web
    monkeypatch.setattr(msci_web,'extend',lambda source,p,*args:(p,None))

SOURCE={'id':'msci_us','provider':'msci','symbol':'984000','currency':'USD','variant':'STRD'}
DATA={'schema':'msci_homepage_v1','as_of':'2026-11-19','currency':'USD','variant':'STRD',
      'observed_at':'2026-11-20T00:59:00+00:00','capture_method':'verified_browser_display',
      'rows':[{'index_code':'984000','level':6830.81,'precision_decimals':2,
               'url':'https://www.msci.com/indexes?index-variant=strd&index-keyword=984000'}]}

def primary():
    return pd.Series([6986.71598018989,6971.922770590507],index=pd.to_datetime(['2026-11-17','2026-11-18']))

def write(tmp,obj):
    (tmp/'2026-11-19.json').write_text(json.dumps(obj))

def test_same_index_extension_preserves_history(tmp_path):
    write(tmp_path,DATA);before=primary();out,info=msci_homepage_extension(SOURCE,before,'2026-11-19',tmp_path)
    pd.testing.assert_series_equal(out.loc[before.index],before)
    assert out.loc['2026-11-19']==6830.81 and len(out)==3
    assert info['record']['automatic_browser_collection'] is False
    assert info['record']['dates']==['2026-11-19']

@pytest.mark.parametrize('key,value',[('schema','unknown'),('as_of','2026-11-18'),('currency','EUR'),('variant','GRTR'),('observed_at','2026-11-20T00:59:00'),('observed_at','2026-11-18T00:00:00+00:00'),('capture_method','estimated')])
def test_wrong_capture_rejected(tmp_path,key,value):
    obj=copy.deepcopy(DATA);obj[key]=value;write(tmp_path,obj)
    with pytest.raises(SourceError):msci_homepage_extension(SOURCE,primary(),'2026-11-19',tmp_path)

@pytest.mark.parametrize('value',[-1,0,float('nan'),float('inf'),True,'6830.81'])
def test_invalid_price_rejected(tmp_path,value):
    obj=copy.deepcopy(DATA);obj['rows'][0]['level']=value;write(tmp_path,obj)
    with pytest.raises(SourceError):msci_homepage_extension(SOURCE,primary(),'2026-11-19',tmp_path)

def test_wrong_code_never_substituted(tmp_path):
    obj=copy.deepcopy(DATA);obj['rows'][0]['index_code']='891800';write(tmp_path,obj)
    out,info=msci_homepage_extension(SOURCE,primary(),'2026-11-19',tmp_path)
    pd.testing.assert_series_equal(out,primary());assert info is None

def test_duplicate_rejected(tmp_path):
    obj=copy.deepcopy(DATA);obj['rows']*=2;write(tmp_path,obj)
    with pytest.raises(SourceError):msci_homepage_extension(SOURCE,primary(),'2026-11-19',tmp_path)

def test_wrong_origin_rejected(tmp_path):
    obj=copy.deepcopy(DATA);obj['rows'][0]['url']='https://example.com/indexes';write(tmp_path,obj)
    with pytest.raises(SourceError):msci_homepage_extension(SOURCE,primary(),'2026-11-19',tmp_path)

def test_challenge_body_rejected(tmp_path):
    (tmp_path/'2026-11-19.json').write_text('<title>Challenge Validation</title>')
    with pytest.raises(SourceError):msci_homepage_extension(SOURCE,primary(),'2026-11-19',tmp_path)

def test_previous_day_not_reused(tmp_path):
    write(tmp_path,DATA);out,info=msci_homepage_extension(SOURCE,primary(),'2026-11-20',tmp_path)
    pd.testing.assert_series_equal(out,primary());assert info is None

def test_primary_current_is_authoritative(tmp_path):
    write(tmp_path,DATA);p=primary();p.loc[pd.Timestamp('2026-11-19')]=6830.812345
    out,info=msci_homepage_extension(SOURCE,p,'2026-11-19',tmp_path)
    pd.testing.assert_series_equal(out,p);assert info is None

def test_acquire_records_homepage_provenance(tmp_path,monkeypatch):
    cache=tmp_path/'msci-homepage';cache.mkdir();write(cache,DATA)
    monkeypatch.setenv('ECCLES_RUNTIME',str(tmp_path))
    from eccles_service import msci_download
    monkeypatch.setattr(msci_download,'extend',lambda source,p,*args:(p,None))
    monkeypatch.setattr(sources,'acquire_one',lambda *args:(primary(),b'original-history','https://app2.msci.com/original'))
    prices,manifest=sources.acquire({'sources':[SOURCE]},'2026-11-17','2026-11-19',tmp_path/'snapshot')
    assert not manifest['errors'] and prices['msci_us'].loc['2026-11-19']==6830.81
    record=manifest['records'][0]
    assert record['last']=='2026-11-19' and record['fallback']['symbol']=='984000'
    assert (tmp_path/'snapshot/msci_us.raw').read_bytes()==b'original-history'
    assert json.loads((tmp_path/'snapshot/msci_us.fallback.raw').read_text())==DATA

def test_bad_capture_keeps_original_and_reports_error(tmp_path,monkeypatch):
    cache=tmp_path/'msci-homepage';cache.mkdir();obj=copy.deepcopy(DATA);obj['variant']='GRTR';write(cache,obj)
    monkeypatch.setenv('ECCLES_RUNTIME',str(tmp_path))
    from eccles_service import msci_download
    monkeypatch.setattr(msci_download,'extend',lambda source,p,*args:(p,None))
    monkeypatch.setattr(sources,'acquire_one',lambda *args:(primary(),b'original-history','https://app2.msci.com/original'))
    prices,manifest=sources.acquire({'sources':[SOURCE]},'2026-11-17','2026-11-19',tmp_path/'snapshot')
    pd.testing.assert_series_equal(prices['msci_us'],primary())
    assert 'error' in manifest['records'][0]['fallback']
