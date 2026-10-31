import pytest

@pytest.fixture(autouse=True)
def isolate_live_ai_settings(monkeypatch):
    # All provider tests use stub transports. Never consume or change the
    # user's real one-shot allowance during automated verification.
    monkeypatch.setenv('AI_LIVE_ENABLED','true')
    monkeypatch.setenv('AI_ONE_SHOT','false')

# Legacy workflow tests authenticate through the real HTTP session flow.
def authenticated_client(engine):
    import time
    from fastapi.testclient import TestClient
    engine.initialize()
    with engine.connection() as db:
        db.execute('INSERT OR IGNORE INTO auth_users VALUES (?,?,?,?,?,?)',('customer-demo','workflow@example.test','Workflow operator',engine.auth.hash_password('Workflow-test-password-123'),'operator',time.time()))
    client=TestClient(engine.app)
    response=client.post('/api/auth/login',json={'email':'workflow@example.test','password':'Workflow-test-password-123'})
    assert response.status_code==200,response.text
    client.headers['X-CSRF-Token']=client.cookies.get(engine.auth.CSRF)
    return client
