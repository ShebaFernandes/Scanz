"""Local provider simulator: durable acceptance, reconciliation, monotonic events."""
import json
import time
import uuid
from fastapi import HTTPException


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS provider_refunds (
      order_id TEXT PRIMARY KEY, request_key TEXT UNIQUE, ref TEXT UNIQUE,
      amount INTEGER, status TEXT, accepted REAL);
    CREATE TABLE IF NOT EXISTS recovery (
      case_id TEXT PRIMARY KEY, fault TEXT, consumed INTEGER DEFAULT 0,
      attempts INTEGER DEFAULT 0, recovered INTEGER DEFAULT 0,
      duplicates INTEGER DEFAULT 0, stale_events INTEGER DEFAULT 0,
      resume_state TEXT);
    CREATE TABLE IF NOT EXISTS provider_events (
      event_id TEXT PRIMARY KEY, case_id TEXT, status TEXT, received REAL);
    CREATE TABLE IF NOT EXISTS evaluation_runs (
      id TEXT PRIMARY KEY, created REAL, report TEXT);
    ''')
    columns={row['name'] for row in db.execute('PRAGMA table_info(recovery)')}
    if 'checkpoint_state' not in columns:
        db.execute('ALTER TABLE recovery ADD COLUMN checkpoint_state TEXT')


def log(db,cid,title,detail,kind='done'):
    db.execute('INSERT INTO events(case_id,title,detail,kind,created) VALUES (?,?,?,?,?)',
               (cid,title,detail,kind,time.time()))


def recovery_row(db,cid):
    db.execute('INSERT OR IGNORE INTO recovery(case_id) VALUES (?)',(cid,))
    return dict(db.execute('SELECT * FROM recovery WHERE case_id=?',(cid,)).fetchone())


def summary(db,case):
    row=db.execute('SELECT * FROM recovery WHERE case_id=?',(case['id'],)).fetchone()
    result=dict(row) if row else {'fault':None,'consumed':0,'attempts':0,'recovered':0,'duplicates':0,'stale_events':0,'resume_state':None}
    result['provider_refund_count']=db.execute('SELECT COUNT(*) FROM provider_refunds WHERE order_id=?',(case['order_id'],)).fetchone()[0]
    result['request_key']='refund:'+case['order_id']
    result['receipts']=[dict(r) for r in db.execute('SELECT event_id,status,received FROM provider_events WHERE case_id=? ORDER BY received',(case['id'],))]
    return result


def arm(db,cid,kind):
    row=recovery_row(db,cid)
    if row['fault'] and not row['consumed']:
        raise HTTPException(409,'A fault is already armed for this case.')
    db.execute('UPDATE recovery SET fault=?,consumed=0 WHERE case_id=?',(kind,cid))
    log(db,cid,'Simulation armed',kind.replace('_',' ')+'. Only the mock provider is affected.','simulation')


def consume(db,cid,kind):
    row=recovery_row(db,cid)
    if row['fault']==kind and not row['consumed']:
        db.execute('UPDATE recovery SET consumed=1 WHERE case_id=?',(cid,))
        return True
    return False


def import_provider_refund(db,case,provider):
    db.execute('INSERT OR IGNORE INTO refunds VALUES (?,?,?,?)',
               (case['order_id'],provider['ref'],provider['amount'],provider['status']))
    db.execute('UPDATE cases SET refund_ref=? WHERE id=?',(provider['ref'],case['id']))


def submit(db,case,order,now,step):
    existing=db.execute('SELECT * FROM refunds WHERE order_id=?',(order['id'],)).fetchone()
    if existing:
        recovery_row(db,case['id'])
        db.execute('UPDATE recovery SET duplicates=duplicates+1 WHERE case_id=?',(case['id'],))
        db.execute('UPDATE cases SET refund_ref=? WHERE id=?',(existing['ref'],case['id']))
        log(db,case['id'],'Duplicate refund prevented','Existing local refund retained; no second provider request.')
        return ('RESOLVED',None) if existing['status']=='CREDITED' else ('REFUND_PENDING',now+step)
    key='refund:'+order['id']
    db.execute('INSERT OR IGNORE INTO provider_refunds VALUES (?,?,?,?,?,?)',
               (order['id'],key,'MOCK-RF-'+uuid.uuid4().hex[:8].upper(),order['amount'],'ACCEPTED',now))
    provider=dict(db.execute('SELECT * FROM provider_refunds WHERE order_id=?',(order['id'],)).fetchone())
    if consume(db,case['id'],'timeout'):
        db.execute("UPDATE recovery SET resume_state='RECONCILE_REFUND' WHERE case_id=?",(case['id'],))
        log(db,case['id'],'Provider response timed out','SIMULATION: the provider accepted the refund but the acknowledgement was lost. Reconcile by the stable request key before any resubmission.','recovery')
        return 'RETRY_PENDING',now+max(step*2,.01)
    import_provider_refund(db,case,provider)
    log(db,case['id'],'Refund submitted',provider['ref']+' accepted by the simulated provider. No real money moved.')
    if consume(db,case['id'],'interruption'):
        db.execute("UPDATE recovery SET resume_state='REFUND_PENDING' WHERE case_id=?",(case['id'],))
        log(db,case['id'],'Worker interruption simulated','Checkpoint persisted after provider acceptance. Resume the saved work; no new refund is needed.','recovery')
        return 'PAUSED_RECOVERY',None
    return 'REFUND_PENDING',now+step


def retry(db,case,now,step):
    row=recovery_row(db,case['id'])
    attempt=row['attempts']+1
    db.execute('UPDATE recovery SET attempts=? WHERE case_id=?',(attempt,case['id']))
    if row['resume_state']=='RECONCILE_REFUND':
        provider=db.execute('SELECT * FROM provider_refunds WHERE request_key=?',('refund:'+case['order_id'],)).fetchone()
        if provider:
            import_provider_refund(db,case,dict(provider))
            db.execute('UPDATE recovery SET recovered=recovered+1,resume_state=NULL WHERE case_id=?',(case['id'],))
            log(db,case['id'],'Accepted refund recovered','Found the original provider refund by its request key. Reused its reference; did not resubmit.','recovery')
            return ('RESOLVED',None) if provider['status']=='CREDITED' else ('REFUND_PENDING',now+step)
        if attempt>=3:
            log(db,case['id'],'Reconciliation needs a human','Three lookups could not confirm acceptance. No automatic resubmission was attempted.','review')
            return 'NEEDS_HUMAN',None
        log(db,case['id'],'Reconciliation retry scheduled',f'Lookup {attempt}/3 did not confirm acceptance. Backoff increased; no refund resubmitted.','recovery')
        return 'RETRY_PENDING',now+max(step,.01)*(2**attempt)
    db.execute('UPDATE recovery SET recovered=recovered+1,resume_state=NULL WHERE case_id=?',(case['id'],))
    log(db,case['id'],'Tracking resumed','The delayed mock status check is ready. Continuing the existing refund.','recovery')
    return row['resume_state'] or 'REFUND_PENDING',now+step


RANK={'ACCEPTED':0,'PROCESSING':1,'CREDITED':2}
def apply_status(db,case,event_id,status,now,step):
    refund=db.execute('SELECT * FROM refunds WHERE order_id=?',(case['order_id'],)).fetchone()
    if not refund: raise HTTPException(409,'A local refund must exist before applying a status event.')
    if db.execute('SELECT 1 FROM provider_events WHERE event_id=?',(event_id,)).fetchone():
        recovery_row(db,case['id'])
        db.execute('UPDATE recovery SET duplicates=duplicates+1 WHERE case_id=?',(case['id'],))
        log(db,case['id'],'Duplicate status event ignored',event_id+' was already processed. State and refund amount are unchanged.','recovery')
        return False
    db.execute('INSERT INTO provider_events VALUES (?,?,?,?)',(event_id,case['id'],status,now))
    if RANK[status]<=RANK[refund['status']]:
        recovery_row(db,case['id'])
        db.execute('UPDATE recovery SET stale_events=stale_events+1 WHERE case_id=?',(case['id'],))
        log(db,case['id'],'Out-of-order status ignored',status+' cannot move the refund backwards from '+refund['status']+'.','recovery')
        return False
    db.execute('UPDATE refunds SET status=? WHERE order_id=?',(status,case['order_id']))
    db.execute('UPDATE provider_refunds SET status=? WHERE order_id=?',(status,case['order_id']))
    state='RESOLVED' if status=='CREDITED' else 'AWAITING_CREDIT'
    db.execute('UPDATE cases SET state=?,updated=?,due=? WHERE id=?',(state,now,None if status=='CREDITED' else now+step,case['id']))
    return True
