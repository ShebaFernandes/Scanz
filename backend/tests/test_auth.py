"""HTTP integration tests: actual password/session/CSRF and cross-account isolation."""
import sys
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main

@pytest.fixture
def clients(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB',tmp_path/'auth.sqlite3');monkeypatch.setenv('AI_LIVE_ENABLED','false');monkeypatch.setenv('AI_ONE_SHOT','false')
    async def forbidden(*a,**kw):raise AssertionError('No real model may be called')
    monkeypatch.setattr(main.ai_agent,'run_agent',forbidden);main.initialize()
    return TestClient(main.app),TestClient(main.app),TestClient(main.app)

def register(c,email='alice@example.test'):
    r=c.post('/api/auth/register',json={'email':email,'password':'Not-my-card-PIN-12345','name':'Account Owner'})
    assert r.status_code==200,r.text;c.headers['X-CSRF-Token']=c.cookies.get('resolve_csrf');return r.json()['user']
def demo(c):
    r=c.post('/api/demo-orders',json={'last4':'4821'});assert r.status_code==200;return r.json()['order']
def case(c,order):return c.post('/api/cases',json={'order_id':order['id'],'complaint':'Please check payment','agent_mode':'deterministic'}).json()
def operator(c,user):
    with main.connection() as db:db.execute("UPDATE auth_users SET role='operator' WHERE id=?",(user['id'],))

@pytest.mark.parametrize('path,method',[('/api/cases','get'),('/api/rescue','get'),('/api/commerce/categories','get'),('/api/card-candidates','post'),('/api/demo-orders','post'),('/api/lab/start','post'),('/api/evaluations','get'),('/api/rescue-scorecards','get'),('/api/tap/sessions','post'),('/api/integrations/paytm/status','get')])
def test_unauthenticated_api_is_closed(clients,path,method):
    r=getattr(clients[0],method)(path);assert r.status_code==401

def test_sessions_hash_passwords_and_secrets_logout_revokes(clients):
    a,b,_=clients;user=register(a)
    assert user['role']=='customer' and a.get('/api/auth/me').json()['user']['id']==user['id']
    with main.connection() as db:
        row=db.execute('SELECT * FROM auth_users').fetchone();assert row['password_hash'].startswith('scrypt$') and 'Not-my-card-PIN' not in row['password_hash']
        session=db.execute('SELECT * FROM auth_sessions').fetchone();assert a.cookies.get('resolve_session') not in session[:]
    raw=a.cookies.get('resolve_session');assert a.post('/api/auth/logout').status_code==200
    b.cookies.set('resolve_session',raw);assert b.get('/api/cases').status_code==401
    assert a.post('/api/auth/login',json={'email':'alice@example.test','password':'wrong'}).status_code==401
    r=a.post('/api/auth/login',json={'email':'ALICE@example.test','password':'Not-my-card-PIN-12345'});assert r.status_code==200
    assert 'httponly' in r.headers.get_list('set-cookie')[0].lower() and 'samesite=lax' in r.headers.get_list('set-cookie')[0].lower()

def test_expiry_csrf_and_cross_origin_block_writes(clients):
    a,_,_=clients;register(a);token=a.headers.pop('X-CSRF-Token')
    assert a.post('/api/demo-orders',json={'last4':'4821'}).status_code==403
    a.headers['X-CSRF-Token']='forged';assert a.post('/api/demo-orders',json={'last4':'4821'}).status_code==403
    a.headers['X-CSRF-Token']=token
    assert a.post('/api/demo-orders',json={'last4':'4821'},headers={'Origin':'https://attacker.example'}).status_code==403
    assert a.post('/api/demo-orders',json={'last4':'4821'},headers={'Origin':'http://localhost:3000'}).status_code==200
    with main.connection() as db:db.execute('UPDATE auth_sessions SET expires=0')
    assert a.get('/api/cases').status_code==401

def test_signup_cannot_choose_owner_role_or_claim_legacy_records(clients):
    a,b,_=clients
    assert a.post('/api/auth/register',json={'email':'a@example.test','name':'A','password':'Not-my-card-PIN-12345','role':'operator'}).status_code==422
    register(a);assert a.get('/api/cases').json()['cases']==[]
    assert a.post('/api/card-candidates',json={'last4':'4821'}).json()['orders']==[]
    assert a.post('/api/cases',json={'order_id':'ORD-1042','complaint':'Get refund'}).status_code==404
    assert a.post('/api/auth/register',json={'email':'short@example.test','name':'B','password':'short'}).status_code==422

def test_customer_cannot_use_staff_or_live_ai_tools(clients):
    a,_,_=clients;register(a);order=demo(a);c=case(a,order)
    for path,body in [('/api/cases/'+c['id']+'/review',{'decision':'approve'}),('/api/lab/start',{'scenario':'normal'}),('/api/rescue/settings',{'grace_seconds':1}),('/api/evaluations',{}),('/api/integrations/paytm/discover',{})]:assert a.post(path,json=body).status_code==403
    assert a.post('/api/cases',json={'order_id':order['id'],'complaint':'Please refund','agent_mode':'ai'}).status_code==503
    with main.connection() as db:assert db.execute('SELECT COUNT(*) FROM tool_calls').fetchone()[0]==0

def test_cross_account_order_case_and_checkout_paths_are_isolated(clients):
    a,b,_=clients;ua=register(a);ub=register(b,'bob@example.test');oa=demo(a);ob=demo(b);ca=case(a,oa)
    assert oa['id']!=ob['id']
    assert [o['id'] for o in b.post('/api/card-candidates',json={'last4':'4821'}).json()['orders']]==[ob['id']]
    assert b.get('/api/cases').json()['cases']==[]
    assert b.post('/api/cases',json={'order_id':oa['id'],'complaint':'Get refund'}).status_code==404
    for suffix in ['', '/reply','/handoff','/retry-ai','/enable-ai']:
        path='/api/cases/'+ca['id']+suffix;r=b.get(path) if not suffix else b.post(path,json={'message':'Check this'})
        assert r.status_code in {404,503},(suffix,r.text)
    co=a.post('/api/rescue/demo',json={'scenario':'stockout'}).json()['checkout_id']
    assert b.get('/api/rescue').json()['entries']==[]
    assert b.get('/api/commerce/'+co).status_code==404
    for suffix,body in [('alternative',{'product_id':co+'-matched','consent':True}),('delivery-quotes',{}),('channels',{'channel':'sms','enabled':True,'consent':True})]:assert b.post('/api/commerce/'+co+'/'+suffix,json=body).status_code==404
    with main.connection() as db:
        db.execute('UPDATE rescue_payments SET paid_at=0');main.advance_db(db,now=time.time(),step=0)
    c=a.get('/api/rescue').json()['entries'][0]['case']
    assert b.post('/api/rescue/cases/'+c['id']+'/preference',json={'intent':'refund'}).status_code==404
    operator(b,ub)
    assert b.post('/api/cases/'+ca['id']+'/review',json={'decision':'approve'}).status_code==404
    assert b.post('/api/rescue/cases/'+c['id']+'/decision',json={'decision':'approve'}).status_code==404
    assert b.post('/api/rescue/'+co+'/confirm-original').status_code==404
    assert b.post('/api/rescue/mock-events',json={'checkout_id':co,'event_id':'E-other','payment_id':'P-other','amount_paise':79900}).status_code==404
    assert b.get('/api/commerce/analytics').json()['totals']['checkouts']==0

def test_rate_limit_and_generic_login_errors(clients):
    a,_,_=clients;register(a)
    for _ in range(10):assert a.post('/api/auth/login',json={'email':'missing@example.test','password':'wrong'}).status_code==401
    assert a.post('/api/auth/login',json={'email':'missing@example.test','password':'wrong'}).status_code==429

def test_background_worker_can_process_multiple_accounts_without_exposing_them(clients):
    a,b,_=clients;register(a);register(b,'bob@example.test');case(a,demo(a));case(b,demo(b))
    for _ in range(10):
        with main.connection() as db:db.execute('UPDATE cases SET due=0 WHERE due IS NOT NULL');main.advance_db(db,step=0)
    for client in (a,b):
        records=client.get('/api/cases').json()['cases'];assert len(records)==1 and records[0]['state']=='RESOLVED'
        assert records[0]['order']['customer']==client.get('/api/auth/me').json()['user']['id']

def test_operator_sees_only_explicitly_assigned_customer_case(clients):
    customer,staff,other=clients;register(customer);operator_user=register(staff,'staff@example.test');register(other,'other@example.test');operator(staff,operator_user)
    owned=demo(customer)
    with main.connection() as db:db.execute('UPDATE orders SET amount=149900 WHERE id=?',(owned['id'],))
    c=case(customer,owned);private=case(other,demo(other))
    for _ in range(5):
        with main.connection() as db:db.execute('UPDATE cases SET due=0 WHERE due IS NOT NULL');main.advance_db(db,step=0)
    assert customer.get('/api/cases/'+c['id']).json()['state']=='NEEDS_HUMAN'
    assert staff.get('/api/cases/'+c['id']).status_code==404
    with main.connection() as db:db.execute('INSERT INTO support_assignments VALUES (?,?)',(owned['id'],operator_user['id']))
    records=staff.get('/api/cases').json()['cases'];assert [record['id'] for record in records]==[c['id']]
    assert staff.get('/api/cases/'+private['id']).status_code==404
    assert staff.post('/api/cases/'+c['id']+'/review',json={'decision':'approve'}).status_code==200
    for _ in range(8):
        with main.connection() as db:db.execute('UPDATE cases SET due=0 WHERE due IS NOT NULL');main.advance_db(db,step=0)
    assert customer.get('/api/cases/'+c['id']).json()['state']=='RESOLVED'

def test_session_rotation_and_csrf_are_bound_to_the_session(clients):
    a,b,_=clients;register(a);register(b,'bob@example.test')
    stolen_csrf=b.cookies.get('resolve_csrf');assert a.post('/api/demo-orders',json={'last4':'4821'},headers={'X-CSRF-Token':stolen_csrf}).status_code==403
    old=a.cookies.get('resolve_session')
    assert a.post('/api/auth/login',json={'email':'alice@example.test','password':'Not-my-card-PIN-12345'}).status_code==200
    assert old!=a.cookies.get('resolve_session')
    with main.connection() as db:assert db.execute('SELECT 1 FROM auth_sessions WHERE token_hash=?',(main.auth.digest(old),)).fetchone() is None

def test_cross_origin_login_and_signup_are_rejected(clients):
    a,_,_=clients
    for path,body in [('/api/auth/register',{'email':'a@example.test','name':'A','password':'Password-is-not-a-PIN'}),('/api/auth/login',{'email':'a@example.test','password':'wrong'})]:assert a.post(path,json=body,headers={'Origin':'https://attacker.example'}).status_code==403

def test_upi_lookup_is_unverified_and_account_scoped(clients):
    a,b,_=clients;register(a);register(b,'bob@example.test')
    payload={'upi_id':' Alice@Bank '}
    assert a.post('/api/upi-candidates',json=payload).json()['orders']==[]
    r=a.post('/api/demo-upi-orders',json=payload);assert r.status_code==200
    order=r.json()['order'];assert order['last4']=='' and r.json()['verified'] is False
    assert a.post('/api/demo-upi-orders',json=payload).json()['order']['id']==order['id']
    result=a.post('/api/upi-candidates',json={'upi_id':'alice@bank'}).json()
    assert result['verified'] is False and len(result['orders'])==1
    assert b.post('/api/upi-candidates',json=payload).json()['orders']==[]
    assert b.post('/api/cases',json={'order_id':order['id'],'complaint':'Refund please'}).status_code==404
    for value in ['1234','name@@bank','<script>@bank','name@','a@b']:
        assert a.post('/api/upi-candidates',json={'upi_id':value}).status_code==422
    assert a.post('/api/cases',json={'order_id':order['id'],'complaint':'Check my UPI payment','offline_intent':'status'}).status_code==200


def test_csv_import_retired(clients):
    a,_,_=clients;register(a)
    r=a.post('/api/statement-transactions',json={'last4':'9533','transactions':[{'date':'2026-10-30','merchant':'Shop','amount_paise':100}]})
    assert r.status_code==410

def test_merchant_sync_private_idempotent_and_incremental(clients):
    a,b,_=clients;alice=register(a);register(b,'bob@example.test')
    assert a.get('/api/purchases').json()['orders']==[]
    r=a.post('/api/purchases/sync');assert r.status_code==200 and r.json()['added']==3 and r.json()['simulated'] is True
    assert a.post('/api/purchases/sync').json()['added']==0
    listing=a.get('/api/purchases').json();assert len(listing['orders'])==3 and listing['source']=='mock_merchant' and listing['last_synced']
    order=listing['orders'][0]
    assert b.get('/api/purchases').json()['orders']==[]
    assert b.post('/api/cases',json={'order_id':order['id'],'complaint':'Refund please'}).status_code==404
    c=a.post('/api/cases',json={'order_id':order['id'],'complaint':'Check my payment','offline_intent':'status'})
    assert c.status_code==200
    a.post('/api/purchases/sync')
    matched=next(o for o in a.get('/api/purchases').json()['orders'] if o['id']==order['id'])
    assert matched['case_id']==c.json()['id']
    with main.connection() as db:
        db.execute('INSERT INTO merchant_feed VALUES (?,?,?,?,?,?,?,?)',(alice['id'],'new-order','New Shop',50000,'1234','SUCCESS','CANCELLED','Simulated new purchase'))
    assert a.post('/api/purchases/sync').json()['added']==1
    assert len(a.get('/api/purchases').json()['orders'])==4
    assert b.post('/api/purchases/sync').json()['added']==3
    assert len(b.get('/api/purchases').json()['orders'])==3
    a.post('/api/auth/logout')
    assert a.post('/api/purchases/sync').status_code==401

def test_no_signin_guest_demo_is_private_and_offline(clients,monkeypatch):
    a,b,_=clients;monkeypatch.setenv('AUTH_DEMO_MODE','true')
    r=a.post('/api/auth/demo');assert r.status_code==200;uid=r.json()['user']['id'];assert uid.startswith('GUEST-') and r.json()['user']['role']=='operator'
    a.headers['X-CSRF-Token']=a.cookies.get('resolve_csrf')
    assert a.post('/api/purchases/sync').status_code==200
    orders=a.get('/api/purchases').json()['orders'];assert len(orders)==3
    assert a.post('/api/auth/demo').json()['user']['id']==uid
    a.headers['X-CSRF-Token']=a.cookies.get('resolve_csrf')
    r=b.post('/api/auth/demo');assert r.status_code==200 and r.json()['user']['id']!=uid
    b.headers['X-CSRF-Token']=b.cookies.get('resolve_csrf')
    assert b.get('/api/purchases').json()['orders']==[]
    assert b.post('/api/cases',json={'order_id':orders[0]['id'],'complaint':'Refund please'}).status_code==404
    assert a.post('/api/cases',json={'order_id':orders[0]['id'],'complaint':'Check','agent_mode':'ai'}).status_code==403
    assert a.get('/api/integrations/paytm/status').status_code==403
    assert a.post('/api/auth/demo',headers={'Origin':'https://evil.example'}).status_code==403

def test_guest_can_open_offline_with_ai_enabled_but_live_paytm_is_blocked(clients,monkeypatch):
    a,_,_=clients;monkeypatch.setenv('AUTH_DEMO_MODE','true');monkeypatch.setenv('AI_LIVE_ENABLED','true')
    assert a.post('/api/auth/demo').status_code==200
    a.headers['X-CSRF-Token']=a.cookies.get('resolve_csrf')
    assert a.post('/api/cases',json={'order_id':'unused','complaint':'Refund','agent_mode':'ai'}).status_code==403
    monkeypatch.setenv('PAYTM_MCP_ENABLED','true')
    assert a.post('/api/auth/demo').status_code==503
    monkeypatch.setenv('PAYTM_MCP_ENABLED','false');monkeypatch.setenv('AUTH_DEMO_MODE','false')
    assert a.post('/api/auth/demo').status_code==403

def test_guest_demo_tools_do_not_promote_private_accounts_or_change_global_settings(clients,monkeypatch):
    a,_,_=clients;private=register(a);monkeypatch.setenv('AUTH_DEMO_MODE','true')
    guest=a.post('/api/auth/demo').json()['user'];assert guest['id']!=private['id'] and guest['role']=='operator'
    a.headers['X-CSRF-Token']=a.cookies.get('resolve_csrf')
    assert a.get('/api/commerce/analytics').status_code==200
    assert a.post('/api/rescue/settings',json={'grace_seconds':2}).status_code==403
    with main.connection() as db:assert db.execute('SELECT role FROM auth_users WHERE id=?',(private['id'],)).fetchone()[0]=='customer'

def test_single_live_demo_requires_consent_and_is_globally_capped(clients,monkeypatch):
    a,b,_=clients
    for k,v in {'AUTH_DEMO_MODE':'true','AI_DEMO_ENABLED':'true','AI_ONE_SHOT':'true','AI_LIVE_ENABLED':'true','AI_PROVIDER':'sarvam','SARVAM_API_KEY':'test-not-real'}.items():monkeypatch.setenv(k,v)
    for c in (a,b):
        assert c.post('/api/auth/demo').status_code==200
        c.headers['X-CSRF-Token']=c.cookies.get('resolve_csrf')
    assert a.get('/api/ai-demo/status').json()['available']
    assert a.post('/api/ai-demo/investigate',json={'complaint':'Check payment only','consent':False}).status_code==422
    assert a.get('/api/ai-demo/status').json()['available']
    r=a.post('/api/ai-demo/investigate',json={'complaint':'Just check status. Do not refund.','consent':True})
    assert r.status_code==200,r.text
    cid=r.json()['id']
    assert r.json()['state']=='AI_QUEUED'
    assert b.get('/api/cases/'+cid).status_code==404
    assert b.get('/api/ai-demo/status').json()['case_id'] is None
    for c in (a,b):
        assert c.post('/api/ai-demo/investigate',json={'complaint':'Another complaint','consent':True}).status_code==409
    assert a.get('/api/ai-demo/status').json()['case_id']==cid
    assert a.get('/api/integrations/paytm/status').status_code==403
    assert a.post('/api/cases/'+cid+'/retry-ai').status_code==403
    assert a.post('/api/cases/'+cid+'/reply',json={'message':'Please continue'}).status_code==403

def test_live_demo_cannot_spend_while_paused_or_without_merchant_access(clients,monkeypatch):
    a,_,_=clients;register(a)
    assert a.post('/api/ai-demo/investigate',json={'complaint':'Refund','consent':True}).status_code==403
    with main.connection() as db:db.execute("UPDATE auth_users SET role='operator'")
    monkeypatch.setenv('AI_DEMO_ENABLED','true');monkeypatch.setenv('AI_ONE_SHOT','true');monkeypatch.setenv('AI_LIVE_ENABLED','false')
    assert a.post('/api/ai-demo/investigate',json={'complaint':'Refund','consent':True}).status_code==503
    assert not a.get('/api/ai-demo/status').json()['used']


def test_deployed_frontend_origin_survives_proxy_metadata_and_dashboard_formatting(clients,monkeypatch):
    a,_,_=clients
    monkeypatch.setenv('AUTH_ALLOWED_ORIGINS',' https://scanz-one.vercel.app/ , http://localhost:3000 ')
    headers={'Origin':'https://scanz-one.vercel.app','Sec-Fetch-Site':'cross-site'}
    r=a.post('/api/auth/register',json={'email':'deployment@example.test','password':'Deployment-test-password','name':'Demo'},headers=headers)
    assert r.status_code==200,r.text
    a.headers['X-CSRF-Token']=a.cookies.get('resolve_csrf')
    assert a.post('/api/demo-orders',json={'last4':'4821'},headers=headers).status_code==200
    a.headers.pop('X-CSRF-Token')
    assert a.post('/api/demo-orders',json={'last4':'4821'},headers=headers).status_code==403

@pytest.mark.parametrize('origin',['https://scanz-one.vercel.app.attacker.test','https://attacker.test','null','https://scanz-one.vercel.app@attacker.test','https://scanz-one.vercel.app/path'])
def test_origin_fix_does_not_trust_spoofed_hosts(clients,monkeypatch,origin):
    a,_,_=clients;monkeypatch.setenv('AUTH_ALLOWED_ORIGINS','https://scanz-one.vercel.app/')
    headers={'Origin':origin,'X-Forwarded-Host':'scanz-one.vercel.app','X-Forwarded-Proto':'https'}
    assert a.post('/api/auth/login',json={'email':'test@example.test','password':'wrong'},headers=headers).status_code==403

def test_cross_site_request_without_origin_is_still_blocked(clients):
    assert clients[0].post('/api/auth/login',json={'email':'test@example.test','password':'wrong'},headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
