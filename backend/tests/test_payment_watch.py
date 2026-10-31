import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main
from conftest import authenticated_client

@pytest.fixture
def ctx(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB',tmp_path/'watch.sqlite3');monkeypatch.setenv('AI_LIVE_ENABLED','false');monkeypatch.setenv('AI_ONE_SHOT','false')
    monkeypatch.setenv('PAYMENT_CHECK_SECONDS','10');monkeypatch.setenv('PAYMENT_ESCALATE_SECONDS','120')
    main.initialize();c=authenticated_client(main)
    return c

def create(c,oid='ORD-1044',intent='refund'):
    r=c.post('/api/cases',json={'order_id':oid,'complaint':'Please resolve this payment','offline_intent':intent})
    assert r.status_code==200;return r.json()

def advance(now):
    with main.connection() as db:main.advance_db(db,now=now,step=1)

def test_pending_is_scheduled_persisted_and_escalates_only_at_deadline(ctx):
    c=ctx;case=create(c);cid=case['id'];w=case['payment_watch'];start=w['started']
    assert case['state']=='WAITING_PAYMENT' and w['deadline']==start+120
    advance(start);first=c.get('/api/cases/'+cid).json()
    assert first['payment_watch']['checks']==1 and first['payment_watch']['next_check']==start+10
    for _ in range(3):advance(start+1)
    assert c.get('/api/cases/'+cid).json()['payment_watch']['checks']==1
    main.initialize() # checkpoint survives startup
    advance(start+119);assert c.get('/api/cases/'+cid).json()['state']=='WAITING_PAYMENT'
    advance(start+120);final=c.get('/api/cases/'+cid).json()
    assert final['state']=='NEEDS_HUMAN' and final['payment_watch']['next_check'] is None and final['refund'] is None
    assert create(c)['id']==cid
    assert c.post('/api/cases/'+cid+'/resolve-now').status_code==409

def test_explicit_mock_confirmation_resumes_refund_without_duplicates(ctx):
    c=ctx;c.post('/api/purchases/sync')
    oid=next(o['id'] for o in c.get('/api/purchases').json()['orders'] if o['merchant']=='Daily Brew')
    case=create(c,oid);cid=case['id'];start=case['payment_watch']['started']
    for n in [0,10,20]:advance(start+n)
    result=c.get('/api/cases/'+cid).json()
    assert result['state']=='CHECK_POLICY' and result['order']['payment_status']=='SUCCESS'
    assert result['order']['fulfillment']=='CANCELLED' and result['payment_watch']['checks']==3
    for i in range(21,32):advance(start+i)
    result=c.get('/api/cases/'+cid).json();assert result['state']=='RESOLVED'
    assert create(c,oid)['id']==cid
    with main.connection() as db:assert db.execute('SELECT count(*) FROM refunds WHERE order_id=?',(oid,)).fetchone()[0]==1

def test_status_only_never_refunds_after_confirmation(ctx):
    c=ctx;case=create(c,intent='status');cid=case['id'];start=case['payment_watch']['started']
    with main.connection() as db:db.execute("UPDATE orders SET payment_status='SUCCESS',fulfillment='CANCELLED' WHERE id='ORD-1044'")
    advance(start);advance(start+1)
    result=c.get('/api/cases/'+cid).json();assert result['state']=='INFORMATION_PROVIDED' and result['refund'] is None

def test_success_does_not_infer_cancellation(ctx):
    c=ctx;case=create(c);start=case['payment_watch']['started']
    with main.connection() as db:db.execute("UPDATE orders SET payment_status='SUCCESS',fulfillment='FULFILLED' WHERE id='ORD-1044'")
    advance(start);advance(start+1)
    result=c.get('/api/cases/'+case['id']).json();assert result['state']=='NEEDS_HUMAN' and result['refund'] is None

def test_legacy_pending_handoff_can_resume_once_without_resetting_deadline(ctx):
    c=ctx;case=create(c);cid=case['id']
    with main.connection() as db:
        db.execute('DELETE FROM payment_watch WHERE case_id=?',(cid,))
        db.execute("UPDATE cases SET state='NEEDS_HUMAN',due=NULL WHERE id=?",(cid,))
    r=c.post('/api/cases/'+cid+'/resolve-now');assert r.status_code==200 and r.json()['state']=='WAITING_PAYMENT'
    deadline=r.json()['payment_watch']['deadline']
    assert c.post('/api/cases/'+cid+'/resolve-now').json()['payment_watch']['deadline']==deadline

def test_failed_confirmation_never_refunds(ctx):
    c=ctx;case=create(c);start=case['payment_watch']['started']
    with main.connection() as db:db.execute("UPDATE orders SET payment_status='FAILURE' WHERE id='ORD-1044'")
    advance(start)
    result=c.get('/api/cases/'+case['id']).json()
    assert result['state']=='NEEDS_HUMAN' and result['refund'] is None and result['payment_watch']['next_check'] is None

def test_resolve_now_ownership_and_csrf(ctx):
    from fastapi.testclient import TestClient
    c=ctx;case=create(c)
    other=TestClient(main.app)
    r=other.post('/api/auth/register',json={'email':'other@watch.test','name':'Other','password':'Long-test-password-123'})
    assert r.status_code==200
    other.headers['X-CSRF-Token']=other.cookies.get('resolve_csrf')
    assert other.post('/api/cases/'+case['id']+'/resolve-now').status_code==404
    token=c.headers.pop('X-CSRF-Token')
    assert c.post('/api/cases/'+case['id']+'/resolve-now').status_code==403
    c.headers['X-CSRF-Token']=token
