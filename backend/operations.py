"""Offline recovery lab and repeatable evaluations of the production state machine."""
import json
import sqlite3
import time
import uuid
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel
try:
    from . import reliability as r
except ImportError:
    import reliability as r

class LabStart(BaseModel):
    scenario: Literal['normal','timeout','delay','interruption','approval','status'] = 'timeout'

class Simulation(BaseModel):
    kind: Literal['timeout','delay','duplicate_request','duplicate_event','late_event','interrupt','resume']


def resume(db,case,now,step):
    if case['state']!='PAUSED_RECOVERY':raise HTTPException(409,'This case has no paused checkpoint.')
    row=r.recovery_row(db,case['id'])
    target=row.get('checkpoint_state') or row['resume_state'] or 'REFUND_PENDING'
    db.execute('UPDATE cases SET state=?,due=?,updated=? WHERE id=?',(target,now+step,now,case['id']))
    db.execute('UPDATE recovery SET recovered=recovered+1,checkpoint_state=NULL,resume_state=? WHERE case_id=?',(row['resume_state'] if target=='RETRY_PENDING' else None,case['id']))
    r.log(db,case['id'],'Checkpoint restored','Resumed '+target+' from persisted state. Existing refund and reference were preserved.','recovery')


def run_evaluation(engine):
    """24 fixtures. No network, model calls, shared DB overrides, or sleep."""
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    try:
        engine.initialize_db(db)
        categories=['normal','boundary','approval','approved','pending','failed','fulfilled','status','existing','timeout','delay','interruption']
        results=[]
        for index in range(24):
            kind=categories[index%len(categories)]
            amount=((100000 if index<12 else 100001) if kind=='boundary' else 149900+index if kind in {'approval','approved'} else (1 if index==0 else 79900+index))
            payment='PENDING' if kind=='pending' else 'FAILURE' if kind=='failed' else 'SUCCESS'
            fulfillment='FULFILLED' if kind=='fulfilled' else 'CANCELLED'
            expected='NEEDS_HUMAN' if kind in {'approval','pending','failed','fulfilled'} or (kind=='boundary' and amount>100000) else 'INFORMATION_PROVIDED' if kind=='status' else 'RESOLVED'
            should_refund=expected=='RESOLVED'
            oid=f'EVAL-ORDER-{index:02d}';cid=f'EVAL-CASE-{index:02d}';now=1000000.
            db.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)',(oid,engine.auth.customer_id(),'Evaluation '+kind,amount,'0000',payment,fulfillment,'Synthetic structured evaluation fixture'))
            db.execute('INSERT INTO cases(id,order_id,complaint,state,created,updated,due,agent_mode,offline_intent) VALUES (?,?,?,?,?,?,?,?,?)',
                (cid,oid,'Structured offline fixture: '+kind,'OPEN',now,now,now,'deterministic','status' if kind=='status' else 'refund'))
            engine.event(db,cid,'Evaluation fixture started','Offline rules with structured intent. No language model is being evaluated.')
            if kind in {'timeout','delay','interruption'}:r.arm(db,cid,kind)
            if kind=='existing':
                ref='MOCK-EXISTING-'+str(index)
                db.execute('INSERT INTO refunds VALUES (?,?,?,?)',(oid,ref,amount,'PROCESSING'))
                db.execute('INSERT INTO provider_refunds VALUES (?,?,?,?,?,?)',(oid,'refund:'+oid,ref,amount,'PROCESSING',now))
            if kind=='pending':
                engine.payment_watch.begin(db,{'id':cid},now,engine)
                db.execute('UPDATE payment_watch SET deadline=?,interval_seconds=1 WHERE case_id=?',(now+5,cid))
            touched_human=False
            for tick in range(30):
                clock=now+tick
                engine.advance_db(db,now=clock,step=1)
                case=engine.snapshot(db,cid)
                if case['state']=='NEEDS_HUMAN':
                    touched_human=True
                    if kind=='approved':
                        db.execute("UPDATE cases SET approved=1,state='CHECK_POLICY',due=?,updated=? WHERE id=?",(clock+1,clock,cid))
                        engine.event(db,cid,'Fixture approval recorded','A simulated human approves; human decision time is excluded.')
                        continue
                if case['state']=='PAUSED_RECOVERY':
                    resume(db,case,clock,1)
                    continue
                if case['state'] in {'RESOLVED','NEEDS_HUMAN','INFORMATION_PROVIDED'}:break
            case=engine.snapshot(db,cid)
            # Replay duplicated submission and out-of-order event against completed cases.
            if should_refund and case['refund']:
                r.submit(db,case,case['order'],clock,1)
                r.apply_status(db,case,cid+'-credited','CREDITED',clock+1,1)
                r.apply_status(db,case,cid+'-late','PROCESSING',clock+2,1)
            case=engine.snapshot(db,cid)
            refunds=db.execute('SELECT COUNT(*) FROM refunds WHERE order_id=?',(oid,)).fetchone()[0]
            provider_count=db.execute('SELECT COUNT(*) FROM provider_refunds WHERE order_id=?',(oid,)).fetchone()[0]
            expected_refunds=1 if should_refund else 0
            handoff_complete=not touched_human or any(e['kind']=='review' and e['detail'] for e in case['events'])
            checks={
                'expected_state':case['state']==expected,
                'refund_count':refunds==expected_refunds,
                'provider_count':provider_count==expected_refunds,
                'amount':not case['refund'] or case['refund']['amount']==amount,
                'no_model_usage':case['ai_tokens']==0 and case['agent_mode']=='deterministic',
                'handoff_evidence':handoff_complete,
            }
            eligible_auto=should_refund and kind!='approved'
            results.append({'id':cid,'scenario':kind,'variant':index//12+1,'expected':expected,
                'actual':case['state'],'passed':all(checks.values()),'checks':checks,
                'eligible_auto':eligible_auto,'human_required':touched_human,
                'simulation_ticks':tick+1,'case':case})
        eligible=[x for x in results if x['eligible_auto']]
        resolved=[x for x in results if x['actual']=='RESOLVED']
        report={
            'id':'RUN-'+uuid.uuid4().hex[:8].upper(),'created':time.time(),
            'label':'Offline workflow evaluation','fixture_version':'v1 / 24 structured fixtures',
            'disclaimer':'Measures backend workflow rules and recovery on synthetic fixtures, not Sarvam language accuracy. Timings are virtual ticks, not bank or human latency.',
            'model_calls':0,'cases':results,
            'metrics':{'total':len(results),'passed':sum(x['passed'] for x in results),
                'correct_action_rate':round(sum(x['passed'] for x in results)/len(results)*100,1),
                'eligible_auto':len(eligible),'autonomous_resolved':sum(x['actual']=='RESOLVED' and not x['human_required'] for x in eligible),
                'autonomous_rate':round(sum(x['actual']=='RESOLVED' and not x['human_required'] for x in eligible)/len(eligible)*100,1) if eligible else None,
                'human_cases':sum(x['human_required'] for x in results),
                'handoffs_complete':sum(x['human_required'] and x['checks']['handoff_evidence'] for x in results),
                'duplicate_deliveries_ignored':sum(x['case']['diagnostics']['duplicates'] for x in results),
                'stale_events_ignored':sum(x['case']['diagnostics']['stale_events'] for x in results),
                'duplicate_refunds':sum(max(0,x['case']['diagnostics']['provider_refund_count']-1) for x in results),
                'recoveries':sum(x['case']['diagnostics']['recovered'] for x in results),
                'mean_resolution_ticks':round(sum(x['simulation_ticks'] for x in resolved)/len(resolved),1) if resolved else None,
            }}
        return report
    finally:db.close()


def install(app,engine):
    @app.post('/api/lab/start')
    def start(body: LabStart):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            token=uuid.uuid4().hex[:8].upper();cid='LAB-'+token;oid='LAB-ORDER-'+token;now=time.time()
            amount=149900 if body.scenario=='approval' else 79900
            db.execute('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)',(oid,engine.auth.customer_id(),'Recovery Lab',amount,'0000','SUCCESS','CANCELLED','SIMULATION: '+body.scenario))
            db.execute('INSERT INTO cases(id,order_id,complaint,state,created,updated,due,agent_mode,offline_intent) VALUES (?,?,?,?,?,?,?,?,?)',
                (cid,oid,'Offline demonstration: '+body.scenario,'OPEN',now,now,now+engine.STEPS_SECONDS,'deterministic','status' if body.scenario=='status' else 'refund'))
            engine.event(db,cid,'Offline lab case opened','Rule-based workflow. No model calls, no real payment, no Sarvam credits used.','simulation')
            if body.scenario in {'timeout','delay','interruption'}:r.arm(db,cid,body.scenario)
            return engine.snapshot(db,cid)

    @app.post('/api/cases/{case_id}/simulate')
    def simulate(case_id: str,body: Simulation):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');case=engine.snapshot(db,case_id);kind=body.kind
            if case['agent_mode']!='deterministic':raise HTTPException(409,'Use an offline lab case for fault simulation; real AI case history is preserved.')
            if kind in {'timeout','delay'}:
                allowed={'OPEN','CHECK_PAYMENT','CHECK_POLICY','SUBMIT_REFUND'} if kind=='timeout' else {'OPEN','CHECK_PAYMENT','CHECK_POLICY','SUBMIT_REFUND','REFUND_PENDING'}
                if case['state'] not in allowed:raise HTTPException(409,'This stage has passed. Start a fresh offline lab case.')
                r.arm(db,case_id,kind)
            elif kind=='duplicate_request':
                r.recovery_row(db,case_id)
                db.execute('UPDATE recovery SET duplicates=duplicates+1 WHERE case_id=?',(case_id,))
                engine.event(db,case_id,'Duplicate request prevented','Same case and stable operation key reused. No second workflow or refund was created.','recovery')
            elif kind in {'duplicate_event','late_event'}:
                if not case['refund'] or case['refund']['status']=='ACCEPTED':raise HTTPException(409,'Wait until the refund is processing or credited.')
                if kind=='duplicate_event':
                    status=case['refund']['status'];event_id=case_id+('-credited' if status=='CREDITED' else '-processing')
                    # Ensure receipt exists for a legacy case, then replay it.
                    if not db.execute('SELECT 1 FROM provider_events WHERE event_id=?',(event_id,)).fetchone():
                        db.execute('INSERT INTO provider_events VALUES (?,?,?,?)',(event_id,case_id,status,time.time()))
                    r.apply_status(db,case,event_id,status,time.time(),engine.STEPS_SECONDS)
                else:r.apply_status(db,case,case_id+'-late-'+uuid.uuid4().hex[:6],'ACCEPTED',time.time(),engine.STEPS_SECONDS)
            elif kind=='interrupt':
                if case['state'] not in {'SUBMIT_REFUND','REFUND_PENDING','AWAITING_CREDIT','RETRY_PENDING'}:raise HTTPException(409,'Interrupt an active refund stage or start an interruption lab case.')
                r.recovery_row(db,case_id)
                db.execute('UPDATE recovery SET checkpoint_state=? WHERE case_id=?',(case['state'],case_id))
                db.execute("UPDATE cases SET state='PAUSED_RECOVERY',due=NULL WHERE id=?",(case_id,))
                engine.event(db,case_id,'Worker interruption simulated','Only this case is paused. A durable checkpoint preserves its next step.','simulation')
            else:resume(db,case,time.time(),engine.STEPS_SECONDS)
            return engine.snapshot(db,case_id)

    @app.post('/api/evaluations')
    def evaluate():
        report=run_evaluation(engine)
        with engine.connection() as db:
            db.execute('INSERT INTO evaluation_runs(id,created,report,owner) VALUES (?,?,?,?)',(report['id'],report['created'],json.dumps(report),engine.auth.customer_id()))
        return report

    @app.get('/api/evaluations')
    def evaluations():
        with engine.connection() as db:
            rows=db.execute('SELECT report FROM evaluation_runs WHERE owner=? ORDER BY created DESC LIMIT 10',(engine.auth.customer_id(),)).fetchall()
            reports=[json.loads(row['report']) for row in rows]
            return {'runs':[{k:r[k] for k in ('id','created','label','metrics','model_calls')} for r in reports], 'latest':reports[0] if reports else None}

    @app.get('/api/evaluations/{run_id}')
    def evaluation(run_id: str):
        with engine.connection() as db:
            row=db.execute('SELECT report FROM evaluation_runs WHERE id=? AND owner=?',(run_id,engine.auth.customer_id())).fetchone()
            if not row:raise HTTPException(404,'Evaluation run not found')
            return json.loads(row['report'])
