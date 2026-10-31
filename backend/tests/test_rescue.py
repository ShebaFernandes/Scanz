from conftest import authenticated_client
import json
import sys
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB',tmp_path/'rescue.sqlite3')
    monkeypatch.setattr(main,'STEPS_SECONDS',1)
    async def no_model(*args,**kwargs):raise AssertionError('Rescue must not call a model during offline tests')
    monkeypatch.setattr(main.ai_agent,'run_agent',no_model)
    main.initialize()
    return authenticated_client(main)

def demo(client,scenario='restore'):
    response=client.post('/api/rescue/demo',json={'scenario':scenario})
    assert response.status_code==200
    return response.json()['checkout_id']

def entry(client,coid):return next(e for e in client.get('/api/rescue').json()['entries'] if e['checkout']['id']==coid)

def tick(n=1,advance=10):
    for _ in range(n):
        with main.connection() as db:
            # Make new receipts old enough for detection; progress already scheduled work.
            db.execute('UPDATE rescue_payments SET paid_at=0')
            db.execute('UPDATE cases SET due=0 WHERE due IS NOT NULL')
            main.advance_db(db,now=time.time(),step=1)

def case(client,coid):return entry(client,coid)['case']

def test_detector_waits_for_grace_and_rechecks_original_order(client):
    co=demo(client,'grace')
    with main.connection() as db:main.advance_db(db,now=time.time(),step=1)
    assert case(client,co) is None
    assert client.post(f'/api/rescue/{co}/confirm-original').status_code==200
    tick(6)
    e=entry(client,co)
    assert e['case'] is None
    assert e['merchant_order']['status']=='CONFIRMED'

def test_proactive_restore_changes_inventory_order_and_support(client):
    co=demo(client);tick(8)
    c=case(client,co);r=c['rescue']
    assert c['state']=='ORDER_RESTORED'
    assert r['inventory']['available']==4
    assert r['reservation']['status']=='COMMITTED'
    assert r['merchant_order']['amount']==r['checkout']['amount']
    assert r['order_ref']==r['merchant_order']['id']
    assert c['refund'] is None and c['ai_tokens']==0
    assert any(e['title']=='Broken purchase detected' for e in c['events'])

def test_timeout_reconciles_one_order_without_double_stock(client):
    co=demo(client,'timeout');tick(4)
    c=case(client,co)
    assert c['state']=='RESCUE_RECONCILE'
    ref=c['rescue']['merchant_order']['id']
    assert c['rescue']['order_ref'] is None
    main.initialize()
    tick(7)
    c=case(client,co)
    assert c['state']=='ORDER_RESTORED'
    assert c['rescue']['order_ref']==ref
    assert c['rescue']['inventory']['available']==4
    assert c['rescue']['reconcile_attempts']==1
    with main.connection() as db:assert db.execute('SELECT COUNT(*) FROM merchant_orders').fetchone()[0]==1

def test_refund_switch_releases_held_stock_before_creation(client):
    co=demo(client,'interruption');tick(4)
    c=case(client,co)
    assert c['state']=='RESCUE_PAUSED'
    assert c['rescue']['reservation']['status']=='HELD'
    response=client.post(f"/api/rescue/cases/{c['id']}/preference",json={'intent':'refund'})
    assert response.status_code==200
    tick(9)
    c=case(client,co)
    assert c['state']=='RESOLVED'
    assert c['rescue']['inventory']['available']==5
    assert c['rescue']['reservation']['status']=='RELEASED'
    assert c['rescue']['merchant_order'] is None
    assert c['refund']['status']=='CREDITED'

def test_refund_after_creation_requires_explicit_cancellation(client):
    co=demo(client,'timeout');tick(4)
    c=case(client,co)
    assert client.post(f"/api/rescue/cases/{c['id']}/preference",json={'intent':'refund'}).status_code==200
    tick(2);c=case(client,co)
    assert c['state']=='NEEDS_HUMAN'
    assert c['refund'] is None
    assert client.post(f"/api/cases/{c['id']}/review",json={'decision':'approve'}).status_code==409
    assert client.post(f"/api/rescue/cases/{c['id']}/decision",json={'decision':'cancel_and_refund'}).status_code==200
    tick(8);c=case(client,co)
    assert c['state']=='RESOLVED'
    assert c['rescue']['merchant_order']['status']=='CANCELLED'
    assert c['rescue']['inventory']['available']==5
    assert c['rescue']['human_decisions']==1
    assert client.post(f"/api/rescue/cases/{c['id']}/preference",json={'intent':'restore'}).status_code==409

def test_unknown_merchant_outcome_never_releases_or_refunds(client):
    co=demo(client,'unknown');tick(10)
    c=case(client,co)
    assert c['state']=='NEEDS_HUMAN'
    assert c['rescue']['reconcile_attempts']==3
    assert c['rescue']['inventory']['available']==4
    assert c['refund'] is None
    assert client.post(f"/api/rescue/cases/{c['id']}/decision",json={'decision':'abandon'}).status_code==409
    assert client.post(f"/api/rescue/cases/{c['id']}/preference",json={'intent':'refund'}).status_code==200
    assert client.post(f"/api/rescue/cases/{c['id']}/decision",json={'decision':'cancel_and_refund'}).status_code==409
    assert client.post(f"/api/rescue/cases/{c['id']}/decision",json={'decision':'reconnect'}).status_code==200
    tick(3)
    c=case(client,co)
    assert c['state']=='NEEDS_HUMAN' # now known; the pending refund needs cancellation review
    assert c['rescue']['order_ref']
    assert c['refund'] is None

def test_creation_rejection_compensates_inventory(client):
    co=demo(client,'create_failed');tick(6)
    c=case(client,co)
    assert c['state']=='WAITING_CUSTOMER'
    assert c['rescue']['inventory']['available']==5
    assert c['rescue']['reservation']['status']=='RELEASED'
    assert c['rescue']['merchant_order'] is None

def test_human_refund_approval_resumes_and_is_counted(client):
    co=demo(client,'approval');tick(6)
    c=case(client,co)
    assert c['state']=='NEEDS_HUMAN'
    assert c['order']['amount']==129900
    assert client.post(f"/api/rescue/cases/{c['id']}/decision",json={'decision':'approve'}).status_code==200
    tick(7);c=case(client,co)
    assert c['state']=='RESOLVED'
    assert c['rescue']['human_decisions']==1
    assert c['rescue']['merchant_order'] is None

@pytest.mark.parametrize('scenario,expected',[('stockout','WAITING_CUSTOMER'),('deadline','WAITING_CUSTOMER'),('ambiguous','WAITING_CUSTOMER'),('status','INFORMATION_PROVIDED'),('duplicate_payments','NEEDS_HUMAN')])
def test_intent_and_feasibility_prevent_unwanted_actions(client,scenario,expected):
    co=demo(client,scenario);tick(8)
    c=case(client,co)
    assert c['state']==expected
    assert c['rescue']['merchant_order'] is None
    assert c['refund'] is None
    if scenario=='duplicate_payments':assert len(c['rescue']['payments'])==2

def test_duplicate_payment_events_and_detection_are_idempotent(client):
    co=demo(client);e=entry(client,co);payment=e['payment'];body={'event_id':'REPEAT-1','payment_id':payment['id'],'checkout_id':co,'amount_paise':payment['amount'],'status':'SUCCESS'}
    assert client.post('/api/rescue/mock-events',json=body).json()['accepted'] is True
    assert client.post('/api/rescue/mock-events',json=body).json()['accepted'] is False
    assert client.post('/api/rescue/mock-events',json={**body,'event_id':'WRONG','amount_paise':1}).status_code==409
    tick(10);e=entry(client,co)
    assert len(e['case']['rescue']['payments'])==1
    with main.connection() as db:assert db.execute('SELECT COUNT(*) FROM rescue_links').fetchone()[0]==1

def test_status_after_restoration_does_not_cancel_or_escalate(client):
    co=demo(client);tick(8);c=case(client,co)
    before=c['rescue']['merchant_order']['id']
    assert client.post(f"/api/rescue/cases/{c['id']}/preference",json={'intent':'status'}).status_code==200
    c=case(client,co)
    assert c['state']=='INFORMATION_PROVIDED'
    assert c['rescue']['merchant_order']['id']==before
    assert c['rescue']['inventory']['available']==4
    assert c['refund'] is None

def test_rescue_scorecard_is_isolated_reproducible_and_zero_credit(client):
    co=demo(client)
    response=client.post('/api/rescue-scorecards')
    assert response.status_code==200
    score=response.json();m=score['metrics']
    assert m['total']==26 and m['correct_outcomes']==26
    assert m['verified_completions']<26
    assert m['duplicate_orders']==m['duplicate_refunds']==m['conflicting_completions']==m['model_calls']==0
    assert m['recovered_order_value_paise']>0
    assert m['baseline_decision_matches']<m['correct_outcomes']
    assert len(client.get('/api/rescue').json()['entries'])==1
    assert case(client,co) is None
    main.initialize()
    assert client.get('/api/rescue-scorecards').json()['latest']['id']==score['id']

def test_financial_fence_blocks_legacy_refund_execution_for_restore_path(client):
    co=demo(client);tick(2);c=case(client,co)
    with main.connection() as db:
        db.execute("UPDATE cases SET state='SUBMIT_REFUND',due=0 WHERE id=?",(c['id'],))
        db.execute("UPDATE orders SET fulfillment='CANCELLED' WHERE id=?",(c['order_id'],))
    tick()
    c=case(client,co)
    assert c['state']=='NEEDS_HUMAN'
    assert c['refund'] is None
    assert c['diagnostics']['provider_refund_count']==0

def test_deadline_revalidated_before_order_creation(client):
    co=demo(client,'interruption');tick(4);c=case(client,co)
    assert c['state']=='RESCUE_PAUSED'
    with main.connection() as db:
        db.execute('UPDATE rescue_links SET today_only=1 WHERE case_id=?',(c['id'],))
        db.execute('UPDATE rescue_inventory SET delivery_today=0')
    assert client.post(f"/api/rescue/cases/{c['id']}/decision",json={'decision':'resume'}).status_code==200
    tick(2);c=case(client,co)
    assert c['state']=='WAITING_CUSTOMER'
    assert c['rescue']['inventory']['available']==5
    assert c['rescue']['merchant_order'] is None
