"""Local support app: real model tool calling with a policy-controlled mock provider."""
import asyncio
import json
import hashlib
import re
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal
try:
    from . import ai_agent, reliability, rescue, commerce, tap, auth, paytm_mcp, statements, purchases, payment_watch, checkout_hub
except ImportError:
    import ai_agent
    import reliability
    import rescue
    import commerce
    import tap
    import auth
    import paytm_mcp
    import statements
    import purchases
    import payment_watch
    import checkout_hub
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DB = Path(os.environ.get('RESOLVE_DB', Path(__file__).with_name('resolve.sqlite3')))
DEMO_CUSTOMER = 'customer-demo'
STEPS_SECONDS = float(os.environ.get('RESOLVE_STEP_SECONDS', '2'))

@contextmanager
def connection():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def initialize():
    with connection() as db:
        initialize_db(db)

def initialize_db(db):
    statements.initialize(db)
    purchases.initialize(db)
    payment_watch.initialize(db)
    checkout_hub.initialize(db)
    db.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, customer TEXT, merchant TEXT, amount INTEGER, last4 TEXT, payment_status TEXT, fulfillment TEXT, description TEXT);
    CREATE TABLE IF NOT EXISTS order_upi (order_id TEXT PRIMARY KEY, upi_id TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS order_upi_lookup ON order_upi(upi_id);
    CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, order_id TEXT UNIQUE, complaint TEXT, state TEXT, created REAL, updated REAL, due REAL, refund_ref TEXT, approved INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, title TEXT, detail TEXT, kind TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS refunds (order_id TEXT PRIMARY KEY, ref TEXT UNIQUE, amount INTEGER, status TEXT);
    ''')
    # Additive migration preserves existing demo cases and refunds.
    columns = {r['name'] for r in db.execute('PRAGMA table_info(cases)')}
    for name, definition in {
        'agent_mode': "TEXT NOT NULL DEFAULT 'deterministic'",
        'offline_intent': "TEXT NOT NULL DEFAULT 'refund'",
        'brief': 'TEXT', 'ai_error': 'TEXT', 'ai_tokens': 'INTEGER NOT NULL DEFAULT 0',
    }.items():
        if name not in columns:
            db.execute(f'ALTER TABLE cases ADD COLUMN {name} {definition}')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS tool_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, name TEXT, result TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, content TEXT, created REAL);
    ''')
    reliability.initialize(db)
    rescue.initialize(db)
    commerce.initialize(db)
    tap.initialize(db)
    auth.initialize(db)
    paytm_mcp.initialize(db)
    orders = [
      ('ORD-1042',DEMO_CUSTOMER,'Urban Pantry',79900,'4821','SUCCESS','CANCELLED','Weekly grocery essentials'),
      ('ORD-1043',DEMO_CUSTOMER,'Studio Supply',149900,'4821','SUCCESS','CANCELLED','Desk essentials kit'),
      ('ORD-1044',DEMO_CUSTOMER,'Daily Brew',24900,'4821','PENDING','UNCONFIRMED','Coffee subscription'),
      ('ORD-1045',DEMO_CUSTOMER,'Paper & Pine',59900,'4821','SUCCESS','FULFILLED','Notebook collection'),
      ('ORD-1046',DEMO_CUSTOMER,'Metro Market',34900,'4821','FAILURE','CANCELLED','Evening essentials'),
      ('ORD-PRIVATE','another-customer','Private merchant',99900,'4821','SUCCESS','CANCELLED','Private order'),
    ]
    db.executemany('INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?,?,?)', orders)

def order_for(db, order_id):
    order = db.execute('SELECT * FROM orders WHERE id=?',(order_id,)).fetchone()
    if not order or not auth.can_access_order(db,order):
        raise HTTPException(404, 'Order not found in this demo account')
    return dict(order)

def event(db, case_id, title, detail, kind='done'):
    db.execute('INSERT INTO events(case_id,title,detail,kind,created) VALUES (?,?,?,?,?)',(case_id,title,detail,kind,time.time()))

def snapshot(db, case_id):
    row = db.execute('SELECT * FROM cases WHERE id=?',(case_id,)).fetchone()
    if not row:
        raise HTTPException(404,'Case not found')
    result = dict(row)
    result['order'] = order_for(db,result['order_id'])
    result['brief'] = json.loads(result['brief']) if result.get('brief') else None
    result['tools'] = [dict(r) for r in db.execute('SELECT id,name,result,created FROM tool_calls WHERE case_id=? ORDER BY id',(case_id,))]
    for tool in result['tools']:
        tool['result'] = json.loads(tool['result'])
        tool['evidence_id'] = 'E'+str(tool['id'])
    result['messages'] = [dict(r) for r in db.execute('SELECT content,created FROM messages WHERE case_id=? ORDER BY id',(case_id,))]
    result['events'] = [dict(e) for e in db.execute('SELECT * FROM events WHERE case_id=? ORDER BY id',(case_id,))]
    refund = db.execute('SELECT * FROM refunds WHERE order_id=?',(result['order_id'],)).fetchone()
    result['refund'] = dict(refund) if refund else None
    result['diagnostics'] = reliability.summary(db,result)
    result['policy'] = policy_for(db,result)
    result['rescue'] = rescue.details(db,case_id)
    result['payment_watch'] = payment_watch.summary(db,result)
    return result

def advance_once():
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        advance_db(db)

def advance_db(db, now=None, step=None):
    now = time.time() if now is None else now
    step = STEPS_SECONDS if step is None else step
    rescue.detect(db,now,step,sys.modules[__name__])
    cases = db.execute("SELECT * FROM cases WHERE due IS NOT NULL AND due<=? AND state NOT IN ('AI_QUEUED','AI_RUNNING')",(now,)).fetchall()
    for raw in cases:
        case = dict(raw)
        if rescue.advance(db,case,now,step,sys.modules[__name__]):
            continue
        order = order_for(db,case['order_id'])
        cid, state = case['id'], case['state']
        next_state, due = state, now+step
        if state == 'WAITING_PAYMENT':
            next_state,due=payment_watch.advance(db,case,order,now,step,sys.modules[__name__])
        elif state == 'OPEN':
            event(db,cid,'Order verified',f"{order['id']} belongs to this demo account. Merchant status: {order['fulfillment'].lower()}.")
            next_state = 'CHECK_PAYMENT'
        elif state == 'CHECK_PAYMENT':
            event(db,cid,'Payment checked',f"Mock Paytm reports {order['payment_status']}. Amount: ₹{order['amount']/100:,.2f}.")
            if order['payment_status']=='PENDING':next_state,due=payment_watch.begin(db,case,now,sys.modules[__name__])
            else:next_state = 'CHECK_POLICY'
        elif state == 'CHECK_POLICY':
            existing = db.execute('SELECT * FROM refunds WHERE order_id=?',(order['id'],)).fetchone()
            if order['payment_status']=='PENDING':
                next_state,due=payment_watch.begin(db,case,now,sys.modules[__name__])
            elif case.get('offline_intent')=='status' and case['agent_mode']=='deterministic':
                next_state, due = 'INFORMATION_PROVIDED', None
                event(db,cid,'Status-only request completed','Structured offline intent: information only. Payment status: '+order['payment_status']+'. No refund requested.')
            elif existing:
                next_state = 'REFUND_PENDING' if existing['status'] != 'CREDITED' else 'RESOLVED'
                db.execute('UPDATE cases SET refund_ref=? WHERE id=?',(existing['ref'],cid))
                event(db,cid,'Existing refund found','Tracking the existing refund. No second refund was created.')
                if next_state == 'RESOLVED': due = None
            elif order['payment_status'] != 'SUCCESS':
                next_state, due = 'NEEDS_HUMAN', None
                event(db,cid,'Payment needs reconciliation','No successful payment is confirmed. No refund submitted. A specialist must review the reported debit.','review')
            elif order['fulfillment'] != 'CANCELLED':
                next_state, due = 'NEEDS_HUMAN', None
                event(db,cid,'Fulfillment needs review','The order is fulfilled. Automatic refund is not permitted.','review')
            elif order['amount'] > 100000 and not case['approved']:
                next_state, due = 'NEEDS_HUMAN', None
                event(db,cid,'Approval requested','The refund exceeds the sample merchant’s ₹1,000 automatic limit. Review the evidence to continue.','review')
            else:
                event(db,cid,'Refund policy passed','Successful payment, cancelled order, and no existing refund. Refund amount is within the approved policy.')
                next_state = 'SUBMIT_REFUND'
        elif state == 'SUBMIT_REFUND':
            # Revalidate mutable business data at the moment of execution.
            if rescue.fence(db,case) or order['payment_status'] != 'SUCCESS' or order['fulfillment'] != 'CANCELLED' or (order['amount'] > 100000 and not case['approved']):
                next_state, due = 'NEEDS_HUMAN', None
                event(db,cid,'Execution paused','Eligibility changed before execution. Human review is required.','review')
            else:
                next_state, due = reliability.submit(db,case,order,now,step)
        elif state == 'RETRY_PENDING':
            next_state, due = reliability.retry(db,case,now,step)
        elif state == 'REFUND_PENDING':
            if reliability.consume(db,cid,'delay'):
                db.execute("UPDATE recovery SET resume_state='REFUND_PENDING' WHERE case_id=?",(cid,))
                event(db,cid,'Status update delayed','SIMULATION: the provider status is delayed. Scheduling a follow-up for the same refund.','recovery')
                next_state, due = 'RETRY_PENDING', now+max(step*2,.01)
            else:
                reliability.apply_status(db,case,cid+'-processing','PROCESSING',now,step)
                event(db,cid,'Refund processing','Simulated provider status: processing. Credit is not yet confirmed.')
                next_state = 'AWAITING_CREDIT'
        elif state == 'AWAITING_CREDIT':
            reliability.apply_status(db,case,cid+'-credited','CREDITED',now,step)
            event(db,cid,'Simulated credit confirmed','Demo bank simulator confirms credit. No real funds moved.')
            event(db,cid,'Support case resolved','Ticket updated; simulated outcome recorded.')
            next_state, due = 'RESOLVED', None
        else:
            due = None
        db.execute('UPDATE cases SET state=?,updated=?,due=? WHERE id=?',(next_state,now,due,cid))
    commerce.notifications(db,now,step)
    checkout_hub.tick(db,now)

async def worker():
    while True:
        try:
            await asyncio.to_thread(advance_once)
        except Exception:
            import logging
            logging.exception('Workflow tick failed; durable jobs will retry')
        await asyncio.sleep(.5)

@asynccontextmanager
async def lifespan(app):
    initialize()
    task = asyncio.create_task(worker())
    model_task = asyncio.create_task(ai_worker())
    yield
    task.cancel()
    model_task.cancel()
    for running in (task, model_task):
        try: await running
        except asyncio.CancelledError: pass

app = FastAPI(title='Scan-to-Resolve demo API',lifespan=lifespan)

class CardQuery(BaseModel):
    last4: str = Field(pattern=r'^\d{4}$')
class UpiQuery(BaseModel):
    upi_id: str = Field(min_length=5,max_length=254)

def normalize_upi(value):
    value=value.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,127}@[a-z0-9][a-z0-9.-]{1,63}',value):
        raise HTTPException(422,'Enter a UPI ID like name@bank. Do not enter a PIN or phone number alone.')
    return value

@app.post('/api/upi-candidates')
def upi_candidates(body: UpiQuery):
    upi=normalize_upi(body.upi_id)
    with connection() as db:
        return {'orders':[dict(r) for r in db.execute('SELECT o.* FROM orders o JOIN order_upi u ON u.order_id=o.id WHERE o.customer=? AND u.upi_id=?',(auth.customer_id(),upi))],'verified':False}

@app.post('/api/demo-upi-orders')
def create_upi_demo(body: UpiQuery):
    upi=normalize_upi(body.upi_id)
    oid='UPI-DEMO-'+auth.customer_id()+'-'+hashlib.sha256(upi.encode()).hexdigest()[:16]
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?,?,?)',(oid,auth.customer_id(),'UPI Test Store',79900,'','SUCCESS','CANCELLED','SIMULATED UPI purchase — no bank connection or real payment'))
        db.execute('INSERT OR IGNORE INTO order_upi VALUES (?,?)',(oid,upi))
        return {'order':order_for(db,oid),'simulated':True,'verified':False}

class CreateCase(BaseModel):
    order_id: str = Field(max_length=100)
    complaint: str = Field(min_length=3,max_length=2000)
    agent_mode: Literal['ai','openai','deterministic'] | None = None
    offline_intent: Literal['refund','status'] = 'refund'
class Review(BaseModel):
    decision: str = Field(pattern='^(approve|reject)$')

@app.get('/api/health')
def health(): return {'ai_live_enabled':ai_agent.setting('AI_LIVE_ENABLED','true').lower() in {'true','1','yes'},'status':'ok','provider':'simulated','teammate':ai_agent.provider_name(),'ai_provider':ai_agent.provider_name(),'key_env':ai_agent.key_name(),'ai_configured':ai_agent.configured(),'model':ai_agent.model_name(),'auth':'server-session'}

@app.post('/api/card-candidates')
def candidates(body: CardQuery):
    with connection() as db:
        return {'orders':[dict(r) for r in db.execute('SELECT * FROM orders WHERE customer=? AND last4=?',(auth.customer_id(),body.last4))]}

@app.post('/api/cases')
def create_case(body: CreateCase):
    mode = body.agent_mode or 'deterministic'
    if mode!='deterministic' and ai_agent.setting('AI_LIVE_ENABLED','false').lower() not in {'true','1','yes'}:raise HTTPException(503,'Live AI is paused. Use demo mode.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        selected_order=order_for(db,body.order_id)
        existing = db.execute('SELECT id FROM cases WHERE order_id=?',(body.order_id,)).fetchone()
        if existing:
            reliability.recovery_row(db,existing['id'])
            db.execute('UPDATE recovery SET duplicates=duplicates+1 WHERE case_id=?',(existing['id'],))
            event(db,existing['id'],'Duplicate request prevented','Reused the existing case. No second refund action was created.','recovery')
            return snapshot(db,existing['id'])
        cid = 'CASE-'+uuid.uuid4().hex[:8].upper()
        now = time.time()
        complaint = ai_agent.redact(body.complaint.strip())
        state = 'OPEN' if mode == 'deterministic' else ('AI_QUEUED' if ai_agent.configured() else 'AI_SETUP_REQUIRED')
        db.execute('INSERT INTO cases(id,order_id,complaint,state,created,updated,due,agent_mode) VALUES (?,?,?,?,?,?,?,?)',(cid,body.order_id,complaint,state,now,now,now+STEPS_SECONDS if state!='AI_SETUP_REQUIRED' else None,mode))
        db.execute('UPDATE cases SET offline_intent=? WHERE id=?',(body.offline_intent,cid))
        if mode=='deterministic' and selected_order['payment_status']=='PENDING':
            state,due=payment_watch.begin(db,{'id':cid},now,sys.modules[__name__])
            db.execute('UPDATE cases SET state=?,due=? WHERE id=?',(state,due,cid))
        event(db,cid,'Case opened',f'{ai_agent.provider_name().title()} will choose investigative tools. Payments stay simulated.' if mode!='deterministic' else 'Explicit offline demo: rules select the workflow; no model is called.')
        return snapshot(db,cid)

@app.get('/api/cases')
def list_cases():
    with connection() as db:
        scope,params=auth.order_scope('o')
        return {'cases':[snapshot(db,r['id']) for r in db.execute('SELECT c.id FROM cases c JOIN orders o ON o.id=c.order_id WHERE '+scope+' ORDER BY c.created DESC',params).fetchall()]}

@app.get('/api/cases/{case_id}')
def get_case(case_id: str):
    with connection() as db: return snapshot(db,case_id)

@app.post('/api/cases/{case_id}/review')
def review_case(case_id: str, body: Review):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        case = snapshot(db,case_id)
        if case.get('rescue'):
            rescue.review(db,case,body.decision,time.time(),STEPS_SECONDS)
            return snapshot(db,case_id)
        if case['state'] != 'NEEDS_HUMAN': raise HTTPException(409,'This case is not awaiting review')
        if body.decision == 'approve':
            order = case['order']
            if order['payment_status'] != 'SUCCESS' or order['fulfillment'] != 'CANCELLED':
                raise HTTPException(409,'Cannot approve: a successful payment and cancelled order are required')
            db.execute("UPDATE cases SET approved=1,state='CHECK_POLICY',due=?,updated=? WHERE id=?",(time.time()+STEPS_SECONDS,time.time(),case_id))
            event(db,case_id,'Human approval recorded','Demo reviewer approved the refund. The teammate will recheck eligibility and resume.')
        else:
            db.execute("UPDATE cases SET state='CLOSED_REVIEWED',due=NULL,updated=? WHERE id=?",(time.time(),case_id))
            event(db,case_id,'Review completed','Demo reviewer closed the case without a refund.','review')
        return snapshot(db,case_id)

# Model tools are bound to one authorized case. The model never supplies an
# order ID, merchant ID, customer ID, refund amount, or approval flag.
REQUIRED_EVIDENCE = {'get_order','get_payment_status','get_existing_refunds','evaluate_refund_policy'}

def policy_for(db, case):
    order = order_for(db,case['order_id'])
    refund = db.execute('SELECT * FROM refunds WHERE order_id=?',(order['id'],)).fetchone()
    blocked=rescue.fence(db,case)
    if blocked: decision, reason='blocked',blocked
    elif refund: decision, reason = 'existing_refund', 'Track the existing refund; a second refund is forbidden.'
    elif order['payment_status'] == 'PENDING': decision, reason = 'blocked', 'Payment is awaiting confirmation; automatic checks must finish before a refund.'
    elif order['payment_status'] != 'SUCCESS': decision, reason = 'blocked', 'A successful payment is not confirmed; no automatic refund is allowed.'
    elif order['fulfillment'] != 'CANCELLED': decision, reason = 'blocked', 'Only cancelled orders qualify for this sample automatic refund policy.'
    elif order['amount'] > 100000 and not case['approved']: decision, reason = 'approval_required', 'Amount exceeds the sample merchant’s ₹1,000 automatic limit.'
    else: decision, reason = 'eligible', 'Successful payment, cancelled order, no duplicate, amount permitted.'
    return {'decision':decision,'reason':reason,'amount_paise':order['amount'],'automatic_limit_paise':100000,'source':'server_policy_v1','simulated':True}

async def investigate(case_id):
    seen = set()
    with connection() as db:
        case = snapshot(db,case_id)
        context = {'case_id':case_id,'complaint':case['complaint'], 'customer_replies':[m['content'] for m in case['messages']], 'scope':'Selected merchant order only. All payment systems are mocked.'}

    async def usage(data):
        with connection() as db:
            db.execute('UPDATE cases SET ai_tokens=ai_tokens+? WHERE id=?',(int(data.get('total_tokens',0)),case_id))

    async def execute(name, args):
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = snapshot(db,case_id)
            order = current['order']
            allowed = {t['name'] for t in ai_agent.TOOLS}
            if name not in allowed: return {'error':'Unknown tool'}
            if name not in {'escalate_case','ask_customer','finish_case_brief'} and args:
                return {'error':'This tool takes no arguments; scope is server-bound.'}
            if name in {'get_checkout','get_inventory','request_order_restore'}:
                context=rescue.details(db,case_id)
                if not context:
                    result={'error':'This case has no original merchant checkout. Do not reconstruct it from a receipt.'}
                elif name=='get_checkout':
                    result={k:context[k] for k in ('checkout','intent','today_only','merchant_order','payments','pending_intent')}
                elif name=='get_inventory':
                    result={k:context[k] for k in ('inventory','reservation','today_only')}
                elif context['intent']!='restore' or not {'get_checkout','get_inventory'}.issubset(seen):
                    result={'error':'Read checkout and inventory evidence and respect the explicit restore preference.'}
                elif current['state']!='AI_RUNNING':
                    result={'error':'This case already has a workflow or awaits review.'}
                elif current['refund'] or db.execute('SELECT 1 FROM provider_refunds WHERE order_id=?',(current['order_id'],)).fetchone():
                    result={'error':'Refund exists. Restoration blocked.'}
                else:
                    db.execute("UPDATE cases SET state='RESCUE_VERIFY',due=?,updated=? WHERE id=?",(time.time()+STEPS_SECONDS,time.time(),case_id))
                    result={'action':'restoration_queued','simulated':True,'note':'Not restored yet; backend will verify stock, delivery, and the resulting merchant order.'}
            elif name == 'get_order':
                result = {key:order[key] for key in ('id','merchant','amount','fulfillment','description')}
            elif name == 'get_payment_status':
                result = {'status':order['payment_status'],'amount_paise':order['amount'],'source':'mock_paytm','simulated':True}
            elif name == 'get_existing_refunds':
                result = {'refund':current['refund'],'simulated':True}
            elif name == 'evaluate_refund_policy':
                result = policy_for(db,current)
            elif name in {'request_refund','track_refund'}:
                if not REQUIRED_EVIDENCE.issubset(seen):
                    result = {'error':'Read all four evidence tools before requesting an action.','missing_tools':sorted(REQUIRED_EVIDENCE-seen)}
                elif current['state'] not in {'AI_RUNNING','SUBMIT_REFUND','REFUND_PENDING','AWAITING_CREDIT','RESOLVED'}:
                    result = {'error':'Case is paused or under human review. No action allowed.'}
                else:
                    policy = policy_for(db,current)
                    if current['refund']:
                        state = 'RESOLVED' if current['refund']['status']=='CREDITED' else ('AWAITING_CREDIT' if current['refund']['status']=='PROCESSING' else 'REFUND_PENDING')
                        db.execute('UPDATE cases SET state=?,due=?,updated=? WHERE id=?',(state,None if state=='RESOLVED' else time.time()+STEPS_SECONDS,time.time(),case_id))
                        result = {'action':'tracking_existing_refund','refund':current['refund'],'simulated':True}
                    elif name == 'track_refund':
                        result = {'error':'No refund exists. Investigate before taking another action.'}
                    elif policy['decision'] != 'eligible':
                        result = {'action':'blocked','policy':policy}
                    else:
                        db.execute("UPDATE cases SET state='SUBMIT_REFUND',due=?,updated=? WHERE id=?",(time.time()+STEPS_SECONDS,time.time(),case_id))
                        result = {'action':'refund_queued','amount_paise':order['amount'],'simulated':True,'note':'Not submitted or credited yet; tracking continues asynchronously.'}
            elif name == 'escalate_case':
                reason=args.get('reason')
                if set(args)!={'reason'} or not isinstance(reason,str) or not 3<=len(reason)<=800:
                    return {'error':'Provide a reason between 3 and 800 characters.'}
                if current['state'] in {'SUBMIT_REFUND','REFUND_PENDING','AWAITING_CREDIT','RESOLVED'}:
                    result={'error':'A refund is already underway or completed; preserve tracking.'}
                else:
                    db.execute("UPDATE cases SET state='NEEDS_HUMAN',due=NULL,updated=? WHERE id=?",(time.time(),case_id))
                    event(db,case_id,'AI requested human review',ai_agent.redact(reason),'review')
                    result={'action':'human_review_requested','reason':ai_agent.redact(reason)}
            elif name == 'ask_customer':
                question=args.get('question')
                if set(args)!={'question'} or not isinstance(question,str) or not 3<=len(question)<=500:
                    return {'error':'Provide one question between 3 and 500 characters.'}
                if current['state'] != 'AI_RUNNING': return {'error':'Cannot interrupt an action already underway.'}
                db.execute("UPDATE cases SET state='WAITING_CUSTOMER',due=NULL,updated=? WHERE id=?",(time.time(),case_id))
                event(db,case_id,'A quick clarification',ai_agent.redact(question),'review')
                result={'action':'awaiting_customer','question':ai_agent.redact(question)}
            else:
                try: brief=ai_agent.Brief.model_validate(args)
                except ValueError: return {'error':'Invalid brief. Follow the schema and length limits.'}
                evidence = {t['evidence_id']:t for t in current['tools']}
                required={'get_checkout','get_inventory'} if brief.recommendation=='restore_order' else REQUIRED_EVIDENCE
                if not required.issubset(seen): return {'error':'Gather the required evidence tools before finishing.'}
                if any(e not in evidence for e in brief.evidence_ids): return {'error':'Cite only actual evidence IDs returned by tools.'}
                expected = {'RESCUE_VERIFY':'restore_order','RESCUE_RESERVE':'restore_order','RESCUE_CREATE':'restore_order','RESCUE_RECONCILE':'restore_order','RESCUE_VERIFY_ORDER':'restore_order','ORDER_RESTORED':'restore_order','SUBMIT_REFUND':'refund','REFUND_PENDING':'track_refund','AWAITING_CREDIT':'track_refund','RESOLVED':'track_refund','NEEDS_HUMAN':'human_review','WAITING_CUSTOMER':'clarification','AI_RUNNING':'information'}
                if brief.recommendation != expected.get(current['state']):
                    # The worker can advance a queued refund while the model prepares its brief.
                    if not (brief.recommendation=='refund' and current['state'] in {'REFUND_PENDING','AWAITING_CREDIT','RESOLVED'}):
                        return {'error':'Recommendation must match the action actually executed.','expected':expected.get(current['state'])}
                payload=brief.model_dump()
                for field in ('intent','summary','next_step'): payload[field]=ai_agent.redact(payload[field])
                payload['model']=ai_agent.model_name()
                payload['provider']=ai_agent.provider_name()
                payload['generated_at']=time.time()
                db.execute('UPDATE cases SET brief=?,ai_error=NULL,updated=? WHERE id=?',(json.dumps(payload),time.time(),case_id))
                if current['state']=='AI_RUNNING':
                    db.execute("UPDATE cases SET state='INFORMATION_PROVIDED',due=NULL WHERE id=?",(case_id,))
                event(db,case_id,'Evidence brief ready','AI summary saved with references to executed tool results.')
                return {'saved':True}
            cursor=db.execute('INSERT INTO tool_calls(case_id,name,result,created) VALUES (?,?,?,?)',(case_id,name,json.dumps(result),time.time()))
            evidence_id='E'+str(cursor.lastrowid)
            event(db,case_id,'AI tool · '+name, 'Evidence '+evidence_id+' recorded. '+str(result.get('action') or result.get('decision') or result.get('status') or 'Read completed.'))
            seen.add(name)
            return {**result,'evidence_id':evidence_id}

    try:
        await ai_agent.run_agent(context,execute,usage)
    except asyncio.CancelledError:
        with connection() as db:
            db.execute("UPDATE cases SET state='AI_ERROR',due=NULL,ai_error='Investigation interrupted. Retry to continue.' WHERE id=? AND state='AI_RUNNING'",(case_id,))
        raise
    except Exception as exc:
        # Do not expose raw provider responses or secrets in the browser/log.
        message = str(exc) if isinstance(exc,ai_agent.AgentUnavailable) else 'AI investigation could not complete. Retry or request human review.'
        with connection() as db:
            db.execute('UPDATE cases SET ai_error=?,updated=? WHERE id=?',(message,time.time(),case_id))
            db.execute("UPDATE cases SET state='AI_ERROR',due=NULL WHERE id=? AND state='AI_RUNNING'",(case_id,))
            event(db,case_id,'AI needs attention',message,'review')

async def ai_worker():
    while True:
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE cases SET state='AI_ERROR',due=NULL,ai_error='Previous investigation was interrupted. Retry to continue.' WHERE state='AI_RUNNING' AND due<=?",(time.time(),))
            rows=db.execute("SELECT id FROM cases WHERE state='AI_QUEUED' ORDER BY created LIMIT 3").fetchall()
            ids=[r['id'] for r in rows]
            for cid in ids:
                db.execute("UPDATE cases SET state='AI_RUNNING',due=?,ai_error=NULL WHERE id=?",(time.time()+600,cid))
                event(db,cid,'AI investigation started','The model is choosing tools for this complaint. Backend policy controls every action.')
        if ids: await asyncio.gather(*(investigate(cid) for cid in ids))
        await asyncio.sleep(.5)

class CustomerReply(BaseModel):
    message: str = Field(min_length=3,max_length=2000)

@app.post('/api/cases/{case_id}/reply')
def reply_to_case(case_id: str, body: CustomerReply):
    if ai_agent.setting('AI_LIVE_ENABLED','false').lower() not in {'true','1','yes'}:raise HTTPException(503,'Live AI is paused. No model call was made.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        case=snapshot(db,case_id)
        if case['state']!='WAITING_CUSTOMER': raise HTTPException(409,'This case is not awaiting a customer reply')
        if not ai_agent.configured(): raise HTTPException(503,f'Configure {ai_agent.key_name()} locally before continuing.')
        db.execute('INSERT INTO messages(case_id,content,created) VALUES (?,?,?)',(case_id,ai_agent.redact(body.message.strip()),time.time()))
        db.execute("UPDATE cases SET state='AI_QUEUED',due=?,brief=NULL WHERE id=?",(time.time(),case_id))
        event(db,case_id,'Customer clarification received','The teammate will reconsider the case with your reply.')
        return snapshot(db,case_id)

@app.post('/api/cases/{case_id}/retry-ai')
def retry_ai(case_id: str):
    if ai_agent.setting('AI_LIVE_ENABLED','false').lower() not in {'true','1','yes'}:raise HTTPException(503,'Live AI is paused. No model call was made.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        case=snapshot(db,case_id)
        if case['state'] not in {'AI_SETUP_REQUIRED','AI_ERROR'}: raise HTTPException(409,'This case is not awaiting an AI retry')
        if not ai_agent.configured(): raise HTTPException(503,f'Add {ai_agent.key_name()} to the project .env file, then retry.')
        db.execute("UPDATE cases SET state='AI_QUEUED',due=?,ai_error=NULL WHERE id=?",(time.time(),case_id))
        return snapshot(db,case_id)

@app.post('/api/cases/{case_id}/handoff')
def manual_handoff(case_id: str):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        case=snapshot(db,case_id)
        if case['state'] not in {'AI_SETUP_REQUIRED','AI_ERROR','WAITING_CUSTOMER','INFORMATION_PROVIDED'}: raise HTTPException(409,'Case cannot be handed off from its current state')
        db.execute("UPDATE cases SET state='NEEDS_HUMAN',due=NULL WHERE id=?",(case_id,))
        event(db,case_id,'Human review requested','Customer requested a specialist. No new refund was submitted.','review')
        return snapshot(db,case_id)

@app.post('/api/cases/{case_id}/enable-ai')
def enable_ai(case_id: str):
    if ai_agent.setting('AI_LIVE_ENABLED','false').lower() not in {'true','1','yes'}:raise HTTPException(503,'Live AI is paused. No model call was made.')
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        case=snapshot(db,case_id)
        if case['agent_mode']!='deterministic' or case['state'] not in {'RESOLVED','NEEDS_HUMAN'}:
            raise HTTPException(409,'Only settled or review-required offline cases can be investigated with AI.')
        state='AI_QUEUED' if ai_agent.configured() else 'AI_SETUP_REQUIRED'
        db.execute('UPDATE cases SET state=?,agent_mode=\'openai\',due=?,brief=NULL WHERE id=?',(state,time.time() if state=='AI_QUEUED' else None,case_id))
        event(db,case_id,'Real AI requested','Existing refunds are preserved. The model will investigate this case using live tool calls against mocked data.')
        return snapshot(db,case_id)

@app.post('/api/demo-orders')
def create_scanned_card_demo(body: CardQuery):
    """Explicit synthetic fixture, not a bank/card linking operation."""
    with connection() as db:
        oid='DEMO-'+auth.customer_id()+'-'+body.last4
        db.execute('INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?,?,?)',
            (oid,auth.customer_id(),'Scan Test Store',79900,body.last4,'SUCCESS','CANCELLED',
             'SIMULATED purchase for your card-scan test - no real payment'))
        return {'order':order_for(db,oid),'simulated':True}

try:
    from . import operations
except ImportError:
    import operations
import sys
operations.install(app,sys.modules[__name__])

rescue.install(app,sys.modules[__name__])
rescue.install_scorecard(app,sys.modules[__name__])

commerce.install(app,sys.modules[__name__])

tap.install(app,sys.modules[__name__])

auth.install(app,sys.modules[__name__])

paytm_mcp.install(app,sys.modules[__name__])

statements.install(app,sys.modules[__name__])

purchases.install(app,sys.modules[__name__])

payment_watch.install(app,sys.modules[__name__])

checkout_hub.install(app,sys.modules[__name__])

try:
    from . import live_demo
except ImportError:
    import live_demo
live_demo.install(app,sys.modules[__name__])
