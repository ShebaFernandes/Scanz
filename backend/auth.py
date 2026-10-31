"""Server-side sessions, password hashing, CSRF and request-scoped ownership."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import dotenv_values
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

principal=ContextVar('resolve_principal',default=None)
COOKIE='resolve_session'
CSRF='resolve_csrf'
SESSION_SECONDS=8*3600
def setting(name,default=''):
    return os.environ.get(name) or dotenv_values(Path(__file__).resolve().parents[1]/'.env').get(name) or default

def customer_id():return (principal.get() or {}).get('id','customer-demo')
def request_customer():return (principal.get() or {}).get('id')
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def hash_password(value):
    salt=secrets.token_bytes(16)
    key=hashlib.scrypt(value.encode(),salt=salt,n=32768,r=8,p=1,maxmem=64*1024*1024)
    return 'scrypt$'+salt.hex()+'$'+key.hex()
def verify_password(value,stored):
    try:
        _,salt,key=stored.split('$')
        calculated=hashlib.scrypt(value.encode(),salt=bytes.fromhex(salt),n=32768,r=8,p=1,maxmem=64*1024*1024)
        return hmac.compare_digest(calculated.hex(),key)
    except (ValueError,TypeError):return False

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS auth_users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'customer',created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS auth_sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,csrf_hash TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS support_assignments(order_id TEXT NOT NULL,operator_id TEXT NOT NULL,PRIMARY KEY(order_id,operator_id));
    CREATE TABLE IF NOT EXISTS auth_attempts(bucket TEXT PRIMARY KEY,count INTEGER NOT NULL,reset REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS security_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,event TEXT NOT NULL,resource TEXT,created REAL NOT NULL);
    ''')
    for table in ['evaluation_runs','rescue_runs']:
        columns={r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
        if columns and 'owner' not in columns:db.execute('ALTER TABLE '+table+' ADD COLUMN owner TEXT')

def audit(db,event,resource=None,uid=None):db.execute('INSERT INTO security_audit(user_id,event,resource,created) VALUES (?,?,?,?)',(uid or request_customer(),event,resource,time.time()))
def limited(db,key,limit,seconds=900):
    now=time.time();bucket=digest(key)
    db.execute('INSERT INTO auth_attempts VALUES (?,?,?) ON CONFLICT(bucket) DO UPDATE SET count=CASE WHEN reset<=? THEN 1 ELSE count+1 END,reset=CASE WHEN reset<=? THEN ? ELSE reset END',(bucket,1,now+seconds,now,now,now+seconds))
    return db.execute('SELECT count FROM auth_attempts WHERE bucket=?',(bucket,)).fetchone()[0]>limit

def normalized_origin(value):
    """Compare exact web origins, allowing harmless dashboard formatting."""
    try:
        parsed=urlsplit(value.strip())
        if parsed.scheme.lower() not in {'http','https'} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None or parsed.path not in {'','/'} or parsed.query or parsed.fragment:
            return None
        scheme=parsed.scheme.lower();port=parsed.port
        host=parsed.hostname.lower()
        if ':' in host:host='['+host+']'
        suffix='' if port is None or (scheme,port) in {('https',443),('http',80)} else ':'+str(port)
        return scheme+'://'+host+suffix
    except (ValueError,AttributeError):
        return None

def origin_allowed(request):
    origin=request.headers.get('origin')
    configured=setting('AUTH_ALLOWED_ORIGINS','http://localhost:3000,http://127.0.0.1:3000')
    allowed={normalized_origin(value) for value in configured.split(',')}
    # Never trust forwarded host/origin headers to expand this allowlist.
    allowed.add(normalized_origin(str(request.base_url)))
    allowed.discard(None)
    if origin is not None:
        # Explicit configured origins are authoritative, including when a proxy
        # forwards a cross-site Fetch Metadata header. CSRF checks still apply.
        normalized=normalized_origin(origin)
        return normalized is not None and normalized in allowed
    # Non-browser API clients may omit Origin; cross-site browser writes may not.
    return request.headers.get('sec-fetch-site','').lower()!='cross-site'

class Credentials(BaseModel):
    model_config=ConfigDict(extra='forbid')
    email:str=Field(min_length=3,max_length=254)
    password:str=Field(min_length=1,max_length=128)
class Registration(Credentials):
    name:str=Field(min_length=1,max_length=80)

def public(user):return {key:user[key] for key in ('id','email','name','role')}
def staff_path(path,body):
    if path in {'/api/rescue/settings','/api/rescue/mock-events','/api/lab/start','/api/evaluations','/api/rescue-scorecards','/api/commerce/analytics'}:return True
    if path.startswith(('/api/evaluations/','/api/integrations/')):return True
    if path.endswith(('/review','/simulate','/confirm-original','/test-message')):return True
    if path.endswith('/decision'):return body.get('decision')!='abandon'
    return False

class SessionMiddleware(BaseHTTPMiddleware):
    def __init__(self,app,engine):super().__init__(app);self.engine=engine
    async def dispatch(self,request,call_next):
        path=request.url.path
        if not path.startswith('/api/') or path=='/api/health':return await call_next(request)
        if request.method not in {'GET','HEAD','OPTIONS'} and not origin_allowed(request):return JSONResponse({'detail':'This request origin is not allowed.'},403)
        if path in {'/api/auth/login','/api/auth/register','/api/auth/demo'}:return await call_next(request)
        with self.engine.connection() as db:
            session=db.execute('SELECT s.*,u.id,u.email,u.name,u.role FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?',(digest(request.cookies.get(COOKIE,'')),time.time())).fetchone()
        if not session:return JSONResponse({'detail':'Please sign in.'},401)
        if request.method not in {'GET','HEAD','OPTIONS'}:
            value=request.headers.get('x-csrf-token','')
            if not value or not hmac.compare_digest(digest(value),session['csrf_hash']):return JSONResponse({'detail':'Your security token is missing or expired. Refresh and try again.'},403)
        body={}
        if request.method=='POST':
            try:body=await request.json()
            except (ValueError,TypeError):pass
        if not isinstance(body,dict):body={}
        if staff_path(path,body) and session['role']!='operator':return JSONResponse({'detail':'Staff access is required.'},403)
        if session['id'].startswith('GUEST-') and (path.startswith('/api/integrations/') or (path=='/api/rescue/settings' and request.method=='POST') or body.get('agent_mode') in {'ai','openai'} or path.endswith(('/enable-ai','retry-ai','/reply'))):
            return JSONResponse({'detail':'Guest workspaces support offline mocked operations only.'},403)
        token=principal.set(public(session))
        try:
            response=await call_next(request)
            if request.method not in {'GET','HEAD','OPTIONS'} or response.status_code in {403,404}:
                with self.engine.connection() as db:audit(db,'api_request_'+str(response.status_code),path[:300])
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
            return response
        finally:principal.reset(token)

def install(app,engine):
    app.add_middleware(SessionMiddleware,engine=engine)
    def issue(db,user,response):
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);now=time.time()
        db.execute('INSERT INTO auth_sessions VALUES (?,?,?,?,?)',(digest(token),user['id'],digest(csrf),now,now+SESSION_SECONDS))
        secure=setting('AUTH_COOKIE_SECURE','false').lower()=='true'
        response.set_cookie(COOKIE,token,max_age=SESSION_SECONDS,httponly=True,secure=secure,samesite='lax',path='/api')
        response.set_cookie(CSRF,csrf,max_age=SESSION_SECONDS,httponly=False,secure=secure,samesite='lax',path='/')
        response.headers['Cache-Control']='no-store'
        return {'user':public(user)}
    @app.post('/api/auth/demo')
    def demo(request:Request,response:Response):
        if setting('AUTH_DEMO_MODE','false').lower()!='true':raise HTTPException(403,'Guest demo is disabled.')
        if setting('PAYTM_MCP_ENABLED','false').lower()=='true':
            raise HTTPException(503,'Guest workspace requires live Paytm MCP to be disabled.')
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute("SELECT u.* FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.id LIKE 'GUEST-%'",(digest(request.cookies.get(COOKIE,'')),time.time())).fetchone()
            if old:
                # Local demo operators remain scoped to their own synthetic records.
                db.execute("UPDATE auth_users SET role='operator' WHERE id=?",(old['id'],))
                old=db.execute('SELECT * FROM auth_users WHERE id=?',(old['id'],)).fetchone()
                db.execute('DELETE FROM auth_sessions WHERE token_hash=?',(digest(request.cookies.get(COOKIE,'')),))
                return issue(db,old,response)
            if limited(db,'guest-ip:'+(request.client.host if request.client else 'unknown'),30):
                db.commit();raise HTTPException(429,'Too many new demo workspaces. Try again later.')
            uid='GUEST-'+secrets.token_hex(16)
            db.execute('INSERT INTO auth_users VALUES (?,?,?,?,?,?)',(uid,uid.lower()+'@demo.invalid','Demo visitor',hash_password(secrets.token_urlsafe(32)),'operator',time.time()))
            user=db.execute('SELECT * FROM auth_users WHERE id=?',(uid,)).fetchone()
            audit(db,'guest_demo_started',uid=uid)
            return issue(db,user,response)

    def normalize(email):
        value=email.strip().lower()
        if value.count('@')!=1 or not all(value.split('@')) or any(c.isspace() for c in value):raise HTTPException(422,'Enter a valid email address.')
        return value
    @app.post('/api/auth/register')
    def register(body:Registration,request:Request,response:Response):
        email=normalize(body.email)
        if len(body.password)<12:raise HTTPException(422,'Use a password of at least 12 characters.')
        if not body.name.strip():raise HTTPException(422,'Enter your name.')
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            blocked=limited(db,'register-ip:'+(request.client.host if request.client else 'unknown'),12)
        if blocked:raise HTTPException(429,'Too many attempts. Try again in 15 minutes.')
        encoded=hash_password(body.password)
        with engine.connection() as db:
            try:db.execute('INSERT INTO auth_users VALUES (?,?,?,?,?,?)',('USER-'+secrets.token_hex(16),email,body.name.strip(),encoded,'customer',time.time()))
            except sqlite3.IntegrityError:raise HTTPException(409,'Unable to create this account. Try signing in instead.')
            user=db.execute('SELECT * FROM auth_users WHERE email=?',(email,)).fetchone();audit(db,'registered',uid=user['id']);return issue(db,user,response)
    @app.post('/api/auth/login')
    def login(body:Credentials,request:Request,response:Response):
        email=normalize(body.email);ip=request.client.host if request.client else 'unknown'
        with engine.connection() as db:
            db.execute('BEGIN IMMEDIATE');blocked=limited(db,'login-email:'+email,10)|limited(db,'login-ip:'+ip,40)
            user=db.execute('SELECT * FROM auth_users WHERE email=?',(email,)).fetchone()
        if blocked:raise HTTPException(429,'Too many attempts. Try again in 15 minutes.')
        # Equal-cost password work for unknown accounts; never store plaintext passwords.
        expected=user['password_hash'] if user else 'scrypt$'+'00'*16+'$'+'00'*64
        if not verify_password(body.password,expected):raise HTTPException(401,'Email or password is incorrect.')
        with engine.connection() as db:
            old=request.cookies.get(COOKIE)
            if old:db.execute('DELETE FROM auth_sessions WHERE token_hash=?',(digest(old),))
            audit(db,'signed_in',uid=user['id']);return issue(db,user,response)
    @app.get('/api/auth/me')
    def me():return {'user':principal.get()}
    @app.post('/api/auth/logout')
    def logout(request:Request,response:Response):
        with engine.connection() as db:db.execute('DELETE FROM auth_sessions WHERE token_hash=?',(digest(request.cookies.get(COOKIE,'')),));audit(db,'signed_out')
        response.delete_cookie(COOKIE,path='/api');response.delete_cookie(CSRF,path='/');return {'ok':True}


def can_access_order(db,order):
    user=principal.get()
    if user is None:return True  # Internal worker: no HTTP route reaches here unauthenticated.
    if order['customer']==user['id']:return True
    return user['role']=='operator' and bool(db.execute('SELECT 1 FROM support_assignments WHERE order_id=? AND operator_id=?',(order['id'],user['id'])).fetchone())

def can_access_checkout(db,checkout):
    user=principal.get()
    if user is None or checkout['customer']==user['id']:return True
    return user['role']=='operator' and bool(db.execute('SELECT 1 FROM support_assignments WHERE order_id=? AND operator_id=?',('CHECKOUT-'+checkout['id'],user['id'])).fetchone())

def order_scope(alias='o'):
    user=principal.get() or {'id':'customer-demo','role':'customer'}
    return f"({alias}.customer=? OR (?='operator' AND EXISTS (SELECT 1 FROM support_assignments sa WHERE sa.order_id={alias}.id AND sa.operator_id=?)))",(user['id'],user['role'],user['id'])

def checkout_scope(alias='co'):
    user=principal.get() or {'id':'customer-demo','role':'customer'}
    return f"({alias}.customer=? OR (?='operator' AND EXISTS (SELECT 1 FROM support_assignments sa WHERE sa.order_id='CHECKOUT-'||{alias}.id AND sa.operator_id=?)))",(user['id'],user['role'],user['id'])
