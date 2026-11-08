import os
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['DATA_MODE'] = 'demo'
import pytest
from datetime import timedelta
from sqlalchemy import create_engine, select, func
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app import core, main, worker

@pytest.fixture(autouse=True)
def database(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    core.Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    for module in (core, main, worker):
        monkeypatch.setattr(module, 'Session', factory)
    yield factory
    engine.dispose()

@pytest.mark.parametrize('metric,value,expected', [('pm25',0,0),('pm25',55,0),('pm25',56,1),('pm25',150,1),('pm25',151,2),('pm25',250,2),('pm25',251,3),('psi',50,0),('psi',51,1),('psi',100,1),('psi',101,2),('psi',200,2),('psi',201,3),('psi',300,3),('psi',301,4)])
def test_boundaries(metric,value,expected):
    assert core.level(metric,value)==expected

def sub(db):
    s=core.Subscription(email='a@example.com',region='central',metric='pm25',threshold=56,token_hash='hash',created_at=core.iso(core.now()),verified=True)
    db.add(s);db.flush();return s

def insert(db,value,minutes):
    core.save_reading(db,'central','pm25',value,core.iso(core.now().replace(second=0,microsecond=0)+timedelta(minutes=minutes)))

def kinds(db):
    return list(db.scalars(select(core.Event.kind)))

def test_warning_escalation_recovery_and_duplicate(database):
    with database() as db:
        sub(db)
        for val,minute in [(60,-90),(60,-90),(80,-80),(170,-70),(30,-60),(30,-60)]:insert(db,val,minute)
        assert kinds(db)==['warning','escalation']
        insert(db,35,-50)
        assert kinds(db)==['warning','escalation','recovery']
        assert db.scalar(select(func.count()).select_from(core.Mail))==3

def test_stale_and_out_of_order_cannot_clear_warning(database):
    with database() as db:
        sub(db);insert(db,75,-10);insert(db,5,-200);insert(db,5,-20)
        assert kinds(db)==['warning']

def test_new_warning_after_recovery(database):
    with database() as db:
        sub(db)
        for val,minute in [(60,-60),(10,-50),(10,-40),(60,-30)]:insert(db,val,minute)
        assert kinds(db)==['warning','recovery','warning']

@pytest.mark.parametrize('value',[-1,float('nan'),float('inf'),None,True,'56'])
def test_invalid_readings_rejected(database,value):
    with database() as db,pytest.raises(ValueError):
        core.save_reading(db,'north','pm25',value,core.iso(core.now()))

def test_backfill_suppresses_alerts(database):
    with database() as db:
        sub(db)
        core.save_reading(db,'central','pm25',200,core.iso(core.now()),False)
        assert kinds(db)==[]

def test_decode_missing_regions_and_bad_schema():
    payload={'code':0,'data':{'items':[{'timestamp':'2026-01-01T08:00:00+08:00','readings':{'pm25_one_hourly':{'north':45}}}]}}
    assert len(core.decode(payload,'pm25'))==1
    with pytest.raises(ValueError):core.decode({'data':{}},'pm25')

def test_subscription_verify_export_unsubscribe(database):
    client=TestClient(main.app)
    with database() as db:insert(db,80,-1);db.commit()
    response=client.post('/api/v1/subscriptions',json={'email':'test@example.com','region':'central'})
    assert response.status_code==202
    with database() as db:
        mail=db.scalar(select(core.Mail))
        token=mail.body.split('#token=')[1].split('\n')[0]
    assert client.post('/api/v1/subscriptions/history',json={'token':'x'*32}).status_code==404
    assert client.post('/api/v1/subscriptions/verify',json={'token':token}).status_code==200
    assert len(client.post('/api/v1/subscriptions/history',json={'token':token}).json()['events'])==1
    assert client.get('/api/v1/readings/export').text.startswith('mode,region,metric,value,observed_at_utc')
    assert client.post('/api/v1/subscriptions/unsubscribe',json={'token':token}).status_code==200
    assert client.post('/api/v1/subscriptions/history',json={'token':token}).status_code==404

def test_api_validation_and_empty_history():
    client=TestClient(main.app)
    assert client.get('/api/v1/readings/history?region=unknown').status_code==422
    assert client.get('/api/v1/readings/history?hours=9999').status_code==422
    assert client.get('/api/v1/readings/history?end=nope').status_code==422
    assert client.get('/api/v1/readings/history').json()['summary']['peak'] is None
    assert client.post('/api/v1/subscriptions',json={'email':'invalid','region':'north'}).status_code==422

def test_outbox_retry(database,monkeypatch):
    with database() as db:
        db.add(core.Mail(subscription_id=1,recipient='test@example.com',subject='test',body='test',next_at=core.iso(core.now())))
        db.commit()
    def broken(*a,**kw):raise OSError('offline')
    monkeypatch.setattr(worker.smtplib,'SMTP',broken)
    worker.deliver()
    with database() as db:
        row=db.scalar(select(core.Mail))
        assert row.status=='pending' and row.attempts==1
        assert core.parse(row.next_at)>core.now()

def test_transaction_rolls_back_observation_and_warning(database):
    with database() as db:
        sub(db);db.commit();insert(db,70,-1);db.rollback()
        assert db.scalar(select(func.count()).select_from(core.Reading))==0
        assert kinds(db)==[]

def test_source_pagination_and_retry(database,monkeypatch):
    import httpx
    stamp=core.iso(core.now())
    seen=[]
    class FakeClient:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,params):
            seen.append(dict(params))
            request=httpx.Request('GET',url)
            if len(seen)==1:
                return httpx.Response(429,headers={'Retry-After':'0'},request=request)
            region='south' if params.get('paginationToken') else 'north'
            data={'items':[{'timestamp':stamp,'readings':{'pm25_one_hourly':{region:40}}}]}
            if region=='north':data['paginationToken']='next-page'
            return httpx.Response(200,json={'code':0,'data':data},request=request)
    monkeypatch.setattr(core.httpx,'Client',FakeClient)
    monkeypatch.setattr(core.time,'sleep',lambda _:None)
    with database() as db:
        assert core.ingest_live(db,'pm25')==2
        assert seen==[{}, {}, {'paginationToken':'next-page'}]

def test_email_delivery_marks_sent(database,monkeypatch):
    sent=[]
    class SMTP:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def send_message(self,message):sent.append(message)
    monkeypatch.setattr(worker.smtplib,'SMTP',SMTP)
    with database() as db:
        db.add(core.Mail(subscription_id=1,recipient='a@example.com',subject='test',body='secret',next_at=core.iso(core.now())))
        db.commit()
    worker.deliver()
    with database() as db:
        row=db.scalar(select(core.Mail))
        assert row.status=='sent' and row.body=='[delivered]'
    assert len(sent)==1 and sent[0]['Message-ID']

def test_expired_verification(database):
    with database() as db:
        db.add(core.Subscription(email='a@example.com',region='central',metric='pm25',threshold=56,
             token_hash=core.digest('x'*32),created_at=core.iso(core.now()-timedelta(days=2)),verified=False))
        db.commit()
    assert TestClient(main.app).post('/api/v1/subscriptions/verify',json={'token':'x'*32}).status_code==410
