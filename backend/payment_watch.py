"""Durable scheduled reconciliation against a local mock payment adapter."""
import os
from fastapi import HTTPException

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS payment_watch(case_id TEXT PRIMARY KEY,started REAL,deadline REAL,last_checked REAL,checks INTEGER NOT NULL DEFAULT 0,interval_seconds REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS mock_payment_updates(order_id TEXT PRIMARY KEY,after_checks INTEGER NOT NULL,payment_status TEXT NOT NULL,fulfillment TEXT);
    ''')

def begin(db,case,now,engine):
    interval=max(1,float(os.environ.get('PAYMENT_CHECK_SECONDS','10')))
    deadline=now+max(interval,float(os.environ.get('PAYMENT_ESCALATE_SECONDS','120')))
    added=db.execute('INSERT OR IGNORE INTO payment_watch(case_id,started,deadline,interval_seconds) VALUES (?,?,?,?)',(case['id'],now,deadline,interval)).rowcount
    row=db.execute('SELECT * FROM payment_watch WHERE case_id=?',(case['id'],)).fetchone()
    if added:engine.event(db,case['id'],'Automatic payment checks started','Mock Paytm confirmation is pending. No refund will be requested until payment and order eligibility are verified.')
    return 'WAITING_PAYMENT',min(now,row['deadline'])

def advance(db,case,order,now,step,engine):
    row=db.execute('SELECT * FROM payment_watch WHERE case_id=?',(case['id'],)).fetchone()
    if not row:
        begin(db,case,now,engine);row=db.execute('SELECT * FROM payment_watch WHERE case_id=?',(case['id'],)).fetchone()
    count=row['checks']+1
    db.execute('UPDATE payment_watch SET checks=?,last_checked=? WHERE case_id=?',(count,now,case['id']))
    plan=db.execute('SELECT * FROM mock_payment_updates WHERE order_id=?',(order['id'],)).fetchone()
    # Only explicit mock-provider fixtures may change a pending outcome.
    if plan and order['payment_status']=='PENDING' and count>=plan['after_checks']:
        db.execute('UPDATE orders SET payment_status=?,fulfillment=COALESCE(?,fulfillment) WHERE id=?',(plan['payment_status'],plan['fulfillment'],order['id']))
        order=engine.order_for(db,order['id'])
    engine.event(db,case['id'],'Payment checked automatically',f"Mock Paytm check {count}: {order['payment_status']}. No real provider was contacted.")
    if order['payment_status']=='SUCCESS':
        engine.event(db,case['id'],'Payment confirmed','Resuming the original request. Refund eligibility will be checked again before any simulated action.')
        return 'CHECK_POLICY',now+step
    if order['payment_status']!='PENDING':
        engine.event(db,case['id'],'Payment result confirmed','The mock provider reports no successful payment. No refund was submitted.')
        return ('INFORMATION_PROVIDED',None) if case.get('offline_intent')=='status' else ('NEEDS_HUMAN',None)
    if now>=row['deadline']:
        engine.event(db,case['id'],'Payment check deadline reached',f"Payment still pending after {count} checks. Evidence is saved for specialist reconciliation; no refund submitted.",'review')
        return 'NEEDS_HUMAN',None
    return 'WAITING_PAYMENT',min(now+row['interval_seconds'],row['deadline'])

def summary(db,case):
    row=db.execute('SELECT * FROM payment_watch WHERE case_id=?',(case['id'],)).fetchone()
    if not row:return None
    return {**dict(row),'next_check':case['due'] if case['state']=='WAITING_PAYMENT' else None,'active':case['state']=='WAITING_PAYMENT','source':'mock_paytm'}

def install(app,engine):
    @app.post('/api/cases/{case_id}/resolve-now')
    def resume(case_id:str):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');case=engine.snapshot(db,case_id);order=case['order']
            if case['agent_mode']!='deterministic' or order['payment_status']!='PENDING' or case['refund'] or case['state'] not in {'NEEDS_HUMAN','WAITING_PAYMENT'}:
                raise HTTPException(409,'This request is not eligible for automatic pending-payment checks.')
            row=db.execute('SELECT * FROM payment_watch WHERE case_id=?',(case_id,)).fetchone()
            if case['state']=='WAITING_PAYMENT':return case
            if row:raise HTTPException(409,'The automatic-check deadline has already ended. Specialist review is required.')
            import time
            now=time.time();state,due=begin(db,case,now,engine)
            db.execute('UPDATE cases SET state=?,due=?,updated=? WHERE id=?',(state,due,now,case_id))
            return engine.snapshot(db,case_id)
