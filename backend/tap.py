"""Presence-only NFC demo handoff. Never identifies a card or authorizes money."""
import hashlib
import secrets
import time
from typing import Literal
from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

TTL=300

def digest(value):return hashlib.sha256(value.encode()).hexdigest()

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS tap_sessions (
      id TEXT PRIMARY KEY, checkout_id TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
      owner_hash TEXT NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL,
      source TEXT, created REAL NOT NULL, redeemed REAL)''')

class Create(BaseModel):
    model_config=ConfigDict(extra='forbid')
    checkout_id: str=Field(min_length=1,max_length=80)
class Redeem(BaseModel):
    model_config=ConfigDict(extra='forbid')
    token: str=Field(min_length=43,max_length=43,pattern=r'^[A-Za-z0-9_-]+$')
    source: Literal['android_nfc','simulation']

def public(row,now):
    state='EXPIRED' if row['state']=='WAITING' and row['expires']<=now else row['state']
    return {k:row[k] for k in ['id','checkout_id','expires','source','created','redeemed']}|{'state':state,'simulated_payments':True,'card_identity_verified':False}

def install(app,engine):
    def owned(db,sid,key):
        row=db.execute('SELECT * FROM tap_sessions WHERE id=?',(sid,)).fetchone()
        if not row or not key or not secrets.compare_digest(row['owner_hash'],digest(key)):raise HTTPException(404,'Pairing session not found')
        engine.commerce.own_checkout(db,row['checkout_id'])
        return row

    @app.post('/api/tap/sessions')
    def create(body:Create):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');engine.commerce.own_checkout(db,body.checkout_id)
            # Bound outstanding capability lifetime without retaining old raw secrets.
            db.execute("UPDATE tap_sessions SET state='EXPIRED' WHERE state='WAITING' AND expires<=?",(time.time(),))
            token=secrets.token_urlsafe(32);owner=secrets.token_urlsafe(32);sid='TAP-'+secrets.token_hex(12);now=time.time()
            db.execute('INSERT INTO tap_sessions VALUES (?,?,?,?,?,?,?,?,?)',(sid,body.checkout_id,digest(token),digest(owner),now+TTL,'WAITING',None,now,None))
            row=db.execute('SELECT * FROM tap_sessions WHERE id=?',(sid,)).fetchone()
            return public(row,now)|{'token':token,'owner_key':owner}

    @app.get('/api/tap/sessions/{session_id}')
    def status(session_id:str,x_tap_owner:str|None=Header(default=None)):
        with engine.connection() as db:return public(owned(db,session_id,x_tap_owner),time.time())

    @app.post('/api/tap/sessions/{session_id}/cancel')
    def cancel(session_id:str,x_tap_owner:str|None=Header(default=None)):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');owned(db,session_id,x_tap_owner)
            db.execute("UPDATE tap_sessions SET state='CANCELLED' WHERE id=? AND state='WAITING'",(session_id,))
            return public(owned(db,session_id,x_tap_owner),time.time())

    @app.post('/api/tap/redeem')
    def redeem(body:Redeem):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');now=time.time()
            row=db.execute('SELECT * FROM tap_sessions WHERE token_hash=?',(digest(body.token),)).fetchone()
            if not row:raise HTTPException(404,'Pairing link not found')
            if row['state']!='WAITING':raise HTTPException(409,'Link already used or cancelled. Create a new pairing session.')
            if row['expires']<=now:raise HTTPException(410,'Pairing link expired. Create a new session.')
            engine.commerce.own_checkout(db,row['checkout_id'])
            db.execute("UPDATE tap_sessions SET state='REDEEMED',source=?,redeemed=? WHERE id=? AND state='WAITING'",(body.source,now,row['id']))
            engine.commerce.audit(db,row['checkout_id'],'Tap demo handoff recorded',('SIMULATED NFC event.' if body.source=='simulation' else 'Android app reported compatible NFC presence (not attested).')+' Opened the preselected demo checkout only. No card data, card ownership, bank linkage, or financial authorization was verified.','simulation')
            return public(db.execute('SELECT * FROM tap_sessions WHERE id=?',(row['id'],)).fetchone(),now)
