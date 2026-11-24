import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pandas as pd
import pytest

from eccles_service.api import create_app
from eccles_service.model import digest
from eccles_service.storage import Store
from test_release import KEY, sample, setup

script = Path(__file__).resolve().parents[1] / 'scripts/backfill_history.py'
spec = importlib.util.spec_from_file_location('backfill_history', script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
backfill = module.backfill


def save_manifest(root, manifest):
    for name in manifest['files']:
        manifest['files'][name] = digest(root/name)
    (root/'manifest.json').write_text(json.dumps(manifest))
    return digest(root/'manifest.json')


@pytest.fixture
def archive(setup, tmp_path):
    b = setup[2]
    root = tmp_path/'archive'; root.mkdir()
    golden = json.loads((b.path/'golden.json').read_text())
    inputs = pd.DataFrame(golden['inputs'][:3], index=pd.to_datetime(['2001-01-02','2022-12-30','2023-01-03']), columns=b.names)
    pred = b.infer(inputs)
    expected = pd.DataFrame({'state':pred['state'], **{f'distance_C{k}':pred['distances'][:,k] for k in range(3)}},index=inputs.index)
    expected = pd.concat([expected,pd.DataFrame(pred['relevance'], index=inputs.index,columns=b.names)],axis=1)
    for name, frame in [('features.csv',inputs),('raw-features.csv',inputs),('expected.csv',expected)]:
        frame.to_csv(root/name,index_label='date')
    manifest = dict(schema_version=1,model_version=b.version,model_sha256=b.hash,origin='historical_replay',
                    periods=[dict(name='training',start='2001-01-01',end='2022-12-31',count=2),
                             dict(name='validation',start='2023-01-01',end='2024-12-31',count=1)],
                    files={name:'' for name in ['features.csv','raw-features.csv','expected.csv']})
    return root,manifest,save_manifest(root,manifest)


def test_history_serves_same_fields_preserves_live_and_is_idempotent(setup, archive):
    models,runtime,b=setup; root,_,sha=archive
    store=Store(runtime); live=sample(b);store.publish(live)
    report=backfill(models,runtime,root,sha)
    assert report['inserted']==3 and report['fits']==0
    auth={'Authorization':'Bearer '+KEY}
    client=TestClient(create_app(models,runtime,KEY))
    for day,period in [('2001-01-02','training'),('2023-01-03','validation')]:
        path='/v1/regimes/'+day
        assert client.get(path).status_code==401
        response=client.get(path,headers=auth)
        assert response.status_code==200
        item=response.json()
        assert set(live)<=set(item)
        assert item['sample_period']==period and item['in_sample']==(period=='training')
        assert item['origin']=='historical_replay' and item['observed_at'] is None
        assert item['historical_vintage_verified'] is False
        assert len(item['neon']['features'])==26
        assert all(f['raw_value'] is not None for f in item['neon']['features'])
        assert item['neon']['relevance_sum']==pytest.approx(item['winning_margin'])
    history=client.get('/v1/regimes?start=2001-01-01&end=2022-12-31&limit=1',headers=auth).json()
    assert [x['as_of'] for x in history['items']]==['2022-12-30']
    history=client.get('/v1/regimes?start=2001-01-01&end=2024-12-31',headers=auth).json()
    assert [x['as_of'] for x in history['items']]==['2023-01-03','2022-12-30','2001-01-02']
    before=store.get(b.version,'2001-01-02')
    again=backfill(models,runtime,root,sha)
    assert again['inserted']==0 and again['already_present']==3
    assert store.get(b.version,'2001-01-02')==before
    assert store.latest(b.version)==live and store.last_attempt(b.version) is None
    assert client.get('/v1/regimes/2025-01-02',headers=auth).status_code==404


@pytest.mark.parametrize('change',['manifest','file','model','distance','relevance','date_alignment','duplicate','warmup','count'])
def test_invalid_archive_never_publishes(setup,archive,change):
    models,runtime,b=setup; root,manifest,sha=archive
    if change=='manifest': sha='0'*64
    elif change=='file': (root/'features.csv').write_text('changed')
    elif change=='model':
        manifest['model_sha256']='0'*64;sha=save_manifest(root,manifest)
    elif change in {'distance','relevance'}:
        f=pd.read_csv(root/'expected.csv',index_col=0)
        f.iloc[0,1 if change=='distance' else 4]+=1
        f.to_csv(root/'expected.csv');sha=save_manifest(root,manifest)
    elif change=='date_alignment':
        f=pd.read_csv(root/'raw-features.csv',index_col=0);f.index=['2001-01-03','2022-12-30','2023-01-03']
        f.to_csv(root/'raw-features.csv');sha=save_manifest(root,manifest)
    elif change=='duplicate':
        f=pd.read_csv(root/'features.csv',index_col=0);f.index=['2001-01-02','2001-01-02','2023-01-03']
        f.to_csv(root/'features.csv');sha=save_manifest(root,manifest)
    elif change=='warmup':
        for name in manifest['files']:
            f=pd.read_csv(root/name,index_col=0);f.index=['2001-01-02','2022-12-30','2025-01-03'];f.to_csv(root/name)
        manifest['periods'][1].update(start='2025-01-01',end='2025-12-31');sha=save_manifest(root,manifest)
    else:
        manifest['periods'][0]['count']=3;sha=save_manifest(root,manifest)
    with pytest.raises(ValueError):backfill(models,runtime,root,sha)
    assert Store(runtime).latest(b.version) is None


def test_conflict_rolls_back_earlier_rows_and_preserves_published_record(setup,archive):
    models,runtime,b=setup;root,_,sha=archive
    store=Store(runtime);prior=sample(b,'2022-12-30');store.publish(prior)
    with pytest.raises(ValueError,match='conflict'):backfill(models,runtime,root,sha)
    assert store.get(b.version,'2001-01-02') is None
    assert store.get(b.version,'2022-12-30')==prior
    assert store.get(b.version,'2023-01-03') is None
