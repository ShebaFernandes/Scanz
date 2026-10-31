"""Account-private, user-supplied transaction history. Never provider evidence."""
import hashlib
import json
from datetime import date
from fastapi import HTTPException
from pydantic import BaseModel, Field, ConfigDict

class Transaction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    date: date
    merchant: str=Field(min_length=1,max_length=160)
    amount_paise: int=Field(gt=0,le=1000000000,strict=True)

class Import(BaseModel):
    model_config=ConfigDict(extra='forbid')
    last4: str=Field(pattern=r'^\d{4}$')
    transactions: list[Transaction]=Field(min_length=1,max_length=500)

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS statement_transactions (
        id TEXT PRIMARY KEY, customer TEXT NOT NULL, last4 TEXT NOT NULL,
        date TEXT NOT NULL, merchant TEXT NOT NULL, amount_paise INTEGER NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS statement_owner ON statement_transactions(customer,last4)')

def install(app,engine):
    @app.get('/api/statement-transactions')
    def history(last4: str):
        import re
        if not re.fullmatch(r'\d{4}',last4):raise HTTPException(422,'Enter four digits.')
        with engine.connection() as db:
            rows=db.execute('SELECT id,date,merchant,amount_paise FROM statement_transactions WHERE customer=? AND last4=? ORDER BY date DESC,id LIMIT 500',(engine.auth.customer_id(),last4)).fetchall()
            return {'transactions':[dict(r) for r in rows],'source':'user_import','verified':False}

    @app.post('/api/statement-transactions')
    def save(body:Import):
        raise HTTPException(410,'CSV import has been removed. Use My purchases for merchant orders.')

    @app.delete('/api/statement-transactions')
    def remove(last4:str):
        with engine.connection() as db:
            db.execute('DELETE FROM statement_transactions WHERE customer=? AND last4=?',(engine.auth.customer_id(),last4))
        return {'deleted':True}
