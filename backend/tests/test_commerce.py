"""Offline commerce integration checks. No provider calls or real messages."""
import sys
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main
from conftest import authenticated_client

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB',tmp_path/'commerce.sqlite3');monkeypatch.setattr(main,'STEPS_SECONDS',1)
    async def forbidden(*args,**kwargs):raise AssertionError('Commerce extensions must never call a model')
    monkeypatch.setattr(main.ai_agent,'run_agent',forbidden);main.initialize();return authenticated_client(main)

def demo(c,scenario='stockout',category='groceries'):
    r=c.post('/api/rescue/demo',json={'scenario':scenario,'category':category});assert r.status_code==200;return r.json()['checkout_id']

def tick(n=1):
    for _ in range(n):
        with main.connection() as db:
            db.execute('UPDATE rescue_payments SET paid_at=0');db.execute('UPDATE cases SET due=0 WHERE due IS NOT NULL');db.execute('UPDATE communication_outbox SET due=0 WHERE due IS NOT NULL');main.advance_db(db,now=time.time(),step=1)

def entry(c,co):return next(e for e in c.get('/api/rescue').json()['entries'] if e['checkout']['id']==co)
def scope(c,co):return c.get('/api/commerce/'+co).json()
def choose(c,co,consent=True,pid=None):
    p=pid or next(p['id'] for p in scope(c,co)['alternatives'] if p['eligible'])
    return c.post('/api/commerce/'+co+'/alternative',json={'product_id':p,'consent':consent})
def quote(c,co,**kwargs):
    r=c.post('/api/commerce/'+co+'/delivery-quotes',json=kwargs);assert r.status_code==200;return r.json()['options']
def plan(c,co,q):return c.post('/api/commerce/'+co+'/delivery-plan',json={'quote_id':q['quote_id']})

@pytest.mark.parametrize('category,tag',[('groceries','vegetarian'),('electronics','usb-c'),('fashion','size-m')])
def test_category_catalog_constraints_and_consent(client,category,tag):
    co=demo(client,category=category);tick(3);s=scope(client,co)
    assert s['category']==category and s['context']['compatibility']==tag
    assert sum(p['eligible'] for p in s['alternatives'])==1
    for p in s['alternatives']:
        if not p['eligible']:assert choose(client,co,pid=p['id']).status_code==409
    assert choose(client,co,False).status_code==409
    before=entry(client,co)['checkout']['amount'];assert choose(client,co).status_code==200;tick(8)
    e=entry(client,co);assert e['case']['state']=='ORDER_RESTORED'
    assert e['merchant_order']['amount']==before and e['case']['rescue']['reservation']['sku']==e['checkout']['sku']
    assert choose(client,co,pid=co+'-matched').status_code==409

def test_substitution_releases_old_reservation_and_refund_returns_correct_sku(client):
    co=demo(client,'interruption');tick(4);old=entry(client,co)['checkout']['sku']
    assert entry(client,co)['case']['state']=='RESCUE_PAUSED'
    assert choose(client,co).status_code==200;tick(2)
    e=entry(client,co);new=e['checkout']['sku'];cid=e['case']['id']
    with main.connection() as db:
        assert db.execute('SELECT available FROM rescue_inventory WHERE sku=?',(old,)).fetchone()[0]==5
        assert db.execute('SELECT available FROM rescue_inventory WHERE sku=?',(new,)).fetchone()[0]==2
    assert client.post(f'/api/rescue/cases/{cid}/preference',json={'intent':'refund'}).status_code==200
    tick(10)
    with main.connection() as db:
        assert db.execute('SELECT available FROM rescue_inventory WHERE sku=?',(old,)).fetchone()[0]==5
        assert db.execute('SELECT available FROM rescue_inventory WHERE sku=?',(new,)).fetchone()[0]==3
    assert entry(client,co)['case']['state']=='RESOLVED'

@pytest.mark.parametrize('scenario',['approval','status','ambiguous','unknown'])
def test_mutations_respect_selected_outcome_and_uncertain_creation(client,scenario):
    co=demo(client,scenario);tick(9);assert choose(client,co).status_code==409

def test_delivery_objectives_and_hard_constraints(client):
    co=demo(client);tick(3)
    assert quote(client,co,objective='cheapest')[0]['id']=='standard'
    assert quote(client,co,objective='fastest')[0]['id']=='express'
    assert quote(client,co,objective='balanced')[0]['id']=='green'
    assert not any(p['eligible'] for p in quote(client,co,distance_km=50))
    assert not any(p['eligible'] for p in quote(client,co,deadline_minutes=30))
    assert [p['id'] for p in quote(client,co,budget_paise=0) if p['eligible']]==['standard']
    assert client.post('/api/commerce/'+co+'/delivery-quotes',json={'distance_km':-1}).status_code==422

def test_delivery_quotes_cannot_cross_checkout_product_or_expiry(client):
    co=demo(client);other=demo(client);tick(3);q=quote(client,co)[0]
    assert plan(client,other,q).status_code==409
    with main.connection() as db:db.execute('UPDATE delivery_quotes SET expires=0 WHERE id=?',(q['quote_id'],))
    assert plan(client,co,q).status_code==409
    q=quote(client,co)[0];assert choose(client,co).status_code==200
    assert plan(client,co,q).status_code==409

def test_delivery_rechecks_capacity_at_commit_and_releases_stock(client):
    co=demo(client,'interruption');tick(4);q=quote(client,co,objective='fastest')[0]
    assert plan(client,co,q).status_code==200
    with main.connection() as db:db.execute('UPDATE delivery_lanes SET available=0 WHERE id=?',(q['id'],))
    tick(6);e=entry(client,co)
    assert e['case']['state']=='WAITING_CUSTOMER' and e['merchant_order'] is None
    assert e['case']['rescue']['inventory']['available']==5
    assert scope(client,co)['delivery_plan']['status']=='CANCELLED'

def test_timeout_and_explicit_cancellation_conserve_carrier_capacity(client):
    co=demo(client,'timeout');tick(9);s=scope(client,co);assert s['delivery_plan']['status']=='COMMITTED'
    with main.connection() as db:
        lane=db.execute('SELECT * FROM delivery_lanes WHERE id=?',('green',)).fetchone();available=lane['available'];assert available==lane['capacity']-1
    tick(6)
    with main.connection() as db:assert db.execute("SELECT available FROM delivery_lanes WHERE id='green'").fetchone()[0]==available
    cid=entry(client,co)['case']['id'];client.post(f'/api/rescue/cases/{cid}/preference',json={'intent':'refund'})
    assert client.post(f'/api/rescue/cases/{cid}/decision',json={'decision':'cancel_and_refund'}).status_code==200;tick(9)
    with main.connection() as db:
        lane=db.execute("SELECT * FROM delivery_lanes WHERE id='green'").fetchone();assert lane['available']==lane['capacity']
    assert scope(client,co)['delivery_plan']['status']=='CANCELLED'

def test_channel_optin_cancellation_and_retry_limits(client):
    co=demo(client);tick(3);base='/api/commerce/'+co
    assert client.post(base+'/channels',json={'channel':'sms','enabled':True}).status_code==409
    assert client.post(base+'/test-message',json={'channel':'sms'}).status_code==409
    assert client.post(base+'/channels',json={'channel':'sms','enabled':True,'consent':True}).status_code==200
    client.post(base+'/test-message',json={'channel':'sms','failure':'once'});tick(1)
    message=next(m for m in scope(client,co)['messages'] if m['phase_key'].startswith('TEST'))
    assert message['status']=='RETRY_SCHEDULED' and message['attempts']==1
    tick(1);m=next(m for m in scope(client,co)['messages'] if m['id']==message['id']);assert m['status']=='SIMULATED_DELIVERED' and m['attempts']==2
    client.post(base+'/test-message',json={'channel':'sms','failure':'always'});tick(5)
    assert any(m['status']=='FAILED' and m['attempts']==3 for m in scope(client,co)['messages'])
    client.post(base+'/test-message',json={'channel':'sms','failure':'none'})
    client.post(base+'/channels',json={'channel':'sms','enabled':False});tick(2)
    tests=[m for m in scope(client,co)['messages'] if m['phase_key'].startswith('TEST')]
    assert sum(m['status']=='CANCELLED' for m in tests)==1
    with main.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM (SELECT case_id,channel,phase_key FROM communication_outbox GROUP BY case_id,channel,phase_key HAVING COUNT(*)>1)').fetchone()[0]==0

def test_analytics_filters_unique_outcomes_and_excludes_scorecards(client):
    co=demo(client,'restore','electronics');fashion=demo(client,'stockout','fashion');tick(9)
    d=client.get('/api/commerce/analytics?category=electronics&days=7').json()
    assert d['totals']['checkouts']==1 and d['totals']['restored_orders']==1
    assert d['totals']['restored_value_paise']==entry(client,co)['checkout']['amount']
    assert d['categories'][0]['category']=='electronics' and d['totals']['model_tokens']==0
    before=client.get('/api/commerce/analytics?days=0').json()['totals']
    report=client.post('/api/rescue-scorecards').json();assert report['metrics']['correct_outcomes']==26
    assert client.get('/api/commerce/analytics?days=0').json()['totals']==before
    with main.connection() as db:db.execute('UPDATE rescue_checkouts SET created=? WHERE id=?',(time.time()-10*86400,fashion))
    assert client.get('/api/commerce/analytics?days=7').json()['totals']['checkouts']==1
    assert client.get('/api/commerce/analytics?days=30').json()['totals']['checkouts']==2
    assert client.get('/api/commerce/analytics?category=invalid').status_code==422
