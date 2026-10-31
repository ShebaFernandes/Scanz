"""Presence-only NFC handoff; no financial actions, card data, or model calls."""
import concurrent.futures
import hashlib
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
    monkeypatch.setattr(main,'DB',tmp_path/'tap.sqlite3')
    async def forbidden(*args,**kwargs):raise AssertionError('Tap must not call a model')
    monkeypatch.setattr(main.ai_agent,'run_agent',forbidden);main.initialize();return authenticated_client(main)

def create(client):
    co=client.post('/api/rescue/demo',json={'scenario':'stockout','category':'electronics'}).json()['checkout_id']
    r=client.post('/api/tap/sessions',json={'checkout_id':co});assert r.status_code==200;return r.json()

def redeem(client,s,source='android_nfc',**extra):return client.post('/api/tap/redeem',json={'token':s['token'],'source':source,**extra})
def headers(s):return {'X-Tap-Owner':s['owner_key']}

def test_pairing_uses_hashed_secrets_and_scoped_status(client):
    s=create(client);assert len(s['token'])==43 and s['expires']>time.time()
    with main.connection() as db:
        row=dict(db.execute('SELECT * FROM tap_sessions').fetchone())
        assert s['token'] not in row.values() and s['owner_key'] not in row.values()
        assert row['token_hash']==hashlib.sha256(s['token'].encode()).hexdigest()
    url='/api/tap/sessions/'+s['id']
    assert client.get(url).status_code==404
    assert client.get(url,headers={'X-Tap-Owner':'wrong'}).status_code==404
    state=client.get(url,headers=headers(s)).json();assert state['state']=='WAITING'
    assert 'token' not in state and 'owner_key' not in state
    assert client.post('/api/tap/sessions',json={'checkout_id':'ORD-PRIVATE'}).status_code==404

@pytest.mark.parametrize('source',['android_nfc','simulation'])
def test_single_use_handoff_cannot_mutate_money_or_invoke_ai(client,source):
    s=create(client)
    with main.connection() as db:
        for name in ['merchant_orders','provider_refunds','refunds','tool_calls']:assert db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]==0
        db.execute('UPDATE rescue_payments SET paid_at=0');main.advance_db(db,now=time.time(),step=1)
        before=[tuple(r) for r in db.execute('SELECT id,state,due FROM cases')]
    r=redeem(client,s,source);assert r.status_code==200
    result=r.json();assert result['checkout_id']==s['checkout_id'] and result['card_identity_verified'] is False
    assert result['source']==source and result['state']=='REDEEMED'
    assert redeem(client,s,source).status_code==409
    with main.connection() as db:
        assert [tuple(r) for r in db.execute('SELECT id,state,due FROM cases')]==before
        for name in ['merchant_orders','provider_refunds','refunds','tool_calls']:assert db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM events WHERE title='Tap demo handoff recorded'").fetchone()[0]==1


def test_expired_and_cancelled_pairings_cannot_be_redeemed(client):
    s=create(client)
    with main.connection() as db:db.execute('UPDATE tap_sessions SET expires=0 WHERE id=?',(s['id'],))
    assert redeem(client,s).status_code==410
    assert client.get('/api/tap/sessions/'+s['id'],headers=headers(s)).json()['state']=='EXPIRED'
    s=create(client);url='/api/tap/sessions/'+s['id']+'/cancel'
    assert client.post(url).status_code==404
    assert client.post(url,headers=headers(s)).json()['state']=='CANCELLED'
    assert redeem(client,s).status_code==409


def test_rejects_card_payload_and_forged_source(client):
    s=create(client)
    for extra in [{'pan':'REDACTED'},{'uid':'REDACTED'},{'expiry':'REDACTED'}]:assert redeem(client,s,**extra).status_code==422
    assert redeem(client,s,source='verified_bank_card').status_code==422
    assert client.post('/api/tap/redeem',json={'token':'x'*43,'source':'android_nfc'}).status_code==404
    assert client.post('/api/tap/redeem',json={'token':'not-a-token','source':'simulation'}).status_code==422


def test_simultaneous_redeems_cannot_duplicate_receipts(client):
    s=create(client)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        codes=list(pool.map(lambda _:redeem(client,s).status_code,range(4)))
    assert sorted(codes)==[200,409,409,409]


def test_android_source_has_no_payment_or_card_identifier_reads():
    source=Path('android-tap/app/src/main/java/com/resolve/tap/MainActivity.java').read_text()
    # Audit executable source; documentation mentions deliberately absent methods.
    executable='\n'.join(line for line in source.splitlines() if not line.strip().startswith(('/','*')))
    assert '.getId(' not in executable and '.transceive(' not in executable and '.connect(' not in executable
    manifest=Path('android-tap/app/src/main/AndroidManifest.xml').read_text()
    assert 'android.permission.INTERNET' not in manifest and 'android.permission.NFC' in manifest
