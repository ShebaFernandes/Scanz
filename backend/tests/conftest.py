import pytest

@pytest.fixture(autouse=True)
def isolate_live_ai_settings(monkeypatch):
    # All provider tests use stub transports. Never consume or change the
    # user's real one-shot allowance during automated verification.
    monkeypatch.setenv('AI_LIVE_ENABLED','true')
    monkeypatch.setenv('AI_ONE_SHOT','false')
