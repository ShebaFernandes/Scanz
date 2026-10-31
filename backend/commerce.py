"""Offline merchant extensions: catalog, constrained delivery, demo outbox, analytics."""
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, Field

try:
    from . import auth
except ImportError:
    import auth

CATEGORIES={
 'groceries':{'name':'Groceries','merchant':'Fresh Basket','title':'Vegetarian pantry bundle','tag':'vegetarian'},
 'electronics':{'name':'Electronics','merchant':'Circuit Hub','title':'USB-C desk essentials','tag':'usb-c'},
 'fashion':{'name':'Fashion','merchant':'Thread & Weave','title':'Everyday cotton tee - M','tag':'size-m'},
}
CHANNELS={'in_app','email','sms','whatsapp'}


def initialize(db):
    columns={r['name'] for r in db.execute('PRAGMA table_info(rescue_checkouts)')}
    if 'category' not in columns:db.execute("ALTER TABLE rescue_checkouts ADD COLUMN category TEXT NOT NULL DEFAULT 'groceries'")
    db.executescript('''
    CREATE TABLE IF NOT EXISTS commerce_context (checkout_id TEXT PRIMARY KEY,original_sku TEXT,original_title TEXT,compatibility TEXT,accepted_alternative TEXT);
    CREATE TABLE IF NOT EXISTS commerce_products (id TEXT PRIMARY KEY,checkout_id TEXT,sku TEXT,title TEXT,unit_price INTEGER,compatibility TEXT);
    CREATE TABLE IF NOT EXISTS delivery_lanes (id TEXT PRIMARY KEY,name TEXT,eta_minutes INTEGER,cost_paise INTEGER,radius_km INTEGER,available INTEGER CHECK(available>=0),capacity INTEGER,same_day INTEGER);
    CREATE TABLE IF NOT EXISTS delivery_quotes (id TEXT PRIMARY KEY,checkout_id TEXT,sku TEXT,lane_id TEXT,objective TEXT,distance_km INTEGER,deadline_minutes INTEGER,budget_paise INTEGER,created REAL,expires REAL);
    CREATE TABLE IF NOT EXISTS delivery_plans (checkout_id TEXT PRIMARY KEY,quote_id TEXT,lane_id TEXT,status TEXT,cost_paise INTEGER,eta_minutes INTEGER,objective TEXT);
    CREATE TABLE IF NOT EXISTS channel_preferences (checkout_id TEXT,channel TEXT,enabled INTEGER,PRIMARY KEY(checkout_id,channel));
    CREATE TABLE IF NOT EXISTS communication_outbox (id TEXT PRIMARY KEY,case_id TEXT,checkout_id TEXT,channel TEXT,phase_key TEXT,body TEXT,status TEXT,attempts INTEGER DEFAULT 0,due REAL,failure_mode TEXT,created REAL,delivered REAL,UNIQUE(case_id,channel,phase_key));
    ''')
    db.executemany('INSERT OR IGNORE INTO delivery_lanes VALUES (?,?,?,?,?,?,?,?)',[
      ('express','City Express',90,4900,15,40,40,1),
      ('green','Green Batch',180,1900,25,60,60,1),
      ('standard','Standard Next Day',1440,0,40,100,100,0),
    ])


def own_checkout(db,coid):
    row=db.execute('SELECT * FROM rescue_checkouts WHERE id=?',(coid,)).fetchone()
    if not row or not auth.can_access_checkout(db,row):raise HTTPException(404,'Checkout not found')
    return dict(row)


def audit(db,coid,title,detail,kind='done'):
    link=db.execute('SELECT case_id FROM rescue_links WHERE checkout_id=?',(coid,)).fetchone()
    if link:db.execute('INSERT INTO events(case_id,title,detail,kind,created) VALUES (?,?,?,?,?)',(link['case_id'],title,detail,kind,time.time()))


def configure(db,coid,category,now):
    if category not in CATEGORIES:raise HTTPException(422,'Unknown merchant category')
    co=own_checkout(db,coid);spec=CATEGORIES[category]
    db.execute('UPDATE rescue_checkouts SET category=?,merchant=? WHERE id=?',(category,spec['merchant'],coid))
    db.execute('UPDATE rescue_inventory SET title=? WHERE sku=?',(spec['title'],co['sku']))
    db.execute('INSERT OR IGNORE INTO commerce_context VALUES (?,?,?,?,NULL)',(coid,co['sku'],spec['title'],spec['tag']))
    db.execute('INSERT OR IGNORE INTO channel_preferences VALUES (?,?,?)',(coid,'in_app',1))
    variants=[('matched',spec['title']+' - alternative',co['unit_price'],spec['tag'],3),
              ('premium',spec['title']+' - premium',co['unit_price']+10000,spec['tag'],4),
              ('incompatible',spec['title']+' - different variant',co['unit_price'],'different-variant',4)]
    for suffix,title,price,tag,stock in variants:
        sku=co['sku']+'-'+suffix;pid=coid+'-'+suffix
        db.execute('INSERT OR IGNORE INTO rescue_inventory VALUES (?,?,?,?)',(sku,title,stock,1))
        db.execute('INSERT OR IGNORE INTO commerce_products VALUES (?,?,?,?,?,?)',(pid,coid,sku,title,price,tag))


def alternatives(db,coid):
    co=own_checkout(db,coid);ctx=db.execute('SELECT * FROM commerce_context WHERE checkout_id=?',(coid,)).fetchone()
    if not ctx:return []
    result=[]
    for row in db.execute('SELECT p.*,i.available,i.delivery_today FROM commerce_products p JOIN rescue_inventory i ON p.sku=i.sku WHERE p.checkout_id=?',(coid,)):
        p=dict(row);reasons=[]
        if p['unit_price']*co['quantity']!=co['amount']:reasons.append('Price changes require a separate payment/refund flow; not offered here.')
        if p['compatibility']!=ctx['compatibility']:reasons.append('Required size, connector, or dietary variant does not match.')
        if p['available']<co['quantity']:reasons.append('Insufficient stock.')
        if p['sku']==co['sku']:reasons.append('Already selected.')
        p['eligible']=not reasons;p['reasons']=reasons or ['Same paid total and required variant; in stock. Customer consent is required.']
        result.append(p)
    return sorted(result,key=lambda p:(not p['eligible'],-p['available'],p['id']))


def ensure_mutable(db,coid):
    co=own_checkout(db,coid)
    link=db.execute('SELECT * FROM rescue_links WHERE checkout_id=?',(coid,)).fetchone()
    if not link:raise HTTPException(409,'Wait for a rescue case before changing fulfillment.')
    case=dict(db.execute('SELECT * FROM cases WHERE id=?',(link['case_id'],)).fetchone())
    if case['state']=='CLOSED_REVIEWED':raise HTTPException(409,'This case has been closed.')
    if link['intent']!='restore':raise HTTPException(409,'Select the restore path before changing fulfillment.')
    if db.execute("SELECT 1 FROM merchant_orders WHERE checkout_id=? AND status='CONFIRMED'",(coid,)).fetchone():raise HTTPException(409,'An order already exists. A substitution or delivery change cannot overwrite it.')
    if db.execute('SELECT 1 FROM provider_refunds WHERE order_id=?',(case['order_id'],)).fetchone() or db.execute('SELECT 1 FROM refunds WHERE order_id=?',(case['order_id'],)).fetchone():raise HTTPException(409,'Refund acceptance blocks changes to fulfillment.')
    if link['resume_state']=='RESCUE_RECONCILE':raise HTTPException(409,'Reconcile uncertain merchant creation before changing resources.')
    return co,dict(link),case


def release_delivery(db,coid,allow_committed=False):
    plan=db.execute('SELECT * FROM delivery_plans WHERE checkout_id=?',(coid,)).fetchone()
    if not plan or plan['status']=='CANCELLED':return
    if plan['status']=='COMMITTED':
        if not allow_committed:return
        db.execute('UPDATE delivery_lanes SET available=MIN(capacity,available+1) WHERE id=?',(plan['lane_id'],))
    db.execute("UPDATE delivery_plans SET status='CANCELLED' WHERE checkout_id=?",(coid,))


def accept_alternative(db,engine,coid,pid,consent,now):
    if not consent:raise HTTPException(409,'Explicit customer consent is required before substitution.')
    co,link,case=ensure_mutable(db,coid)
    candidate=next((p for p in alternatives(db,coid) if p['id']==pid),None)
    if not candidate:raise HTTPException(404,'Alternative not found for this checkout')
    if not candidate['eligible']:raise HTTPException(409,'Alternative is not eligible: '+' '.join(candidate['reasons']))
    engine.rescue.release(db,co);release_delivery(db,coid)
    db.execute('UPDATE rescue_checkouts SET sku=?,unit_price=? WHERE id=?',(candidate['sku'],candidate['unit_price'],coid))
    db.execute('UPDATE commerce_context SET accepted_alternative=? WHERE checkout_id=?',(pid,coid))
    db.execute("UPDATE cases SET state='RESCUE_VERIFY',due=?,updated=? WHERE id=?",(now+engine.STEPS_SECONDS,now,case['id']))
    audit(db,coid,'Alternative explicitly accepted',candidate['title']+' selected with customer consent. Original checkout identity and paid total are preserved; stock will be rechecked.')


def quote_delivery(db,coid,objective,distance,deadline,budget,now,persist=True):
    co=own_checkout(db,coid);link=db.execute('SELECT * FROM rescue_links WHERE checkout_id=?',(coid,)).fetchone()
    inv=db.execute('SELECT * FROM rescue_inventory WHERE sku=?',(co['sku'],)).fetchone()
    today=bool(link and link['today_only']);options=[]
    for row in db.execute('SELECT * FROM delivery_lanes'):
        lane=dict(row);reasons=[]
        if lane['available']<=0:reasons.append('No remaining carrier capacity.')
        if distance>lane['radius_km']:reasons.append('Outside the simulated service radius.')
        if lane['eta_minutes']>deadline:reasons.append('Misses the selected deadline.')
        if lane['cost_paise']>budget:reasons.append('Exceeds the merchant fulfillment budget.')
        if today and not lane['same_day']:reasons.append('Customer requires same-day delivery.')
        if lane['same_day'] and not inv['delivery_today']:reasons.append('This item is not ready for same-day dispatch.')
        rank=lane['cost_paise'] if objective=='cheapest' else lane['eta_minutes'] if objective=='fastest' else .55*(lane['cost_paise']/max(1,budget))+.45*(lane['eta_minutes']/max(1,deadline))
        qid='QUOTE-'+uuid.uuid4().hex[:10].upper()
        if persist:db.execute('INSERT INTO delivery_quotes VALUES (?,?,?,?,?,?,?,?,?,?)',(qid,coid,co['sku'],lane['id'],objective,distance,deadline,budget,now,now+300))
        options.append({**lane,'quote_id':qid,'eligible':not reasons,'reasons':reasons or ['Meets service radius, deadline, capacity, and merchant budget constraints.'],'rank':rank,'expires':now+300})
    options.sort(key=lambda o:(not o['eligible'],o['rank'],o['id']))
    return options


def select_delivery(db,coid,qid,now,check_scope=True):
    if check_scope:ensure_mutable(db,coid)
    co=own_checkout(db,coid);q=db.execute('SELECT * FROM delivery_quotes WHERE id=? AND checkout_id=?',(qid,coid)).fetchone()
    if not q or q['expires']<now or q['sku']!=co['sku']:raise HTTPException(409,'Quote expired or the selected product changed. Recalculate the plan.')
    # Recheck every constraint, including capacity, at selection time.
    options=quote_delivery(db,coid,q['objective'],q['distance_km'],q['deadline_minutes'],q['budget_paise'],now,False)
    lane=next(o for o in options if o['id']==q['lane_id'])
    if not lane['eligible']:raise HTTPException(409,'Plan is no longer feasible: '+' '.join(lane['reasons']))
    old=db.execute('SELECT * FROM delivery_plans WHERE checkout_id=?',(coid,)).fetchone()
    if old and old['status']=='COMMITTED':raise HTTPException(409,'Delivery capacity is already committed to an order.')
    db.execute("INSERT INTO delivery_plans VALUES (?,?,?,?,?,?,?) ON CONFLICT(checkout_id) DO UPDATE SET quote_id=excluded.quote_id,lane_id=excluded.lane_id,status=excluded.status,cost_paise=excluded.cost_paise,eta_minutes=excluded.eta_minutes,objective=excluded.objective",(coid,qid,lane['id'],'SELECTED',lane['cost_paise'],lane['eta_minutes'],q['objective']))
    audit(db,coid,'Delivery plan selected',lane['name']+f" / estimated {lane['eta_minutes']} minutes / merchant cost INR {lane['cost_paise']/100:.2f}. No additional customer charge.")


def commit_delivery(db,co,now):
    plan=db.execute('SELECT * FROM delivery_plans WHERE checkout_id=?',(co['id'],)).fetchone()
    if plan and plan['status']=='COMMITTED':return True
    if not plan or plan['status']=='CANCELLED':
        options=quote_delivery(db,co['id'],'balanced',7,1440,6000,now)
        eligible=[o for o in options if o['eligible']]
        if not eligible:return False
        select_delivery(db,co['id'],eligible[0]['quote_id'],now,False)
        plan=db.execute('SELECT * FROM delivery_plans WHERE checkout_id=?',(co['id'],)).fetchone()
    q=db.execute('SELECT * FROM delivery_quotes WHERE id=?',(plan['quote_id'],)).fetchone()
    if not q or q['expires']<now or q['sku']!=co['sku']:return False
    feasible=next(o for o in quote_delivery(db,co['id'],q['objective'],q['distance_km'],q['deadline_minutes'],q['budget_paise'],now,False) if o['id']==plan['lane_id'])
    if not feasible['eligible']:return False
    changed=db.execute('UPDATE delivery_lanes SET available=available-1 WHERE id=? AND available>0',(plan['lane_id'],)).rowcount
    if not changed:return False
    db.execute("UPDATE delivery_plans SET status='COMMITTED' WHERE checkout_id=?",(co['id'],))
    return True


MILESTONES={'RESCUE_VERIFY','RESCUE_RECONCILE','RESCUE_PAUSED','NEEDS_HUMAN','WAITING_CUSTOMER','ORDER_RESTORED','REFUND_PENDING','RESOLVED','INFORMATION_PROVIDED','CLOSED_REVIEWED'}
def notifications(db,now,step):
    for row in db.execute('SELECT c.id,c.state,c.order_id,l.checkout_id,l.intent,l.pending_intent,l.human_decisions FROM cases c JOIN rescue_links l ON l.case_id=c.id').fetchall():
        if row['state'] not in MILESTONES:continue
        phase=':'.join([row['state'],row['intent'],row['pending_intent'] or '',str(row['human_decisions'])])
        body=f"Demo update for {row['checkout_id']}: {row['state'].lower().replace('_',' ')}. This is a simulated notification; no real payment or message was sent."
        for pref in db.execute('SELECT channel FROM channel_preferences WHERE checkout_id=? AND enabled=1',(row['checkout_id'],)).fetchall():
            db.execute('INSERT OR IGNORE INTO communication_outbox(id,case_id,checkout_id,channel,phase_key,body,status,due,created) VALUES (?,?,?,?,?,?,?,?,?)',('MSG-'+uuid.uuid4().hex[:10].upper(),row['id'],row['checkout_id'],pref['channel'],phase,body,'QUEUED',now+step,now))
    for row in db.execute("SELECT * FROM communication_outbox WHERE status IN ('QUEUED','RETRY_SCHEDULED') AND due<=?",(now,)).fetchall():
        pref=db.execute('SELECT enabled FROM channel_preferences WHERE checkout_id=? AND channel=?',(row['checkout_id'],row['channel'])).fetchone()
        if not pref or not pref['enabled']:
            db.execute("UPDATE communication_outbox SET status='CANCELLED',due=NULL WHERE id=?",(row['id'],));continue
        attempt=row['attempts']+1
        fail=row['failure_mode']=='always' or (row['failure_mode']=='once' and row['attempts']==0)
        status=('FAILED' if attempt>=3 else 'RETRY_SCHEDULED') if fail else 'SIMULATED_DELIVERED'
        db.execute('UPDATE communication_outbox SET status=?,attempts=?,due=?,delivered=? WHERE id=?',(status,attempt,now+max(step,.01)*2**attempt if status=='RETRY_SCHEDULED' else None,now if status=='SIMULATED_DELIVERED' else None,row['id']))


def scope(db,coid):
    co=own_checkout(db,coid)
    ctx=db.execute('SELECT * FROM commerce_context WHERE checkout_id=?',(coid,)).fetchone()
    plan=db.execute('SELECT p.*,l.name AS carrier FROM delivery_plans p JOIN delivery_lanes l ON l.id=p.lane_id WHERE checkout_id=?',(coid,)).fetchone()
    return {'category':co['category'],'context':dict(ctx) if ctx else None,'alternatives':alternatives(db,coid),'delivery_plan':dict(plan) if plan else None,'channels':{name:bool((row:=db.execute('SELECT enabled FROM channel_preferences WHERE checkout_id=? AND channel=?',(coid,name)).fetchone()) and row['enabled']) for name in sorted(CHANNELS)},'messages':[dict(row) for row in db.execute('SELECT * FROM communication_outbox WHERE checkout_id=? ORDER BY created DESC LIMIT 60',(coid,))],'simulated':True}


class AlternativeRequest(BaseModel):
    product_id: str=Field(max_length=100)
    consent: bool
class QuoteRequest(BaseModel):
    objective: Literal['cheapest','fastest','balanced']='balanced'
    distance_km: int=Field(default=7,ge=1,le=100)
    deadline_minutes: int=Field(default=1440,ge=30,le=2880)
    budget_paise: int=Field(default=6000,ge=0,le=20000)
class SelectQuote(BaseModel):
    quote_id: str=Field(max_length=100)
class ChannelRequest(BaseModel):
    channel: Literal['in_app','email','sms','whatsapp']
    enabled: bool
    consent: bool=False
class TestMessage(BaseModel):
    channel: Literal['in_app','email','sms','whatsapp']='in_app'
    failure: Literal['none','once','always']='once'


def analytics(db,category,days,now):
    since=0 if days==0 else now-days*86400
    scope,params=auth.checkout_scope('co')
    rows=db.execute("SELECT co.*,l.case_id,l.order_ref,l.human_decisions,c.state,c.created AS case_created,c.updated,m.status AS merchant_status,m.amount AS merchant_amount FROM rescue_checkouts co LEFT JOIN rescue_links l ON l.checkout_id=co.id LEFT JOIN cases c ON c.id=l.case_id LEFT JOIN merchant_orders m ON m.checkout_id=co.id WHERE "+scope+" AND co.created>=? AND (?='all' OR co.category=?) ORDER BY co.created",(*params,since,category,category)).fetchall()
    groups={key:{'category':key,'label':spec['name'],'checkouts':0,'restored':0,'refunded':0,'needs_human':0,'waiting':0,'restored_value_paise':0} for key,spec in CATEGORIES.items()}
    totals={'checkouts':len(rows),'detected_cases':0,'restored_orders':0,'restored_value_paise':0,'refunds_accepted_paise':0,'refunds_credited_paise':0,'human_decisions':0,'open_cases':0,'delivery_cost_committed_paise':0,'alternatives_accepted':0,'model_tokens':0}
    trend=defaultdict(lambda:{'checkouts':0,'restored':0,'refunded':0});durations=[];ids=[]
    for raw in rows:
        row=dict(raw);g=groups.get(row['category'],groups['groceries']);g['checkouts']+=1
        day=datetime.fromtimestamp(row['created'],timezone(timedelta(hours=5,minutes=30))).strftime('%Y-%m-%d');trend[day]['checkouts']+=1
        if not row['case_id']:continue
        ids.append(row['id']);totals['detected_cases']+=1;totals['human_decisions']+=row['human_decisions'] or 0
        c=db.execute('SELECT order_id,ai_tokens FROM cases WHERE id=?',(row['case_id'],)).fetchone();totals['model_tokens']+=c['ai_tokens']
        refund=db.execute('SELECT * FROM refunds WHERE order_id=?',(c['order_id'],)).fetchone();provider=db.execute('SELECT * FROM provider_refunds WHERE order_id=?',(c['order_id'],)).fetchone()
        if provider:totals['refunds_accepted_paise']+=provider['amount']
        if refund and refund['status']=='CREDITED':totals['refunds_credited_paise']+=refund['amount'];g['refunded']+=1;trend[day]['refunded']+=1
        if row['order_ref'] and row['merchant_status']=='CONFIRMED' and not provider:
            totals['restored_orders']+=1;totals['restored_value_paise']+=row['merchant_amount'];g['restored']+=1;g['restored_value_paise']+=row['merchant_amount'];trend[day]['restored']+=1
        if row['state']=='NEEDS_HUMAN':g['needs_human']+=1
        if row['state'] in {'WAITING_CUSTOMER','RESCUE_PAUSED'}:g['waiting']+=1
        if row['state'] not in {'ORDER_RESTORED','RESOLVED','INFORMATION_PROVIDED','CLOSED_REVIEWED'}:totals['open_cases']+=1
        elif row['updated']>=row['case_created']:durations.append(row['updated']-row['case_created'])
        plan=db.execute("SELECT cost_paise FROM delivery_plans WHERE checkout_id=? AND status='COMMITTED'",(row['id'],)).fetchone()
        if plan:totals['delivery_cost_committed_paise']+=plan['cost_paise']
        ctx=db.execute('SELECT accepted_alternative FROM commerce_context WHERE checkout_id=?',(row['id'],)).fetchone()
        if ctx and ctx['accepted_alternative']:totals['alternatives_accepted']+=1
    messages=[]
    channel_scope,channel_params=auth.checkout_scope('c')
    for channel in sorted(CHANNELS):
        counts=defaultdict(int);retries=0
        for row in db.execute('SELECT o.status,o.attempts FROM communication_outbox o JOIN rescue_checkouts c ON c.id=o.checkout_id WHERE '+channel_scope+' AND c.created>=? AND (?=\'all\' OR c.category=?) AND o.channel=?',(*channel_params,since,category,category,channel)):
            counts[row['status']]+=1;retries+=max(0,row['attempts']-1)
        messages.append({'channel':channel,'simulated_delivered':counts['SIMULATED_DELIVERED'],'queued':counts['QUEUED']+counts['RETRY_SCHEDULED'],'failed':counts['FAILED'],'cancelled':counts['CANCELLED'],'retry_attempts':retries})
    totals['mean_recorded_completion_seconds']=round(sum(durations)/len(durations),1) if durations else None
    return {'generated_at':now,'category':category,'days':days,'totals':totals,'categories':[v for k,v in groups.items() if category=='all' or category==k],'trend':[{'day':day,**value} for day,value in sorted(trend.items())],'channels':messages,'disclaimer':'Live local demo records only; evaluation fixtures are excluded. Money and channel deliveries are simulated. Completion intervals use recorded case timestamps, exclude pre-detection grace, and are not bank settlement performance.'}


def install(app,engine):
    @app.get('/api/commerce/categories')
    def categories():return {'categories':[{'id':key,**value} for key,value in CATEGORIES.items()]}

    @app.get('/api/commerce/analytics')
    def report(category: Literal['all','groceries','electronics','fashion']='all',days: int=7):
        if days not in {0,1,7,30}:raise HTTPException(422,'Choose a 1, 7, 30 day window or 0 for all time.')
        with engine.connection() as db:return analytics(db,category,days,time.time())

    @app.get('/api/commerce/{checkout_id}')
    def get(checkout_id: str):
        with engine.connection() as db:return scope(db,checkout_id)

    @app.post('/api/commerce/{checkout_id}/alternative')
    def substitute(checkout_id: str,body: AlternativeRequest):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');accept_alternative(db,engine,checkout_id,body.product_id,body.consent,time.time());return scope(db,checkout_id)

    @app.post('/api/commerce/{checkout_id}/delivery-quotes')
    def quotes(checkout_id: str,body: QuoteRequest):
        with engine.connection() as db:
            return {'options':quote_delivery(db,checkout_id,body.objective,body.distance_km,body.deadline_minutes,body.budget_paise,time.time()),'simulated':True,'cost_borne_by':'merchant'}

    @app.post('/api/commerce/{checkout_id}/delivery-plan')
    def plan(checkout_id: str,body: SelectQuote):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');select_delivery(db,checkout_id,body.quote_id,time.time())
            row=db.execute('SELECT case_id FROM rescue_links WHERE checkout_id=?',(checkout_id,)).fetchone()
            db.execute("UPDATE cases SET state='RESCUE_VERIFY',due=?,updated=? WHERE id=?",(time.time()+engine.STEPS_SECONDS,time.time(),row['case_id']))
            return scope(db,checkout_id)

    @app.post('/api/commerce/{checkout_id}/channels')
    def channels(checkout_id: str,body: ChannelRequest):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');own_checkout(db,checkout_id)
            if body.enabled and not body.consent:raise HTTPException(409,'Explicit opt-in is required. These are simulated channels, not real deliveries.')
            db.execute('INSERT INTO channel_preferences VALUES (?,?,?) ON CONFLICT(checkout_id,channel) DO UPDATE SET enabled=excluded.enabled',(checkout_id,body.channel,int(body.enabled)))
            if not body.enabled:db.execute("UPDATE communication_outbox SET status='CANCELLED',due=NULL WHERE checkout_id=? AND channel=? AND status IN ('QUEUED','RETRY_SCHEDULED')",(checkout_id,body.channel))
            return scope(db,checkout_id)

    @app.post('/api/commerce/{checkout_id}/test-message')
    def test_message(checkout_id: str,body: TestMessage):
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');own_checkout(db,checkout_id)
            pref=db.execute('SELECT enabled FROM channel_preferences WHERE checkout_id=? AND channel=?',(checkout_id,body.channel)).fetchone()
            link=db.execute('SELECT case_id FROM rescue_links WHERE checkout_id=?',(checkout_id,)).fetchone()
            if not pref or not pref['enabled']:raise HTTPException(409,'Opt in to this simulated channel first.')
            if not link:raise HTTPException(409,'Wait for a support case before testing a delivery.')
            token=uuid.uuid4().hex[:10].upper();now=time.time()
            db.execute('INSERT INTO communication_outbox(id,case_id,checkout_id,channel,phase_key,body,status,due,failure_mode,created) VALUES (?,?,?,?,?,?,?,?,?,?)',('MSG-'+token,link['case_id'],checkout_id,body.channel,'TEST-'+token,'SIMULATED test update. No actual email, SMS, or WhatsApp message is sent.','QUEUED',now+engine.STEPS_SECONDS,body.failure,now))
            return scope(db,checkout_id)
