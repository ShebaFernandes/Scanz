"""Explicit opt-in, globally single-use live model demo on synthetic records."""
import time
import uuid
from fastapi import HTTPException
from pydantic import BaseModel, Field
from typing import Literal
try:
    from . import auth, ai_agent
except ImportError:
    import auth, ai_agent

class Investigation(BaseModel):
    complaint: str = Field(min_length=3,max_length=1000)
    consent: Literal[True]

def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS live_demo_allowance (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, case_id TEXT NOT NULL, created REAL NOT NULL)')

def enabled():
    return ai_agent.provider_name()=='sarvam' and ai_agent.setting('AI_DEMO_ENABLED','false').lower()=='true' and ai_agent.setting('AI_ONE_SHOT','false').lower()=='true'

def install(app,engine):
    @app.get('/api/ai-demo/status')
    def status():
        with engine.connection() as db:
            initialize(db)
            row=db.execute('SELECT * FROM live_demo_allowance WHERE id=1').fetchone()
        return {'available':enabled() and ai_agent.setting('AI_LIVE_ENABLED','false').lower()=='true' and ai_agent.configured() and not row,
                'used':bool(row),'case_id':row['case_id'] if row and row['owner']==auth.customer_id() else None,
                'provider':ai_agent.provider_name(),'max_model_requests':8}

    @app.post('/api/ai-demo/investigate')
    def investigate(body:Investigation):
        if (auth.principal.get() or {}).get('role')!='operator':raise HTTPException(403,'Merchant demo access is required.')
        if not enabled() or ai_agent.setting('AI_LIVE_ENABLED','false').lower()!='true':raise HTTPException(503,'The live AI demonstration is paused.')
        if not ai_agent.configured():raise HTTPException(503,'Configure your server-side AI key first.')
        owner=auth.customer_id();cid='CASE-'+uuid.uuid4().hex[:8].upper();oid='AI-SAMPLE-'+uuid.uuid4().hex[:12].upper();now=time.time()
        with engine.connection() as db:
            initialize(db)
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM live_demo_allowance WHERE id=1').fetchone():raise HTTPException(409,'The single live investigation has already been used. No additional model call was queued.')
            db.execute('INSERT INTO live_demo_allowance VALUES (1,?,?,?)',(owner,cid,now))
            db.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)',(oid,owner,'Scanz AI sample store',79900,'','SUCCESS','CANCELLED','Synthetic cancelled order for the live AI demonstration'))
            db.execute('INSERT INTO cases(id,order_id,complaint,state,created,updated,due,agent_mode,offline_intent) VALUES (?,?,?,?,?,?,?,?,?)',(cid,oid,ai_agent.redact(body.complaint.strip()),'AI_QUEUED',now,now,now,'ai','refund'))
            engine.event(db,cid,'One live AI investigation authorized','Customer approved sending this sample complaint and synthetic order evidence to the model. Payment and refund operations remain mocked. The global allowance is now consumed.')
            return engine.snapshot(db,cid)
