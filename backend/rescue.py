"""Payment-event driven order rescue. All merchant and payment systems are simulated.

Every resource mutation runs in the caller's SQLite write transaction. The
merchant ledger is distinct from local acknowledgements to model lost responses.
"""
import json
import sqlite3
import time
import uuid
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, Field
try:
    from . import commerce, auth
except ImportError:
    import commerce
    import auth

INTENTS={'restore','refund','status','clarify'}
TERMINAL={'ORDER_RESTORED','RESOLVED','INFORMATION_PROVIDED','CLOSED_REVIEWED'}


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS rescue_settings (id INTEGER PRIMARY KEY, grace_seconds INTEGER);
    INSERT OR IGNORE INTO rescue_settings VALUES (1,6);
    CREATE TABLE IF NOT EXISTS rescue_inventory (sku TEXT PRIMARY KEY, title TEXT, available INTEGER CHECK(available>=0), delivery_today INTEGER);
    CREATE TABLE IF NOT EXISTS rescue_checkouts (id TEXT PRIMARY KEY, customer TEXT, merchant TEXT, sku TEXT, quantity INTEGER, unit_price INTEGER, amount INTEGER, customer_data TEXT, desired TEXT, today_only INTEGER, created REAL);
    CREATE TABLE IF NOT EXISTS rescue_payments (id TEXT PRIMARY KEY, checkout_id TEXT, amount INTEGER, status TEXT, paid_at REAL);
    CREATE TABLE IF NOT EXISTS rescue_event_receipts (id TEXT PRIMARY KEY, payment_id TEXT, received REAL);
    CREATE TABLE IF NOT EXISTS merchant_orders (checkout_id TEXT PRIMARY KEY, id TEXT UNIQUE, request_key TEXT UNIQUE, amount INTEGER, sku TEXT, quantity INTEGER, status TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS inventory_reservations (checkout_id TEXT PRIMARY KEY, sku TEXT, quantity INTEGER, status TEXT);
    CREATE TABLE IF NOT EXISTS rescue_links (checkout_id TEXT PRIMARY KEY, case_id TEXT UNIQUE, intent TEXT, today_only INTEGER, pending_intent TEXT, fault TEXT, fault_consumed INTEGER DEFAULT 0, order_ref TEXT, reconcile_attempts INTEGER DEFAULT 0, human_decisions INTEGER DEFAULT 0, recoveries INTEGER DEFAULT 0, resume_state TEXT);
    CREATE TABLE IF NOT EXISTS rescue_runs (id TEXT PRIMARY KEY, created REAL, report TEXT);
    ''')


def log(db,cid,title,detail,kind='done'):
    db.execute('INSERT INTO events(case_id,title,detail,kind,created) VALUES (?,?,?,?,?)',(cid,title,detail,kind,time.time()))


def checkout(db,checkout_id,customer=None):
    row=db.execute('SELECT * FROM rescue_checkouts WHERE id=?',(checkout_id,)).fetchone()
    if not row or (customer is not None and row['customer']!=customer) or not auth.can_access_checkout(db,row):raise HTTPException(404,'Checkout not found')
    return dict(row)


def ingest(db,event_id,payment_id,checkout_id,amount,status,now):
    co=checkout(db,checkout_id)
    if amount!=co['amount']:raise HTTPException(409,'Receipt amount does not match the saved checkout. Investigate before recovery.')
    if db.execute('SELECT 1 FROM rescue_event_receipts WHERE id=?',(event_id,)).fetchone():return False
    existing=db.execute('SELECT * FROM rescue_payments WHERE id=?',(payment_id,)).fetchone()
    if existing and (existing['checkout_id']!=checkout_id or existing['amount']!=amount):raise HTTPException(409,'Payment identity conflicts with an existing receipt')
    db.execute('INSERT INTO rescue_event_receipts VALUES (?,?,?)',(event_id,payment_id,now))
    if not existing:db.execute('INSERT INTO rescue_payments VALUES (?,?,?,?,?)',(payment_id,checkout_id,amount,status,now))
    elif existing['status']!='SUCCESS':db.execute('UPDATE rescue_payments SET status=?,paid_at=? WHERE id=?',(status,now,payment_id))
    return True


def reserve(db,co):
    existing=db.execute('SELECT * FROM inventory_reservations WHERE checkout_id=?',(co['id'],)).fetchone()
    if existing and existing['status'] in {'HELD','COMMITTED'}:return True
    cursor=db.execute('UPDATE rescue_inventory SET available=available-? WHERE sku=? AND available>=?',(co['quantity'],co['sku'],co['quantity']))
    if cursor.rowcount!=1:return False
    db.execute('INSERT INTO inventory_reservations VALUES (?,?,?,?) ON CONFLICT(checkout_id) DO UPDATE SET sku=excluded.sku,quantity=excluded.quantity,status=excluded.status',(co['id'],co['sku'],co['quantity'],'HELD'))
    return True


def release(db,co,allow_committed=False):
    commerce.release_delivery(db,co['id'],allow_committed)
    allowed={'HELD','COMMITTED'} if allow_committed else {'HELD'}
    row=db.execute('SELECT * FROM inventory_reservations WHERE checkout_id=?',(co['id'],)).fetchone()
    if row and row['status'] in allowed:
        db.execute('UPDATE rescue_inventory SET available=available+? WHERE sku=?',(row['quantity'],row['sku']))
        db.execute("UPDATE inventory_reservations SET status='RELEASED' WHERE checkout_id=?",(co['id'],))
        return True
    return False


def create_merchant(db,co,now):
    existing=db.execute('SELECT * FROM merchant_orders WHERE checkout_id=?',(co['id'],)).fetchone()
    if existing:return dict(existing)
    if not reserve(db,co):raise HTTPException(409,'Stock is no longer available')
    if not commerce.commit_delivery(db,co,now):raise HTTPException(409,'No feasible delivery plan or remaining carrier capacity')
    ref='MOCK-ORDER-'+uuid.uuid4().hex[:8].upper()
    db.execute('INSERT INTO merchant_orders VALUES (?,?,?,?,?,?,?,?)',(co['id'],ref,'restore:'+co['id'],co['amount'],co['sku'],co['quantity'],'CONFIRMED',now))
    db.execute("UPDATE inventory_reservations SET status='COMMITTED' WHERE checkout_id=?",(co['id'],))
    return dict(db.execute('SELECT * FROM merchant_orders WHERE checkout_id=?',(co['id'],)).fetchone())


def detect(db,now,step,engine):
    grace=db.execute('SELECT grace_seconds FROM rescue_settings WHERE id=1').fetchone()[0]
    rows=db.execute("SELECT c.* FROM rescue_checkouts c WHERE EXISTS (SELECT 1 FROM rescue_payments p WHERE p.checkout_id=c.id AND p.status='SUCCESS' AND p.paid_at<=?) AND NOT EXISTS (SELECT 1 FROM rescue_links l WHERE l.checkout_id=c.id) AND NOT EXISTS (SELECT 1 FROM merchant_orders m WHERE m.checkout_id=c.id AND m.status='CONFIRMED')",(now-grace,)).fetchall()
    for raw in rows:
        co=dict(raw);cid='RESCUE-'+uuid.uuid4().hex[:8].upper();oid='CHECKOUT-'+co['id']
        db.execute('INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?,?,?)',(oid,co['customer'],co['merchant'],co['amount'],'0000','SUCCESS','MISSING','Paid checkout awaiting merchant confirmation'))
        db.execute('INSERT INTO cases(id,order_id,complaint,state,created,updated,due,agent_mode,offline_intent) VALUES (?,?,?,?,?,?,?,?,?)',(cid,oid,'Payment-event recovery. Customer preference: '+co['desired'],'RESCUE_VERIFY',now,now,now+step,'deterministic',co['desired']))
        fault=co['customer_data'] and json.loads(co['customer_data']).get('demo_fault')
        db.execute('INSERT INTO rescue_links(checkout_id,case_id,intent,today_only,fault) VALUES (?,?,?,?,?)',(co['id'],cid,co['desired'],co['today_only'],fault))
        log(db,cid,'Broken purchase detected','Successful simulated UPI payment has no merchant order after the configured grace period. Recovery case opened automatically.','simulation')


def fence(db,case):
    link=db.execute('SELECT * FROM rescue_links WHERE case_id=?',(case['id'],)).fetchone()
    if not link:return None
    merchant=db.execute('SELECT * FROM merchant_orders WHERE checkout_id=?',(link['checkout_id'],)).fetchone()
    if link['intent']!='refund':return 'The customer has not selected the refund path.'
    if merchant and merchant['status']=='CONFIRMED':return 'A confirmed merchant order must be cancelled explicitly before a refund.'
    if link['resume_state']=='RESCUE_RECONCILE':return 'Merchant creation is uncertain. Reconcile before issuing a refund.'
    return None


def transition(db,case,state,due,now):
    db.execute('UPDATE cases SET state=?,due=?,updated=? WHERE id=?',(state,due,now,case['id']))


def advance(db,case,now,step,engine):
    row=db.execute('SELECT * FROM rescue_links WHERE case_id=?',(case['id'],)).fetchone()
    if not row or not case['state'].startswith('RESCUE_'):return False
    link=dict(row);co=checkout(db,link['checkout_id']);cid=case['id'];state=case['state'];next_state=state;due=now+step
    merchant=db.execute('SELECT * FROM merchant_orders WHERE checkout_id=?',(co['id'],)).fetchone()
    payments=db.execute("SELECT * FROM rescue_payments WHERE checkout_id=? AND status='SUCCESS'",(co['id'],)).fetchall()
    if state=='RESCUE_VERIFY':
        log(db,cid,'Payment and checkout verified',f"Saved checkout has {co['quantity']} item(s), fixed total INR {co['amount']/100:.2f}, and synthetic delivery details. Successful receipts: {len(payments)}.")
        if len(payments)!=1:
            next_state,due='NEEDS_HUMAN',None
            log(db,cid,'Multiple payment records need review','Do not create an order or refund until the payment records are reconciled.','review')
        elif link['intent']=='status':
            next_state,due='INFORMATION_PROVIDED',None
            log(db,cid,'Information-only outcome verified','Payment succeeded. Merchant order '+('exists.' if merchant else 'is missing.')+' No new order or refund requested.')
        elif link['intent']=='clarify':
            next_state,due='WAITING_CUSTOMER',None
            log(db,cid,'Customer preference needed','Would you like the original order restored, or a refund? No resources have been changed.','review')
        elif link['intent']=='refund':
            blocked=fence(db,case)
            if blocked:
                next_state,due='NEEDS_HUMAN',None;log(db,cid,'Refund path blocked',blocked,'review')
            else:
                if release(db,co):log(db,cid,'Inventory released','Unused reservation released before the refund path.')
                db.execute("UPDATE orders SET fulfillment='CANCELLED' WHERE id=?",(case['order_id'],))
                next_state='CHECK_POLICY';log(db,cid,'Refund path selected','Customer requested a refund. Merchant restoration is fenced off; backend refund policy runs next.')
        elif merchant and merchant['status']=='CONFIRMED':
            db.execute('UPDATE rescue_links SET order_ref=? WHERE case_id=?',(merchant['id'],cid))
            db.execute("UPDATE orders SET fulfillment='CONFIRMED' WHERE id=?",(case['order_id'],))
            next_state,due='ORDER_RESTORED',None
            log(db,cid,'Existing merchant order verified','Order '+merchant['id']+' found. No duplicate order or stock reservation created.')
        else:
            inv=db.execute('SELECT * FROM rescue_inventory WHERE sku=?',(co['sku'],)).fetchone()
            if not inv or inv['available']<co['quantity'] or (link['today_only'] and not inv['delivery_today']):
                next_state,due='WAITING_CUSTOMER',None
                log(db,cid,'Restoration needs a different outcome','Stock or today-only delivery is unavailable. Choose a refund or leave the case open; no order was created.','review')
            else:next_state='RESCUE_RESERVE';log(db,cid,'Restoration feasible','Stock and delivery constraints checked against the original checkout. No new charge is needed.')
    elif state=='RESCUE_RESERVE':
        if link['intent']!='restore':next_state='RESCUE_VERIFY'
        elif reserve(db,co):
            next_state='RESCUE_CREATE';log(db,cid,'Stock reserved','Inventory decremented and a durable HELD reservation saved for this checkout.')
            if link['fault']=='interruption' and not link['fault_consumed']:
                db.execute("UPDATE rescue_links SET fault_consumed=1,resume_state='RESCUE_CREATE' WHERE case_id=?",(cid,))
                next_state,due='RESCUE_PAUSED',None;log(db,cid,'Rescue interruption simulated','Reservation and next step persisted. Resume the checkpoint or abandon to release inventory.','simulation')
        else:next_state,due='WAITING_CUSTOMER',None;log(db,cid,'Stock changed before reservation','No inventory reserved. Choose a refund instead.','review')
    elif state=='RESCUE_CREATE':
        inventory=db.execute('SELECT * FROM rescue_inventory WHERE sku=?',(co['sku'],)).fetchone()
        if link['today_only'] and not inventory['delivery_today']:
            release(db,co);next_state,due='WAITING_CUSTOMER',None
            log(db,cid,'Delivery constraint changed','Today-only delivery is no longer feasible. Released unused stock; no merchant order was created.','review')
        elif link['intent']!='restore':next_state='RESCUE_VERIFY'
        elif db.execute('SELECT 1 FROM refunds WHERE order_id=?',(case['order_id'],)).fetchone() or db.execute('SELECT 1 FROM provider_refunds WHERE order_id=?',(case['order_id'],)).fetchone():
            next_state,due='NEEDS_HUMAN',None;release(db,co);log(db,cid,'Order creation fenced','A refund exists. No order may be restored for the same payment.','review')
        elif link['fault']=='create_failed' and not link['fault_consumed']:
            db.execute('UPDATE rescue_links SET fault_consumed=1 WHERE case_id=?',(cid,));release(db,co)
            next_state,due='WAITING_CUSTOMER',None;log(db,cid,'Creation failed; stock compensated','Merchant rejected creation before acceptance. The reservation was released. Choose a refund or ask a human.','recovery')
        else:
            try:
                order=create_merchant(db,co,now)
            except HTTPException:
                release(db,co)
                transition(db,case,'WAITING_CUSTOMER',None,now)
                log(db,cid,'Delivery plan needs revision','Capacity, quote validity, or delivery constraints changed. Unused stock released; no merchant order created.','review')
                return True
            if link['fault'] in {'timeout','unknown'} and not link['fault_consumed']:
                db.execute("UPDATE rescue_links SET fault_consumed=1,resume_state='RESCUE_RECONCILE' WHERE case_id=?",(cid,))
                next_state='RESCUE_RECONCILE';due=now+max(step*2,.01)
                log(db,cid,'Merchant response timed out','SIMULATION: merchant creation succeeded but its acknowledgement was lost. Query the same operation key before any retry.','recovery')
            else:
                db.execute('UPDATE rescue_links SET order_ref=? WHERE case_id=?',(order['id'],cid));next_state='RESCUE_VERIFY_ORDER'
                log(db,cid,'Merchant record created',order['id']+' created using the original checkout, committed stock, and the existing payment. No new charge.')
    elif state=='RESCUE_RECONCILE':
        attempt=link['reconcile_attempts']+1
        db.execute('UPDATE rescue_links SET reconcile_attempts=? WHERE case_id=?',(attempt,cid))
        if merchant and link['fault']!='unknown':
            db.execute('UPDATE rescue_links SET order_ref=?,recoveries=recoveries+1,resume_state=NULL WHERE case_id=?',(merchant['id'],cid))
            next_state='NEEDS_HUMAN' if link['pending_intent'] and link['pending_intent']!='restore' else 'RESCUE_VERIFY_ORDER'
            if next_state=='NEEDS_HUMAN':
                due=None
                log(db,cid,'Cancellation decision required',f"INR {co['amount']/100:.2f} payment confirmed; merchant order exists; customer requested a refund. Approve explicit merchant cancellation and refund, or keep the order.",'review')
            log(db,cid,'Created order recovered',merchant['id']+' found by the stable operation key. Reused the existing order and committed reservation.','recovery')
        elif attempt>=3:
            next_state,due='NEEDS_HUMAN',None
            log(db,cid,'Merchant outcome still uncertain','Three lookups could not safely confirm creation. Stock remains held/committed. No refund, release, or duplicate creation is allowed until reconciliation.','review')
        else:next_state='RESCUE_RECONCILE';due=now+max(step,.01)*2**attempt;log(db,cid,'Merchant lookup retry scheduled',f'Lookup {attempt}/3 unavailable; no action resubmitted.','recovery')
    elif state=='RESCUE_VERIFY_ORDER':
        if merchant and merchant['status']=='CONFIRMED' and merchant['amount']==co['amount'] and merchant['quantity']==co['quantity'] and not db.execute('SELECT 1 FROM provider_refunds WHERE order_id=?',(case['order_id'],)).fetchone():
            db.execute("UPDATE orders SET fulfillment='CONFIRMED' WHERE id=?",(case['order_id'],))
            next_state,due='ORDER_RESTORED',None
            log(db,cid,'Order rescue verified','Merchant order, original checkout total, stock commitment, and payment agree. Support case completed with verified simulated order restoration.')
        else:next_state,due='NEEDS_HUMAN',None;log(db,cid,'Verification requires a human','Merchant outcome could not be verified against the original checkout.','review')
    else:due=None
    transition(db,case,next_state,due,now)
    return True


def change_intent(db,case,intent,today_only,now,step):
    link=db.execute('SELECT * FROM rescue_links WHERE case_id=?',(case['id'],)).fetchone()
    if not link:raise HTTPException(404,'Not an order rescue case')
    co=checkout(db,link['checkout_id'])
    if case['state']=='CLOSED_REVIEWED':raise HTTPException(409,'This case was closed by a reviewer')
    if db.execute('SELECT 1 FROM refunds WHERE order_id=?',(case['order_id'],)).fetchone() or db.execute('SELECT 1 FROM provider_refunds WHERE order_id=?',(case['order_id'],)).fetchone():
        raise HTTPException(409,'A refund has already started. Keep tracking it; restoration is blocked.')
    merchant=db.execute("SELECT * FROM merchant_orders WHERE checkout_id=? AND status='CONFIRMED'",(co['id'],)).fetchone()
    if link['resume_state']=='RESCUE_RECONCILE' and not link['order_ref']:
        db.execute('UPDATE rescue_links SET pending_intent=? WHERE case_id=?',(intent,case['id']))
        log(db,case['id'],'Outcome change waits for reconciliation','Customer preference recorded, but creation is uncertain. No stock release or refund is allowed yet.','review')
        return
    if merchant and intent=='status':
        db.execute("UPDATE rescue_links SET intent='status',pending_intent=NULL WHERE case_id=?",(case['id'],))
        transition(db,case,'INFORMATION_PROVIDED',None,now)
        log(db,case['id'],'Verified status provided','Payment succeeded and the merchant order is confirmed. No cancellation, refund, or inventory mutation was requested.')
        return
    if merchant and intent!='restore':
        db.execute('UPDATE rescue_links SET pending_intent=? WHERE case_id=?',(intent,case['id']))
        transition(db,case,'NEEDS_HUMAN',None,now)
        log(db,case['id'],'Customer changed outcome after creation','A confirmed merchant order exists. A human must explicitly cancel it before any refund or stock release.','review')
        return
    if link['resume_state']=='RESCUE_RECONCILE' and not link['order_ref']:
        raise HTTPException(409,'Merchant creation is uncertain. Restore the connection and reconcile before switching paths.')
    if intent!='restore' and release(db,co):log(db,case['id'],'Reservation compensated','Unused stock returned before changing the requested outcome.','recovery')
    db.execute('UPDATE rescue_links SET intent=?,today_only=?,pending_intent=NULL,resume_state=NULL WHERE case_id=?',(intent,int(today_only),case['id']))
    db.execute('UPDATE cases SET approved=0,offline_intent=? WHERE id=?',(intent,case['id']))
    transition(db,case,'RESCUE_VERIFY',now+step,now)
    log(db,case['id'],'Customer outcome updated',f'Explicit structured preference: {intent}; today-only delivery: {bool(today_only)}. No model call.')


def review(db,case,decision,now,step):
    link=db.execute('SELECT * FROM rescue_links WHERE case_id=?',(case['id'],)).fetchone()
    if not link:raise HTTPException(404,'Not a rescue case')
    co=checkout(db,link['checkout_id'])
    merchant=db.execute('SELECT * FROM merchant_orders WHERE checkout_id=?',(co['id'],)).fetchone()
    if decision=='resume':
        if case['state']!='RESCUE_PAUSED':raise HTTPException(409,'No paused rescue checkpoint')
        db.execute('UPDATE rescue_links SET resume_state=NULL,recoveries=recoveries+1 WHERE case_id=?',(case['id'],))
        transition(db,case,link['resume_state'] or 'RESCUE_VERIFY',now+step,now)
        log(db,case['id'],'Rescue checkpoint resumed','Restored the persisted next step. Inventory and operation identity are preserved.','recovery');return
    if decision=='reconnect':
        if link['resume_state']!='RESCUE_RECONCILE':raise HTTPException(409,'No uncertain merchant connection to restore')
        db.execute("UPDATE rescue_links SET fault=NULL,reconcile_attempts=0 WHERE case_id=?",(case['id'],))
        transition(db,case,'RESCUE_RECONCILE',now+step,now)
        log(db,case['id'],'Mock merchant connection restored','Reconciliation will retry the original lookup, not creation.','recovery');return
    if case['state'] not in {'NEEDS_HUMAN','RESCUE_PAUSED','WAITING_CUSTOMER'}:raise HTTPException(409,'This case does not need this review action')
    if decision=='cancel_and_refund':
        if link['resume_state']=='RESCUE_RECONCILE' and not link['order_ref']:raise HTTPException(409,'Reconcile uncertain creation before cancellation or refund')
        if not merchant or merchant['status']!='CONFIRMED' or link['pending_intent']!='refund':raise HTTPException(409,'An explicit refund request and confirmed merchant order are required')
        db.execute("UPDATE merchant_orders SET status='CANCELLED' WHERE checkout_id=?",(co['id'],));release(db,co,True)
        db.execute("UPDATE rescue_links SET intent='refund',pending_intent=NULL,resume_state=NULL WHERE case_id=?",(case['id'],))
        db.execute("UPDATE orders SET fulfillment='CANCELLED' WHERE id=?",(case['order_id'],))
        db.execute("UPDATE cases SET approved=1,offline_intent='refund' WHERE id=?",(case['id'],))
        transition(db,case,'CHECK_POLICY',now+step,now)
        log(db,case['id'],'Merchant cancellation approved','Reviewer cancelled the simulated order, released its committed stock, and approved the requested refund.','review')
    elif decision=='approve':
        if fence(db,case):raise HTTPException(409,fence(db,case))
        if len(db.execute("SELECT id FROM rescue_payments WHERE checkout_id=? AND status='SUCCESS'",(co['id'],)).fetchall())!=1:raise HTTPException(409,'Reconcile multiple payments before approving')
        db.execute('UPDATE cases SET approved=1 WHERE id=?',(case['id'],))
        transition(db,case,'CHECK_POLICY',now+step,now);log(db,case['id'],'Refund approval recorded','A reviewer approved the exact saved checkout amount. Backend rechecks eligibility.','review')
    else:
        if link['resume_state']=='RESCUE_RECONCILE' and not link['order_ref']:raise HTTPException(409,'Reconcile uncertain creation before abandoning resources')
        released=release(db,co)
        transition(db,case,'CLOSED_REVIEWED',None,now)
        log(db,case['id'],'Recovery abandoned',('Unused stock was released. ' if released else '')+'No new refund or order action was taken.','review')
    db.execute('UPDATE rescue_links SET human_decisions=human_decisions+1 WHERE case_id=?',(case['id'],))


def details(db,cid):
    link=db.execute('SELECT * FROM rescue_links WHERE case_id=?',(cid,)).fetchone()
    if not link:return None
    result=dict(link);co=checkout(db,link['checkout_id']);result['checkout']=co
    result['checkout']['customer_data']=json.loads(co['customer_data'])
    result['inventory']=dict(db.execute('SELECT * FROM rescue_inventory WHERE sku=?',(co['sku'],)).fetchone())
    for name,table in [('reservation','inventory_reservations'),('merchant_order','merchant_orders')]:
        row=db.execute(f'SELECT * FROM {table} WHERE checkout_id=?',(co['id'],)).fetchone();result[name]=dict(row) if row else None
    result['payments']=[dict(row) for row in db.execute('SELECT * FROM rescue_payments WHERE checkout_id=?',(co['id'],))]
    return result


def demo(db,scenario,intent,now,amount=79900,category='groceries'):
    token=uuid.uuid4().hex[:8].upper();cid='CO-'+token;sku='SKU-'+token
    desired=intent or ('refund' if scenario=='approval' else 'status' if scenario=='status' else 'clarify' if scenario=='ambiguous' else 'restore')
    stock=0 if scenario=='stockout' else 5
    db.execute('INSERT INTO rescue_inventory VALUES (?,?,?,?)',(sku,'Everyday essentials kit',stock,0 if scenario=='deadline' else 1))
    db.execute('INSERT INTO rescue_checkouts(id,customer,merchant,sku,quantity,unit_price,amount,customer_data,desired,today_only,created) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(cid,auth.customer_id(),'Rescue Demo Merchant',sku,1,amount,amount,json.dumps({'name':'Demo Customer','address':'Demo delivery address, India','demo_fault':scenario if scenario in {'timeout','unknown','interruption','create_failed'} else None}),desired,int(scenario=='deadline'),now))
    commerce.configure(db,cid,category,now)
    payment='UPI-DEMO-'+token
    ingest(db,'EVT-'+token,payment,cid,amount,'SUCCESS',now)
    if scenario=='duplicate_payments':ingest(db,'EVT-SECOND-'+token,payment+'-SECOND',cid,amount,'SUCCESS',now)
    return cid


class DemoRequest(BaseModel):
    scenario: Literal['restore','timeout','approval','grace','stockout','deadline','status','ambiguous','interruption','unknown','duplicate_payments','create_failed']='restore'
    intent: Literal['restore','refund','status','clarify']|None=None
    category: Literal['groceries','electronics','fashion']='groceries'
class Preference(BaseModel):
    intent: Literal['restore','refund','status','clarify']
    today_only: bool=False
class Decision(BaseModel):
    decision: Literal['approve','reject','cancel_and_refund','resume','reconnect','abandon']
class Settings(BaseModel):
    grace_seconds: int=Field(ge=1,le=120)
class Receipt(BaseModel):
    event_id: str=Field(min_length=1,max_length=100)
    payment_id: str=Field(min_length=1,max_length=100)
    checkout_id: str=Field(min_length=1,max_length=100)
    amount_paise: int=Field(gt=0)
    status: Literal['SUCCESS','PENDING','FAILURE']='SUCCESS'


def install(app,engine):
    @app.get('/api/rescue')
    def overview():
        with engine.connection() as db:
            grace=db.execute('SELECT grace_seconds FROM rescue_settings WHERE id=1').fetchone()[0]
            entries=[]
            scope,params=auth.checkout_scope('co')
            for row in db.execute('SELECT co.* FROM rescue_checkouts co WHERE '+scope+' ORDER BY co.created DESC LIMIT 60',params).fetchall():
                co=dict(row);link=db.execute('SELECT case_id FROM rescue_links WHERE checkout_id=?',(co['id'],)).fetchone()
                order=db.execute('SELECT * FROM merchant_orders WHERE checkout_id=?',(co['id'],)).fetchone()
                payment=db.execute('SELECT * FROM rescue_payments WHERE checkout_id=? ORDER BY paid_at LIMIT 1',(co['id'],)).fetchone()
                entries.append({'checkout':co,'payment':dict(payment) if payment else None,'merchant_order':dict(order) if order else None,'case':engine.snapshot(db,link['case_id']) if link else None,'detect_after':payment['paid_at']+grace if payment else None})
            return {'grace_seconds':grace,'entries':entries,'simulated':True,'model_calls':0}

    @app.post('/api/rescue/demo')
    def create_demo(body: DemoRequest):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');co=demo(db,body.scenario,body.intent,time.time(),129900 if body.scenario=='approval' else 79900,body.category)
            return {'checkout_id':co,'simulated':True}

    @app.post('/api/rescue/mock-events')
    def mock_event(body: Receipt):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');accepted=ingest(db,body.event_id,body.payment_id,body.checkout_id,body.amount_paise,body.status,time.time())
            return {'accepted':accepted,'simulated':True}

    @app.post('/api/rescue/settings')
    def settings(body: Settings):
        with engine.connection() as db:db.execute('UPDATE rescue_settings SET grace_seconds=? WHERE id=1',(body.grace_seconds,))
        return {'grace_seconds':body.grace_seconds}

    @app.post('/api/rescue/{checkout_id}/confirm-original')
    def confirm_original(checkout_id: str):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');co=checkout(db,checkout_id)
            link=db.execute('SELECT * FROM rescue_links WHERE checkout_id=?',(checkout_id,)).fetchone()
            if link:
                case=dict(db.execute('SELECT * FROM cases WHERE id=?',(link['case_id'],)).fetchone())
                if link['intent']!='restore' or db.execute('SELECT 1 FROM provider_refunds WHERE order_id=?',(case['order_id'],)).fetchone():raise HTTPException(409,'Refund or alternate outcome has already been selected')
            return {'merchant_order':create_merchant(db,co,time.time()),'simulated':True}

    @app.post('/api/rescue/cases/{case_id}/preference')
    def preference(case_id: str,body: Preference):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');case=engine.snapshot(db,case_id)
            change_intent(db,case,body.intent,body.today_only,time.time(),engine.STEPS_SECONDS)
            return engine.snapshot(db,case_id)

    @app.post('/api/rescue/cases/{case_id}/decision')
    def decide(case_id: str,body: Decision):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');case=engine.snapshot(db,case_id)
            review(db,case,body.decision,time.time(),engine.STEPS_SECONDS)
            return engine.snapshot(db,case_id)


def baseline_decision(co,payments,original_exists):
    """Explicit decision-only baseline: status-aware, otherwise refund-first.
    It does not restore inventory/orders or implement partial-action recovery.
    This is not a live-model comparison or a claim that all rules are inferior.
    """
    if len(payments)!=1:return 'NEEDS_HUMAN'
    if co['desired']=='status':return 'INFORMATION_PROVIDED'
    if co['desired']=='clarify':return 'WAITING_CUSTOMER'
    if original_exists:return 'ORDER_RESTORED'
    if co['amount']>100000:return 'NEEDS_HUMAN'
    return 'RESOLVED'


def scorecard(engine):
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    try:
        engine.initialize_db(db)
        db.execute('UPDATE rescue_settings SET grace_seconds=2 WHERE id=1')
        categories=['restore','timeout','approval','late_confirmation','stockout','deadline','status','ambiguous','interruption','switch_reserved','switch_created','duplicate_payments','unknown']
        records=[]
        for index in range(26):
            kind=categories[index%13];variant=index//13+1;start=1000000.+index*100
            scenario='restore' if kind in {'late_confirmation','switch_reserved'} else 'timeout' if kind=='switch_created' else kind
            coid=demo(db,scenario,None,start,129900 if kind=='approval' else 79900+index)
            co=checkout(db,coid)
            if kind=='late_confirmation':create_merchant(db,co,start+1)
            expected='NO_CASE' if kind=='late_confirmation' else 'RESOLVED' if kind in {'approval','switch_reserved','switch_created'} else 'WAITING_CUSTOMER' if kind in {'stockout','deadline','ambiguous'} else 'INFORMATION_PROVIDED' if kind=='status' else 'NEEDS_HUMAN' if kind in {'duplicate_payments','unknown'} else 'ORDER_RESTORED'
            baseline=baseline_decision(co,db.execute("SELECT * FROM rescue_payments WHERE checkout_id=? AND status='SUCCESS'",(coid,)).fetchall(),kind=='late_confirmation')
            if kind=='late_confirmation':baseline='NO_CASE'
            switched=False;approval_done=False;decision_count=0;case=None
            for tick in range(48):
                now=start+tick
                engine.advance_db(db,now=now,step=1)
                link=db.execute('SELECT * FROM rescue_links WHERE checkout_id=?',(coid,)).fetchone()
                if not link:
                    if kind=='late_confirmation' and tick>=4:break
                    continue
                case=engine.snapshot(db,link['case_id'])
                if kind=='switch_reserved' and not switched and case['state']=='RESCUE_CREATE':
                    change_intent(db,case,'refund',False,now,1);switched=True;continue
                if kind=='switch_created' and not switched and case['state']=='RESCUE_RECONCILE':
                    change_intent(db,case,'refund',False,now,1);switched=True;continue
                if case['state']=='RESCUE_PAUSED':review(db,case,'resume',now,1);continue
                if kind=='approval' and case['state']=='NEEDS_HUMAN' and not approval_done:
                    review(db,case,'approve',now,1);approval_done=True;continue
                if kind=='switch_created' and case['state']=='NEEDS_HUMAN' and not approval_done:
                    review(db,case,'cancel_and_refund',now,1);approval_done=True;continue
                if case['state'] in TERMINAL|{'NEEDS_HUMAN','WAITING_CUSTOMER'}:break
            link=db.execute('SELECT * FROM rescue_links WHERE checkout_id=?',(coid,)).fetchone()
            case=engine.snapshot(db,link['case_id']) if link else None
            actual=case['state'] if case else 'NO_CASE'
            merchant=db.execute('SELECT * FROM merchant_orders WHERE checkout_id=?',(coid,)).fetchone()
            reservation=db.execute('SELECT * FROM inventory_reservations WHERE checkout_id=?',(coid,)).fetchone()
            refunds=db.execute('SELECT COUNT(*) FROM provider_refunds WHERE order_id=?',('CHECKOUT-'+coid,)).fetchone()[0]
            order_count=db.execute('SELECT COUNT(*) FROM merchant_orders WHERE checkout_id=?',(coid,)).fetchone()[0]
            confirmed=bool(merchant and merchant['status']=='CONFIRMED')
            inv=db.execute('SELECT available FROM rescue_inventory WHERE sku=?',(co['sku'],)).fetchone()[0]
            expected_stock=0 if kind=='stockout' else 4 if confirmed else 5
            human=case['rescue']['human_decisions'] if case else 0
            intent_ok=(not refunds and not merchant) if kind in {'status','ambiguous','stockout','deadline'} else (refunds==1 and not confirmed) if kind in {'approval','switch_reserved','switch_created'} else True
            checks={'verified_outcome':actual==expected,'intent_respected':intent_ok,'no_duplicate_order':order_count<=1,'no_duplicate_refund':refunds<=1,'no_conflicting_completion':not(confirmed and refunds),'inventory_conserved':inv==expected_stock,'zero_model_tokens':not case or case['ai_tokens']==0}
            records.append({'scenario':kind,'variant':variant,'expected':expected,'actual':actual,'baseline_decision':baseline,'baseline_matches':baseline==expected,'checks':checks,'passed':all(checks.values()),'human_decisions':human,'simulation_ticks':tick+1,'case':case,'merchant_order':dict(merchant) if merchant else None,'inventory_available':inv,'reservation':dict(reservation) if reservation else None,'refund_count':refunds,'recovered_value_paise':co['amount'] if actual=='ORDER_RESTORED' and confirmed else 0})
        metrics={'total':len(records),'correct_outcomes':sum(x['passed'] for x in records),'verified_completions':sum(x['passed'] and x['actual'] in {'ORDER_RESTORED','RESOLVED','INFORMATION_PROVIDED'} for x in records),'intent_respected':sum(x['checks']['intent_respected'] for x in records),'human_cases':sum(x['human_decisions']>0 or x['actual']=='NEEDS_HUMAN' for x in records),'human_decisions':sum(x['human_decisions'] for x in records),'duplicate_orders':sum(not x['checks']['no_duplicate_order'] for x in records),'duplicate_refunds':sum(not x['checks']['no_duplicate_refund'] for x in records),'conflicting_completions':sum(not x['checks']['no_conflicting_completion'] for x in records),'recovered_order_value_paise':sum(x['recovered_value_paise'] for x in records),'automatically_detected_cases':sum(bool(x['case']) for x in records),'reconciled_cases':sum(bool(x['case'] and x['case']['rescue']['recoveries']) for x in records),'baseline_decision_matches':sum(x['baseline_matches'] for x in records),'model_calls':0}
        return {'id':'RESCUE-RUN-'+uuid.uuid4().hex[:8].upper(),'created':time.time(),'label':'Offline order rescue scorecard','disclaimer':'26 structured fixtures through the actual workflow. No model calls. Baseline is a decision-only, status-aware refund-first rule, not an AI benchmark. Timings are virtual ticks.','metrics':metrics,'cases':records}
    finally:db.close()


def install_scorecard(app,engine):
    @app.post('/api/rescue-scorecards')
    def run():
        report=scorecard(engine)
        with engine.connection() as db:db.execute('INSERT INTO rescue_runs(id,created,report,owner) VALUES (?,?,?,?)',(report['id'],report['created'],json.dumps(report),auth.customer_id()))
        return report
    @app.get('/api/rescue-scorecards')
    def latest():
        with engine.connection() as db:
            row=db.execute('SELECT report FROM rescue_runs WHERE owner=? ORDER BY created DESC LIMIT 1',(auth.customer_id(),)).fetchone()
            return {'latest':json.loads(row['report']) if row else None}
