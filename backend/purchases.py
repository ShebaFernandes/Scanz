"""Durable account-scoped simulated merchant feed. No payment/model network calls."""
import time
import hashlib

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS merchant_feed (
      customer TEXT NOT NULL, external_id TEXT NOT NULL, merchant TEXT NOT NULL,
      amount INTEGER NOT NULL, last4 TEXT NOT NULL, payment_status TEXT NOT NULL,
      fulfillment TEXT NOT NULL, description TEXT NOT NULL, PRIMARY KEY(customer,external_id));
    CREATE TABLE IF NOT EXISTS merchant_sync (customer TEXT PRIMARY KEY, last_synced REAL NOT NULL);
    ''')

def install(app,engine):
    @app.post('/api/purchases/sync')
    def sync():
        uid=engine.auth.customer_id()
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            samples=[('grocery-001','Fresh Basket',79900,'4821','SUCCESS','CANCELLED','Simulated groceries: payment received, order cancelled.'),('electronics-001','Studio Supply',129900,'4821','SUCCESS','CANCELLED','Simulated headphones: payment received, order cancelled.'),('coffee-001','Daily Brew',24900,'','PENDING','PENDING','Simulated UPI purchase: payment confirmation pending.')]
            for row in samples:
                db.execute('INSERT OR IGNORE INTO merchant_feed VALUES (?,?,?,?,?,?,?,?)',(uid,*row))
            added=0
            for source in db.execute('SELECT * FROM merchant_feed WHERE customer=?',(uid,)).fetchall():
                oid='MERCHANT-'+hashlib.sha256((uid+':'+source['external_id']).encode()).hexdigest()[:24]
                added+=db.execute('INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?,?,?)',(oid,uid,source['merchant'],source['amount'],source['last4'],source['payment_status'],source['fulfillment'],source['description'])).rowcount
            for source in db.execute("SELECT external_id FROM merchant_feed WHERE customer=? AND external_id='coffee-001'",(uid,)):
                oid='MERCHANT-'+hashlib.sha256((uid+':'+source['external_id']).encode()).hexdigest()[:24]
                db.execute('INSERT OR IGNORE INTO mock_payment_updates VALUES (?,?,?,?)',(oid,3,'SUCCESS','CANCELLED'))
            now=time.time()
            db.execute('INSERT INTO merchant_sync VALUES (?,?) ON CONFLICT(customer) DO UPDATE SET last_synced=excluded.last_synced',(uid,now))
            return {'source':'mock_merchant','simulated':True,'added':added,'last_synced':now}

    @app.get('/api/purchases')
    def purchases():
        uid=engine.auth.customer_id()
        with engine.connection() as db:
            orders=[dict(r) for r in db.execute('SELECT o.*,c.id AS case_id,c.state AS case_state FROM orders o LEFT JOIN cases c ON c.order_id=o.id WHERE o.customer=? ORDER BY o.id',(uid,))]
            row=db.execute('SELECT last_synced FROM merchant_sync WHERE customer=?',(uid,)).fetchone()
            return {'orders':orders,'source':'mock_merchant','simulated':True,'last_synced':row['last_synced'] if row else None}
