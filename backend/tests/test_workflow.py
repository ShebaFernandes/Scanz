from conftest import authenticated_client
import sys
import asyncio
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main

@pytest.fixture
def client(tmp_path, monkeypatch):
    async def idle_worker():
        await asyncio.Event().wait()
    monkeypatch.setattr(main,'worker',idle_worker)
    monkeypatch.setenv('RESOLVE_AGENT_MODE','deterministic')
    monkeypatch.setattr(main,'DB',tmp_path/'test.sqlite3')
    monkeypatch.setattr(main,'STEPS_SECONDS',0)
    main.initialize()
    with authenticated_client(main) as client:
        yield client

def create(client,order='ORD-1042'):
    response=client.post('/api/cases',json={'order_id':order,'complaint':'Money deducted but order failed'})
    assert response.status_code==200
    return response.json()

def finish(client,cid):
    for _ in range(12): main.advance_once()
    return client.get('/api/cases/'+cid).json()

def test_refund_lifecycle_and_idempotency(client):
    c=create(client)
    duplicate=create(client)
    assert duplicate['id']==c['id']
    result=finish(client,c['id'])
    assert result['state']=='RESOLVED'
    assert result['refund']['amount']==79900
    assert result['refund']['status']=='CREDITED'
    assert create(client)['id']==c['id']
    with main.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM refunds').fetchone()[0]==1
    main.initialize() # startup seeding must not overwrite durable state
    assert client.get('/api/cases/'+c['id']).json()['state']=='RESOLVED'

def test_approval_resumes_workflow(client):
    c=create(client,'ORD-1043')
    result=finish(client,c['id'])
    assert result['state']=='NEEDS_HUMAN'
    assert result['refund'] is None
    response=client.post('/api/cases/'+c['id']+'/review',json={'decision':'approve'})
    assert response.status_code==200
    assert finish(client,c['id'])['state']=='RESOLVED'
    assert client.post('/api/cases/'+c['id']+'/review',json={'decision':'approve'}).status_code==409

@pytest.mark.parametrize('order',['ORD-1044','ORD-1045','ORD-1046'])
def test_ineligible_orders_never_refund(client,order):
    c=create(client,order)
    result=finish(client,c['id'])
    assert result['state']==('WAITING_PAYMENT' if order=='ORD-1044' else 'NEEDS_HUMAN')
    assert result['refund'] is None
    assert client.post('/api/cases/'+c['id']+'/review',json={'decision':'approve'}).status_code==409

def test_ownership_and_validation(client):
    assert client.post('/api/cases',json={'order_id':'ORD-PRIVATE','complaint':'Refund me'}).status_code==404
    data=client.post('/api/card-candidates',json={'last4':'4821'}).json()
    assert len(data['orders'])==5
    assert all(o['id']!='ORD-PRIVATE' for o in data['orders'])
    assert client.post('/api/card-candidates',json={'last4':'1234567812345678'}).status_code==422
    assert client.post('/api/card-candidates',json={'last4':'0000'}).json()['orders']==[]

def test_rejection_closes_without_refund(client):
    c=create(client,'ORD-1043')
    finish(client,c['id'])
    response=client.post('/api/cases/'+c['id']+'/review',json={'decision':'reject'})
    assert response.json()['state']=='CLOSED_REVIEWED'
    assert finish(client,c['id'])['refund'] is None

def test_policy_revalidated_before_execution(client):
    c=create(client)
    # Advance to the moment before execution, then change fulfillment.
    for _ in range(3): main.advance_once()
    with main.connection() as db:
        db.execute("UPDATE orders SET fulfillment='FULFILLED' WHERE id='ORD-1042'")
    result=finish(client,c['id'])
    assert result['state']=='NEEDS_HUMAN'
    assert result['refund'] is None

def test_physical_card_demo_fixture_is_explicit_and_idempotent(client):
    response=client.post('/api/demo-orders',json={'last4':'9876'})
    assert response.status_code==200
    assert response.json()['simulated'] is True
    assert response.json()['order']['description'].startswith('SIMULATED')
    duplicate=client.post('/api/demo-orders',json={'last4':'9876'}).json()
    assert duplicate['order']['id']==response.json()['order']['id']
    c=create(client,duplicate['order']['id'])
    assert finish(client,c['id'])['state']=='RESOLVED'
    assert client.post('/api/demo-orders',json={'last4':'4111111111111111'}).status_code==422
