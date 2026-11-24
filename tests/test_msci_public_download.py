from pathlib import Path
import copy,json,sys,os
SERVICE=Path(__file__).resolve().parents[1]
REPLAYS=Path(os.environ.get('ECCLES_REPLAY_ROOT', str(SERVICE.parent/'runs')))
R=REPLAYS/'2026-11-20-before09-original-feed'
sys.path.insert(0,str(R/'xlrd-2.0.2-py2.py3-none-any.whl'))
import numpy as np
import pandas as pd
import pytest
import xlrd
from eccles_service import msci_download as md,sources

@pytest.fixture(autouse=True)
def isolate_new_live_homepage(monkeypatch):
 from eccles_service import msci_web
 monkeypatch.setattr(msci_web,'extend',lambda source,p,*args:(p,None))

if not (R/'input/validation-download.xls').is_file():
 pytest.skip('Set ECCLES_REPLAY_ROOT for private recorded-source checks', allow_module_level=True)
BLOB=(R/'input/validation-download.xls').read_bytes()
DATA=md.parse_download(BLOB,list(md.NAMES),'2026-08-03','2026-11-19')
SPEC=json.loads((SERVICE/'models/1.0/model.json').read_text())
SOURCE=next(x for x in SPEC['sources'] if x['id']=='msci_us')

class Sheet:
 def __init__(self):
  book=xlrd.open_workbook(file_contents=BLOB);s=book.sheet_by_index(0)
  self.rows=[s.row_values(i) for i in range(s.nrows)];self.ncols=s.ncols;self.nrows=s.nrows
 def cell_value(self,i,j):return self.rows[i][j]
 def row_values(self,i):return self.rows[i]

def test_all_936_common_observations_and_79_dates():
 d=json.loads((R/'validation-download.json').read_text())
 assert all(len(x)==79 for x in DATA.values())
 for j,source in enumerate(d['sources']):
  for row in d['rows']:assert DATA[source['symbol']].loc[row['date']]==row['values'][j]
 assert sum(x['common_dates'] for x in json.loads((R/'parity.json').read_text()))==936

@pytest.mark.parametrize('row,col,value',[(2,1,'Gross'),(3,1,'EUR'),(6,1,'MSCI World'),(7,0,'Nov 20, 2026'),(7,0,'Aug 4, 2026'),(7,1,'-1.000'),(7,1,'NaN'),(7,1,7000.0),(7,1,'7,000.00')])
def test_reject_wrong_identity_or_values(row,col,value):
 s=Sheet();s.rows[row][col]=value
 with pytest.raises(ValueError):md.parse_sheet(s,list(md.NAMES),'2026-08-03','2026-11-19')

def test_reordered_columns_rejected():
 s=Sheet();s.rows[6][1],s.rows[6][2]=s.rows[6][2],s.rows[6][1]
 with pytest.raises(ValueError):md.parse_sheet(s,list(md.NAMES),'2026-08-03','2026-11-19')

@pytest.mark.parametrize('blob',[b'<title>reCAPTCHA</title>',b'{}',b''])
def test_challenge_and_non_xls_rejected(blob):
 with pytest.raises(ValueError):md.parse_download(blob,list(md.NAMES),'2026-08-03','2026-11-19')

def cache():return dict(data=DATA,blob=BLOB,target='2026-11-19',observed_at='2026-11-20T01:40:00+00:00')
def primary():return DATA['984000'].loc[:'2026-11-18']+0.0001

def test_extend_only_target_preserves_precise_history():
 p=primary();out,info=md.extend(SOURCE,p,'2026-11-19',cache(),None)
 pd.testing.assert_series_equal(out.loc[p.index],p)
 assert out.loc['2026-11-19']==6830.807 and len(out)==len(p)+1
 assert info['record']['automatic_collection'] and info['record']['overlap_count']==78

def test_no_interpolation_of_preceding_gap():
 p=primary().loc[:'2026-11-16'];out,_=md.extend(SOURCE,p,'2026-11-19',cache(),None)
 assert pd.Timestamp('2026-11-17') not in out.index and pd.Timestamp('2026-11-18') not in out.index

def test_current_original_prevents_download():
 p=DATA['984000']+0.0001;out,info=md.extend(SOURCE,p,'2026-11-19',{},lambda *a:pytest.fail('Unexpected network'))
 pd.testing.assert_series_equal(out,p);assert info is None

def test_stale_download_is_not_shifted():
 c=cache();c['data']={k:v.loc[:'2026-11-18'] for k,v in DATA.items()}
 out,info=md.extend(SOURCE,primary(),'2026-11-19',c,None)
 pd.testing.assert_series_equal(out,primary());assert info is None

@pytest.mark.parametrize('p',[lambda:primary()+0.001,lambda:primary().tail(2)])
def test_disagreement_or_insufficient_overlap_rejected(p):
 with pytest.raises(ValueError):md.extend(SOURCE,p(),'2026-11-19',cache(),None)

def test_fetch_once_and_audit_all_twelve(monkeypatch,tmp_path):
 requests=[]
 short={k:v.loc['2026-11-05':] for k,v in DATA.items()}
 monkeypatch.setattr(md,'parse_download',lambda *a:short)
 monkeypatch.setattr(sources,'fetch',lambda *a:(requests.append(a) or BLOB))
 monkeypatch.setattr(sources,'acquire_one',lambda s,*a:(short[s['symbol']].loc[:'2026-11-18'],b'original','original'))
 spec={'sources':[x for x in SPEC['sources'] if x['provider']=='msci']}
 prices,m=sources.acquire(spec,'2026-11-05','2026-11-19',tmp_path/'capture')
 assert len(requests)==1 and len(m['records'])==12 and not m['errors']
 assert all(x['fallback']['provider']=='msci_public_download' for x in m['records'])
 assert all(m['msci_public_download']['target_present'].values())
 assert (tmp_path/'capture/msci-public-download.raw').read_bytes()==BLOB

def test_download_failure_cached_and_original_preserved(monkeypatch,tmp_path):
 requests=[]
 def fail(*a):requests.append(a);raise sources.SourceError('HTTP source failed: HTTPError, status=503')
 monkeypatch.setattr(sources,'fetch',fail)
 monkeypatch.setattr(sources,'msci_homepage_extension',lambda s,p,*a:(p,None))
 monkeypatch.setattr(sources,'acquire_one',lambda s,*a:(DATA[s['symbol']].loc[:'2026-11-18'],b'original','original'))
 spec={'sources':[x for x in SPEC['sources'] if x['provider']=='msci']}
 prices,m=sources.acquire(spec,'2026-08-03','2026-11-19',tmp_path/'capture')
 assert len(requests)==1 and not m['errors']
 assert all(pd.Timestamp('2026-11-19') not in p.index for p in prices.values())
 assert all('public_download_error' in x['fallback'] for x in m['records'])

def test_public_download_precedes_manual_capture(monkeypatch,tmp_path):
 short={k:v.loc['2026-11-05':] for k,v in DATA.items()}
 monkeypatch.setattr(md,'parse_download',lambda *a:short)
 monkeypatch.setattr(sources,'fetch',lambda *a:BLOB)
 monkeypatch.setattr(sources,'acquire_one',lambda s,*a:(short[s['symbol']].loc[:'2026-11-18'],b'original','original'))
 monkeypatch.setattr(sources,'msci_homepage_extension',lambda *a:pytest.fail('Manual capture should not override public original'))
 p,m=sources.acquire({'sources':[SOURCE]},'2026-11-05','2026-11-19',tmp_path/'capture')
 assert m['records'][0]['fallback']['provider']=='msci_public_download'

def test_failed_public_download_allows_verified_emergency_capture(monkeypatch,tmp_path):
 monkeypatch.setattr(sources,'fetch',lambda *a:b'<title>Challenge</title>')
 monkeypatch.setattr(sources,'acquire_one',lambda *a:(primary(),b'original','original'))
 fallback=dict(record=dict(provider='msci_homepage',dates=['2026-11-19']),blob=b'manual')
 p=primary();p.loc[pd.Timestamp('2026-11-19')]=6830.81
 monkeypatch.setattr(sources,'msci_homepage_extension',lambda *a:(p,fallback))
 prices,m=sources.acquire({'sources':[SOURCE]},'2026-08-03','2026-11-19',tmp_path/'capture')
 assert prices['msci_us'].loc['2026-11-19']==6830.81
 assert m['records'][0]['fallback']['provider']=='msci_homepage'
 assert 'public_download_error' in m['records'][0]['fallback']
