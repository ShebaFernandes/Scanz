import sys,time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main
from conftest import authenticated_client

@pytest.fixture
def ctx(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB',tmp_path/'hub.sqlite3');monkeypatch.setenv('AI_LIVE_ENABLED','false');monkeypatch.setenv('PAYTM_MCP_ENABLED','false');main.initialize()
    return authenticated_client(main)

def challenge(c):
    r=c.post('/api/checkout/otp',json={'method':'card','identifier':'4821','phone':'9999999999','consent':True});assert r.status_code==200;return r.json()
def authorize(c):
    otp=challenge(c);r=c.post('/api/checkout/verify',json={'challenge_id':otp['challenge_id'],'code':otp['demo_code']});assert r.status_code==200
    c.headers['X-Checkout-Session']=r.json()['checkout_session'];return otp,r.json()
def start(c,scenario,outcome='refund'):
    p=next(p for p in c.get('/api/checkout/payments').json()['payments'] if p['scenario']==scenario)
    r=c.post('/api/checkout/cases',json={'payment_id':p['id'],'outcome':outcome,'authorize_mock_reversal':outcome=='refund'});assert r.status_code==200;return r.json()
def tick(now):
    with main.connection() as db:main.checkout_hub.tick(db,now)

def test_demo_otp_scoped_expiring_single_use_and_no_bank_verification(ctx):
    c=ctx;otp,g=authorize(c);assert otp['delivery']=='on_screen_only' and g['bank_ownership_verified'] is False
    assert c.post('/api/checkout/verify',json={'challenge_id':otp['challenge_id'],'code':otp['demo_code']}).status_code==400
    with main.connection() as db:
        row=db.execute('SELECT * FROM checkout_otp').fetchone();assert row['phone_last4']=='9999' and otp['demo_code'] not in row['code_hash']
        assert db.execute('SELECT token_hash FROM checkout_grants').fetchone()[0]!=g['checkout_session']
        db.execute('UPDATE checkout_grants SET expires=?',(time.time()-1,))
    assert c.get('/api/checkout/payments').status_code==403

def test_otp_rate_limit_and_attempt_budget_persist(ctx):
    c=ctx;otp=challenge(c)
    assert c.post('/api/checkout/otp',json={'method':'card','identifier':'4821','phone':'9999999999','consent':True}).status_code==429
    wrong='000000' if otp['demo_code']!='000000' else '111111'
    for _ in range(5):assert c.post('/api/checkout/verify',json={'challenge_id':otp['challenge_id'],'code':wrong}).status_code==400
    main.initialize()
    assert c.post('/api/checkout/verify',json={'challenge_id':otp['challenge_id'],'code':otp['demo_code']}).status_code==400
    with main.connection() as db:assert db.execute('SELECT attempts FROM checkout_otp').fetchone()[0]==5

def test_no_sensitive_card_numbers_or_unconsented_connection(ctx):
    c=ctx
    for identifier in ['4111111111111111','abc']:
        assert c.post('/api/checkout/otp',json={'method':'card','identifier':identifier,'phone':'9999999999','consent':True}).status_code==422
    assert c.post('/api/checkout/otp',json={'method':'card','identifier':'4821','phone':'9999999999','consent':False}).status_code==422
    assert c.get('/api/checkout/payments').status_code==403

def test_merchant_paid_produces_receipt_not_reversal(ctx):
    c=ctx;authorize(c);case=start(c,'merchant_paid');now=case['created'];tick(now);tick(now+4)
    final=c.get('/api/checkout/cases/'+case['id']).json()
    assert final['state']=='MERCHANT_PAID' and final['bank_ack'] and final['merchant_ack'] and final['reversal_ref'] is None and final['credit_ack'] is None

def test_reversal_acceptance_distinct_from_credit_and_idempotent(ctx):
    c=ctx;authorize(c);case=start(c,'reversal_credit');now=case['created'];tick(now);tick(now+4)
    accepted=c.get('/api/checkout/cases/'+case['id']).json();assert accepted['state']=='CREDIT_PENDING' and accepted['reversal_ref'] and accepted['credit_ack'] is None
    assert start(c,'reversal_credit')['id']==case['id']
    main.initialize();tick(now+8)
    final=c.get('/api/checkout/cases/'+case['id']).json();assert final['state']=='CREDIT_CONFIRMED' and final['credit_ack'] and final['due'] is None
    with main.connection() as db:
        assert db.execute('SELECT count(*) FROM checkout_cases').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM refunds').fetchone()[0]==0

def test_pending_credit_never_falsely_resolved(ctx):
    c=ctx;authorize(c);case=start(c,'reversal_pending');now=case['created'];tick(now);tick(now+4);tick(now+60)
    final=c.get('/api/checkout/cases/'+case['id']).json();assert final['state']=='TRACKING_CREDIT' and final['credit_ack'] is None and final['reversal_ref']

def test_unknown_merchant_result_no_reversal_and_deadline(ctx):
    c=ctx;authorize(c);case=start(c,'unresolved');now=case['created'];tick(now);tick(now+59)
    assert c.get('/api/checkout/cases/'+case['id']).json()['state']=='CHECKING'
    tick(now+60);final=c.get('/api/checkout/cases/'+case['id']).json()
    assert final['state']=='REVIEW_REQUIRED' and final['reversal_ref'] is None and final['bank_ack'] is None

def test_status_only_respects_intent_and_refund_needs_consent(ctx):
    c=ctx;authorize(c);case=start(c,'reversal_credit','verify');now=case['created'];tick(now);tick(now+4)
    final=c.get('/api/checkout/cases/'+case['id']).json();assert final['state']=='INFORMATION_PROVIDED' and final['reversal_ref'] is None
    p=next(p for p in c.get('/api/checkout/payments').json()['payments'] if p['scenario']=='reversal_pending')
    assert c.post('/api/checkout/cases',json={'payment_id':p['id'],'outcome':'refund'}).status_code==422

def test_cross_account_grant_payment_and_case_are_rejected(ctx):
    a=ctx;_,g=authorize(a);case=start(a,'merchant_paid');b=TestClient(main.app)
    b.post('/api/auth/register',json={'email':'hub-other@example.test','password':'Other-hub-password-123','name':'Other'})
    b.headers['X-CSRF-Token']=b.cookies.get('resolve_csrf');b.headers['X-Checkout-Session']=g['checkout_session']
    assert b.get('/api/checkout/payments').status_code==403
    assert b.get('/api/checkout/cases/'+case['id']).status_code==404
    authorize(b)
    assert b.post('/api/checkout/cases',json={'payment_id':case['payment_id'],'outcome':'refund','authorize_mock_reversal':True}).status_code==404
    assert b.get('/api/checkout/cases').json()['cases']==[]

def test_expired_otp_and_amount_limit_fail_closed(ctx):
    c=ctx;otp=challenge(c)
    with main.connection() as db:db.execute('UPDATE checkout_otp SET expires=?',(time.time()-1,));db.execute('UPDATE checkout_otp SET created=?',(time.time()-31,))
    assert c.post('/api/checkout/verify',json={'challenge_id':otp['challenge_id'],'code':otp['demo_code']}).status_code==400
    # A new challenge supersedes the old one and remains demo-only.
    r=c.post('/api/checkout/otp',json={'method':'upi','identifier':'demo@bank','phone':'9999999999','consent':True}).json()
    g=c.post('/api/checkout/verify',json={'challenge_id':r['challenge_id'],'code':r['demo_code']}).json();c.headers['X-Checkout-Session']=g['checkout_session']
    payment=next(p for p in c.get('/api/checkout/payments').json()['payments'] if p['scenario']=='reversal_credit')
    with main.connection() as db:db.execute('UPDATE checkout_payments SET amount=200000 WHERE id=?',(payment['id'],))
    case=start(c,'reversal_credit')
    tick(case['created']);tick(case['created']+4)
    final=c.get('/api/checkout/cases/'+case['id']).json();assert final['state']=='REVIEW_REQUIRED' and final['reversal_ref'] is None


def test_changed_transaction_and_late_worker_fail_closed(ctx):
    c=ctx;authorize(c);case=start(c,'reversal_credit')
    with main.connection() as db:db.execute('UPDATE checkout_payments SET reference=? WHERE id=?',('DIFFERENT-REFERENCE',case['payment_id']))
    tick(case['created'])
    final=c.get('/api/checkout/cases/'+case['id']).json()
    assert final['state']=='REVIEW_REQUIRED' and final['reversal_ref'] is None
    other=start(c,'merchant_paid');tick(other['deadline']+1)
    final=c.get('/api/checkout/cases/'+other['id']).json()
    assert final['state']=='REVIEW_REQUIRED' and final['merchant_ack'] is None

def test_lost_response_after_commit_recovers_original_case_and_one_reversal(ctx):
    c=ctx;authorize(c)
    p=next(p for p in c.get('/api/checkout/payments').json()['payments'] if p['scenario']=='reversal_credit')
    body={'payment_id':p['id'],'outcome':'refund','authorize_mock_reversal':True}
    first=c.post('/api/checkout/cases',json={**body,'simulate_response_loss':True})
    assert first.status_code==504
    retry=c.post('/api/checkout/cases',json=body);assert retry.status_code==200
    case=retry.json();titles=[e['title'] for e in case['events']]
    assert 'Response lost after save' in titles and 'Original operation recovered' in titles
    tick(case['created']);tick(case['created']+4);tick(case['created']+8)
    result=c.get('/api/checkout/cases/'+case['id']).json();assert result['state']=='CREDIT_CONFIRMED'
    assert c.post('/api/checkout/cases',json=body).json()['reversal_ref']==result['reversal_ref']
    with main.connection() as db:
        assert db.execute('SELECT count(*) FROM checkout_cases WHERE payment_id=?',(p['id'],)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM checkout_events WHERE case_id=? AND title='Mock reversal accepted'",(case['id'],)).fetchone()[0]==1
