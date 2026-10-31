"""Mock-only checkout reconciliation. Demo OTP never authenticates a bank account."""
import hashlib
import json
import secrets
import time
from typing import Literal
from fastapi import HTTPException, Header
from pydantic import BaseModel, Field, ConfigDict

class Challenge(BaseModel):
    model_config=ConfigDict(extra='forbid')
    method: Literal['card','upi']
    identifier: str=Field(min_length=4,max_length=194)
    phone: str=Field(pattern=r'^[6-9]\d{9}$')
    consent: Literal[True]
class Verify(BaseModel):
    challenge_id: str=Field(max_length=64)
    code: str=Field(pattern=r'^\d{6}$')
class Start(BaseModel):
    payment_id: str=Field(max_length=80)
    outcome: Literal['refund','verify']
    authorize_mock_reversal: bool=False
    simulate_response_loss: bool=False

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS checkout_otp(id TEXT PRIMARY KEY,customer TEXT,instrument TEXT,phone_last4 TEXT,code_hash TEXT,created REAL,expires REAL,attempts INTEGER DEFAULT 0,consumed INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS checkout_grants(token_hash TEXT PRIMARY KEY,customer TEXT,instrument TEXT,expires REAL);
    CREATE TABLE IF NOT EXISTS checkout_payments(id TEXT PRIMARY KEY,customer TEXT,instrument TEXT,merchant TEXT,amount INTEGER,reference TEXT,scenario TEXT);
    CREATE TABLE IF NOT EXISTS checkout_cases(id TEXT PRIMARY KEY,payment_id TEXT UNIQUE,customer TEXT,outcome TEXT,authorized INTEGER,state TEXT,created REAL,updated REAL,due REAL,deadline REAL,checks INTEGER DEFAULT 0,bank_ack TEXT,merchant_ack TEXT,reversal_ref TEXT UNIQUE,credit_ack TEXT);
    CREATE TABLE IF NOT EXISTS checkout_events(id INTEGER PRIMARY KEY AUTOINCREMENT,case_id TEXT,title TEXT,detail TEXT,created REAL);
    ''')
    cols={r['name'] for r in db.execute('PRAGMA table_info(checkout_cases)')}
    if 'payment_snapshot' not in cols:db.execute('ALTER TABLE checkout_cases ADD COLUMN payment_snapshot TEXT')

def event(db,cid,title,detail,now):
    db.execute('INSERT INTO checkout_events(case_id,title,detail,created) VALUES (?,?,?,?)',(cid,title,detail,now))

def view(db,cid,uid):
    row=db.execute('SELECT * FROM checkout_cases WHERE id=? AND customer=?',(cid,uid)).fetchone()
    if not row:raise HTTPException(404,'Checkout request not found in your account.')
    value=dict(row);value['payment']=dict(db.execute('SELECT * FROM checkout_payments WHERE id=?',(value['payment_id'],)).fetchone())
    value['events']=[dict(r) for r in db.execute('SELECT title,detail,created FROM checkout_events WHERE case_id=? ORDER BY id',(cid,))]
    value.pop('payment_snapshot',None)
    value['simulated']=True;value['source']='mock_bank_and_acquirer';return value

def tick(db,now):
    for raw in db.execute('SELECT * FROM checkout_cases WHERE due IS NOT NULL AND due<=?',(now,)).fetchall():
        c=dict(raw);payment=db.execute('SELECT * FROM checkout_payments WHERE id=?',(c['payment_id'],)).fetchone();cid=c['id'];state=c['state'];due=now+4
        saved=json.loads(c['payment_snapshot']) if c.get('payment_snapshot') else None
        if not saved or any(payment[k]!=saved[k] for k in ['amount','reference','merchant','customer','instrument']):
            db.execute("UPDATE checkout_cases SET state='REVIEW_REQUIRED',due=NULL,updated=? WHERE id=?",(now,cid))
            event(db,cid,'Transaction evidence changed','Reference, amount or ownership no longer matches the authorized transaction. No further financial action is allowed.',now)
            continue
        if state=='CHECKING' and now>=c['deadline']:
            db.execute("UPDATE checkout_cases SET state='REVIEW_REQUIRED',due=NULL,updated=? WHERE id=?",(now,cid))
            event(db,cid,'Fast-check deadline reached','The partner result was not confirmed before the deadline. No reversal submitted.',now)
            continue
        if state=='CHECKING':
            count=c['checks']+1;db.execute('UPDATE checkout_cases SET checks=? WHERE id=?',(count,cid))
            event(db,cid,'Payment partners checked',f'Simulated bank and merchant-acquirer query #{count}. No live financial system contacted.',now)
            # Contradictory evidence is never enough to authorize a reversal.
            if payment['scenario']=='unresolved':
                if now>=c['deadline']:
                    state='REVIEW_REQUIRED';due=None;event(db,cid,'Fast-check deadline reached','No definitive merchant outcome. Evidence saved; no reversal submitted.',now)
            elif count>=2:
                bank='MOCK-DEBIT-'+payment['reference'];merchant='MOCK-ACQ-'+payment['reference']
                db.execute('UPDATE checkout_cases SET bank_ack=?,merchant_ack=? WHERE id=?',(bank,merchant,cid))
                if payment['scenario']=='merchant_paid':
                    state='MERCHANT_PAID';due=None;event(db,cid,'Merchant payment confirmed','Mock acquirer confirms merchant credit for this exact reference and amount. Show the demo receipt; no refund requested.',now)
                elif c['outcome']=='verify':
                    state='INFORMATION_PROVIDED';due=None;event(db,cid,'Payment evidence ready','Mock bank confirms debit; mock acquirer confirms final non-credit. You requested information only; no reversal initiated.',now)
                elif c['authorized'] and payment['amount']<=100000:
                    ref='MOCK-REV-'+hashlib.sha256(c['payment_id'].encode()).hexdigest()[:16].upper()
                    db.execute('UPDATE checkout_cases SET reversal_ref=? WHERE id=?',(ref,cid));state='CREDIT_PENDING'
                    event(db,cid,'Mock reversal accepted','Mock payment partner accepted one reversal for the original transaction. Acceptance is not proof of bank credit.',now)
                else:
                    state='REVIEW_REQUIRED';due=None;event(db,cid,'Approval required','Automatic reversal is not authorized or exceeds the mock merchant limit. No reversal submitted.',now)
        elif state=='CREDIT_PENDING':
            if payment['scenario']=='reversal_credit':
                state='CREDIT_CONFIRMED';due=None
                db.execute('UPDATE checkout_cases SET credit_ack=? WHERE id=?',('MOCK-CREDIT-'+payment['reference'],cid))
                event(db,cid,'Simulated bank credit confirmed','The demo bank confirms the original amount was credited. No real money moved.',now)
            elif now>=c['deadline']:
                state='TRACKING_CREDIT';due=None
                event(db,cid,'Credit is not confirmed','Reversal acceptance is saved. Real bank credit cannot be promised instantly. This mock case remains open for partner follow-up.',now)
            else:due=min(now+10,c['deadline'])
        else:due=None
        if due is not None:due=min(due,c['deadline'])
        db.execute('UPDATE checkout_cases SET state=?,due=?,updated=? WHERE id=?',(state,due,now,cid))

def install(app,engine):
    def grant(db,token):
        row=db.execute('SELECT * FROM checkout_grants WHERE token_hash=? AND customer=? AND expires>?',(engine.auth.digest(token),engine.auth.customer_id(),time.time())).fetchone()
        if not row:raise HTTPException(403,'Demo checkout session expired. Start verification again.')
        return row

    @app.post('/api/checkout/otp')
    def challenge(body:Challenge):
        import re
        value=body.identifier.strip().lower()
        if (body.method=='card' and not re.fullmatch(r'\d{4}',value)) or (body.method=='upi' and not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,127}@[a-z0-9][a-z0-9.-]{1,63}',value)):
            raise HTTPException(422,'Use only the last four card digits or a UPI ID. Never provide a full card number.')
        now=time.time();uid=engine.auth.customer_id();cid=secrets.token_hex(16);code=f'{secrets.randbelow(1000000):06d}'
        instrument=body.method+':'+hashlib.sha256(value.encode()).hexdigest()
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM checkout_otp WHERE expires<?',(now-86400,))
            db.execute('DELETE FROM checkout_grants WHERE expires<=?',(now,))
            recent=db.execute('SELECT count(*) AS n,max(created) AS latest FROM checkout_otp WHERE customer=? AND created>?',(uid,now-900)).fetchone()
            if recent['n']>=5 or (recent['latest'] and now-recent['latest']<30):raise HTTPException(429,'Please wait before requesting another demo code. Limit: five per 15 minutes.')
            db.execute('UPDATE checkout_otp SET consumed=1 WHERE customer=?',(uid,))
            db.execute('INSERT INTO checkout_otp(id,customer,instrument,phone_last4,code_hash,created,expires) VALUES (?,?,?,?,?,?,?)',(cid,uid,instrument,body.phone[-4:],engine.auth.hash_password(code),now,now+120))
        return {'challenge_id':cid,'demo_code':code,'phone_hint':'••••••'+body.phone[-4:],'expires':now+120,'simulated':True,'delivery':'on_screen_only','bank_ownership_verified':False}

    @app.post('/api/checkout/verify')
    def verify(body:Verify):
        now=time.time();uid=engine.auth.customer_id()
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM checkout_otp WHERE id=? AND customer=?',(body.challenge_id,uid)).fetchone()
            if not row or row['consumed'] or row['expires']<=now or row['attempts']>=5:raise HTTPException(400,'Demo code expired or used. Request a new code.')
            if not engine.auth.verify_password(body.code,row['code_hash']):
                db.execute('UPDATE checkout_otp SET attempts=attempts+1 WHERE id=?',(body.challenge_id,));db.commit()
                raise HTTPException(400,'Incorrect demo code.')
            db.execute('UPDATE checkout_otp SET consumed=1 WHERE id=?',(body.challenge_id,))
            token=secrets.token_urlsafe(32);db.execute('INSERT INTO checkout_grants VALUES (?,?,?,?)',(engine.auth.digest(token),uid,row['instrument'],now+600))
            for scenario,merchant,amount in [('merchant_paid','Mall Grocery Counter',79900),('reversal_credit','Mall Fashion Counter',79900),('reversal_pending','Mall Electronics Counter',99900),('unresolved','Mall Cafe Counter',24900)]:
                pid='CP-'+hashlib.sha256((uid+row['instrument']+scenario).encode()).hexdigest()[:24]
                db.execute('INSERT OR IGNORE INTO checkout_payments VALUES (?,?,?,?,?,?,?)',(pid,uid,row['instrument'],merchant,amount,'DEMO-'+pid[3:15].upper(),scenario))
        return {'checkout_session':token,'expires':now+600,'simulated':True,'bank_ownership_verified':False}

    @app.get('/api/checkout/payments')
    def payments(x_checkout_session:str=Header(default='')):
        with engine.connection() as db:
            g=grant(db,x_checkout_session)
            rows=[dict(r) for r in db.execute('SELECT p.*,c.id AS case_id FROM checkout_payments p LEFT JOIN checkout_cases c ON c.payment_id=p.id WHERE p.customer=? AND p.instrument=?',(engine.auth.customer_id(),g['instrument']))]
            return {'payments':rows,'simulated':True,'note':'Synthetic checkout records, not bank history.'}

    @app.post('/api/checkout/cases')
    def start(body:Start,x_checkout_session:str=Header(default='')):
        now=time.time();uid=engine.auth.customer_id()
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');g=grant(db,x_checkout_session)
            payment=db.execute('SELECT * FROM checkout_payments WHERE id=? AND customer=? AND instrument=?',(body.payment_id,uid,g['instrument'])).fetchone()
            if not payment:raise HTTPException(404,'Payment not found in this checkout session.')
            old=db.execute('SELECT id FROM checkout_cases WHERE payment_id=?',(body.payment_id,)).fetchone()
            if old:
                event(db,old['id'],'Original operation recovered','Reused the saved transaction case after a repeated request. No second case or reversal was created.',now)
                return view(db,old['id'],uid)
            if body.outcome=='refund' and not body.authorize_mock_reversal:raise HTTPException(422,'Authorize a simulated reversal only if the merchant was not paid.')
            cid='HUB-'+secrets.token_hex(8).upper()
            db.execute('INSERT INTO checkout_cases(id,payment_id,customer,outcome,authorized,state,created,updated,due,deadline) VALUES (?,?,?,?,?,?,?,?,?,?)',(cid,body.payment_id,uid,body.outcome,int(body.authorize_mock_reversal),'CHECKING',now,now,now,now+60))
            db.execute('UPDATE checkout_cases SET payment_snapshot=? WHERE id=?',(json.dumps(dict(payment)),cid))
            event(db,cid,'Checkout investigation opened','User reports bank debit and failed app status. Checking the exact simulated merchant reference and amount before any action.',now)
            result=view(db,cid,uid)
            if body.simulate_response_loss:
                event(db,cid,'Response lost after save','Injected demo timeout after the case was committed. The client must recover the original operation instead of creating another.',now)
                db.commit()
                raise HTTPException(504,'Simulated response loss. The original case was saved; retry using the same payment ID.')
            return result

    @app.get('/api/checkout/cases')
    def cases():
        uid=engine.auth.customer_id()
        with engine.connection() as db:return {'cases':[view(db,r['id'],uid) for r in db.execute('SELECT id FROM checkout_cases WHERE customer=? ORDER BY created DESC',(uid,))]}

    @app.get('/api/checkout/cases/{cid}')
    def case(cid:str):
        with engine.connection() as db:return view(db,cid,engine.auth.customer_id())
