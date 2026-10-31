from conftest import authenticated_client
import sys
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB',tmp_path/'ops.sqlite3')
    monkeypatch.setattr(main,'STEPS_SECONDS',1)
    async def forbidden(*args,**kwargs):
        raise AssertionError('Offline operations must never call an AI provider')
    monkeypatch.setattr(main.ai_agent,'run_agent',forbidden)
    main.initialize()
    # No lifespan workers: advance the actual engine explicitly and reproducibly.
    return authenticated_client(main)

def tick(n=1):
    for _ in range(n):
        with main.connection() as db:
            db.execute('UPDATE cases SET due=0 WHERE due IS NOT NULL')
            main.advance_db(db,now=time.time(),step=1)

def lab(client,scenario):
    response=client.post('/api/lab/start',json={'scenario':scenario})
    assert response.status_code==200
    return response.json()['id']

def get(client,cid):return client.get('/api/cases/'+cid).json()

@pytest.mark.parametrize('scenario',['timeout','delay'])
def test_recovery_reuses_one_provider_refund(client,scenario):
    cid=lab(client,scenario)
    tick(12)
    case=get(client,cid)
    assert case['state']=='RESOLVED'
    assert case['diagnostics']['provider_refund_count']==1
    assert case['diagnostics']['recovered']==1
    assert case['ai_tokens']==0
    assert any(e['kind']=='recovery' for e in case['events'])
    with main.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM refunds').fetchone()[0]==1

def test_acceptance_lost_then_reconciled_across_restart(client):
    cid=lab(client,'timeout');tick(4)
    before=get(client,cid)
    assert before['state']=='RETRY_PENDING'
    assert before['refund'] is None
    assert before['diagnostics']['provider_refund_count']==1
    with main.connection() as db:
        original=db.execute('SELECT ref FROM provider_refunds').fetchone()['ref']
    main.initialize() # Open a new connection; additive startup must preserve checkpoint.
    tick(8)
    result=get(client,cid)
    assert result['state']=='RESOLVED'
    assert result['refund']['ref']==original

def test_worker_interruption_persists_and_resumes(client):
    cid=lab(client,'interruption');tick(7)
    before=get(client,cid)
    assert before['state']=='PAUSED_RECOVERY'
    assert before['refund']['status']=='ACCEPTED'
    main.initialize()
    assert get(client,cid)['state']=='PAUSED_RECOVERY'
    response=client.post(f'/api/cases/{cid}/simulate',json={'kind':'resume'})
    assert response.status_code==200
    tick(5)
    result=get(client,cid)
    assert result['state']=='RESOLVED'
    assert result['refund']['ref']==before['refund']['ref']
    assert result['diagnostics']['provider_refund_count']==1

def test_interrupt_during_reconciliation_preserves_nested_checkpoint(client):
    cid=lab(client,'timeout');tick(4)
    assert get(client,cid)['state']=='RETRY_PENDING'
    assert client.post(f'/api/cases/{cid}/simulate',json={'kind':'interrupt'}).status_code==200
    assert client.post(f'/api/cases/{cid}/simulate',json={'kind':'resume'}).status_code==200
    tick(6)
    assert get(client,cid)['state']=='RESOLVED'
    assert get(client,cid)['diagnostics']['provider_refund_count']==1

def test_duplicate_and_late_events_never_regress(client):
    cid=lab(client,'normal');tick(8)
    original=get(client,cid)
    for kind in ('duplicate_event','late_event','duplicate_request'):
        response=client.post(f'/api/cases/{cid}/simulate',json={'kind':kind})
        assert response.status_code==200
        assert response.json()['state']=='RESOLVED'
        assert response.json()['refund']['ref']==original['refund']['ref']
    case=get(client,cid)
    assert case['diagnostics']['duplicates']==2
    assert case['diagnostics']['stale_events']==1
    assert case['diagnostics']['provider_refund_count']==1

def test_offline_status_intent_is_explicit(client):
    cid=lab(client,'status');tick(6)
    case=get(client,cid)
    assert case['state']=='INFORMATION_PROVIDED'
    assert case['refund'] is None
    assert case['offline_intent']=='status'

def test_control_room_approval_resumes(client):
    cid=lab(client,'approval');tick(4)
    case=get(client,cid)
    assert case['state']=='NEEDS_HUMAN'
    assert case['policy']['decision']=='approval_required'
    assert client.post(f'/api/cases/{cid}/review',json={'decision':'approve'}).status_code==200
    tick(6)
    assert get(client,cid)['state']=='RESOLVED'

def test_evaluation_is_isolated_persistent_and_zero_cost(client):
    cid=lab(client,'normal')
    before=get(client,cid)
    response=client.post('/api/evaluations')
    assert response.status_code==200
    report=response.json()
    assert len(report['cases'])==24
    assert report['metrics']['passed']==24
    assert report['metrics']['duplicate_refunds']==0
    assert report['metrics']['recoveries']==6
    assert report['model_calls']==0
    assert report['metrics']['autonomous_resolved']==report['metrics']['eligible_auto']
    assert get(client,cid)['state']==before['state']
    assert len(client.get('/api/cases').json()['cases'])==1
    assert client.get('/api/evaluations').json()['latest']['id']==report['id']
    main.initialize()
    assert client.get('/api/evaluations/'+report['id']).json()['metrics']==report['metrics']
    boundary=[x for x in report['cases'] if x['scenario']=='boundary']
    assert [x['actual'] for x in boundary]==['RESOLVED','NEEDS_HUMAN']

def test_recovery_lookup_exhausts_without_resubmitting(client):
    cid=lab(client,'timeout');tick(4)
    with main.connection() as db:db.execute('DELETE FROM provider_refunds')
    tick(7)
    result=get(client,cid)
    assert result['state']=='NEEDS_HUMAN'
    assert result['refund'] is None
    assert result['diagnostics']['attempts']==3

def test_evaluation_reports_real_failures_instead_of_canned_success(client,monkeypatch):
    def refuse_submission(db,case,order,now,step):
        return 'NEEDS_HUMAN',None
    monkeypatch.setattr(main.reliability,'submit',refuse_submission)
    report=client.post('/api/evaluations').json()
    assert report['metrics']['passed']<24
    assert report['metrics']['correct_action_rate']<100
    assert any(not c['passed'] for c in report['cases'])

def test_customer_selected_offline_status_action_never_refunds(client):
    response=client.post('/api/cases',json={'order_id':'ORD-1042','complaint':'Just check the status, please.','agent_mode':'deterministic','offline_intent':'status'})
    assert response.status_code==200
    cid=response.json()['id']
    tick(6)
    case=get(client,cid)
    assert case['state']=='INFORMATION_PROVIDED'
    assert case['refund'] is None
