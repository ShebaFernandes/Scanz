"""Disabled-by-default Paytm MCP evidence adapter. No write/refund tools or AI sampling."""
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Literal
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from jsonschema import Draft202012Validator
try:
    from . import auth, ai_agent
except ImportError:
    import auth, ai_agent

READ_TOOLS={'fetch_order_status','fetch_refund_status'}

def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS paytm_bindings(order_id TEXT PRIMARY KEY,merchant_id TEXT NOT NULL,provider_order_id TEXT NOT NULL,provider_refund_id TEXT);
    CREATE TABLE IF NOT EXISTS paytm_evidence(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id TEXT NOT NULL,kind TEXT NOT NULL,report TEXT NOT NULL,created REAL NOT NULL);
    ''')

def configuration():
    enabled=ai_agent.setting('PAYTM_MCP_ENABLED','false').lower()=='true'
    url=ai_agent.setting('PAYTM_MCP_URL','https://mcp.paytmpayments.com/mcp')
    parts=urlsplit(url)
    if parts.scheme!='https' or parts.hostname!='mcp.paytmpayments.com' or parts.port not in {None,443} or parts.path not in {'/mcp','/mcp/'} or parts.query or parts.fragment or parts.username or parts.password:raise HTTPException(503,'Paytm MCP URL must be the approved HTTPS Paytm endpoint.')
    token=ai_agent.setting('PAYTM_MCP_ACCESS_TOKEN');mid=ai_agent.setting('PAYTM_MERCHANT_ID')
    return {'enabled':enabled,'url':url,'token':token,'mid':mid}

@asynccontextmanager
async def connect():
    config=configuration()
    if not config['enabled']:raise HTTPException(503,'Paytm MCP is disabled. Refunds remain mocked.')
    if not config['token'] or not config['mid']:raise HTTPException(503,'Configure authorized Paytm access and merchant ID on the server first.')
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    def client_factory(headers=None,timeout=None,auth=None):
        return httpx.AsyncClient(headers=headers,auth=auth,timeout=httpx.Timeout(15,connect=5),follow_redirects=False,trust_env=False)
    async with asyncio.timeout(25):
        async with streamablehttp_client(config['url'],headers={'Authorization':'Bearer '+config['token']},timeout=15,sse_read_timeout=20,httpx_client_factory=client_factory) as (read,write,_):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=15)) as session:
                await session.initialize()
                yield session

def safe_schema(schema):
    if len(json.dumps(schema))>32768:return False
    def valid(value):
        if isinstance(value,dict):return all((key not in {'$ref','$dynamicRef'} or isinstance(item,str) and item.startswith('#/')) and valid(item) for key,item in value.items())
        if isinstance(value,list):return all(valid(item) for item in value)
        return True
    return valid(schema)

async def tools_for(session):
    result=await session.list_tools()
    return {tool.name:tool for tool in result.tools if tool.name in READ_TOOLS and safe_schema(tool.inputSchema) and not (tool.annotations and tool.annotations.readOnlyHint is False)}

def arguments_for(tool,binding,kind):
    aliases={'orderId':binding['provider_order_id'],'order_id':binding['provider_order_id'],'ORDERID':binding['provider_order_id'],'mid':binding['merchant_id'],'merchantId':binding['merchant_id'],'merchant_id':binding['merchant_id']}
    if kind=='refund':
        if not binding['provider_refund_id']:raise HTTPException(409,'No trusted provider refund reference is bound. Mock refund references cannot be sent to Paytm.')
        aliases.update({'refId':binding['provider_refund_id'],'refundId':binding['provider_refund_id'],'refund_id':binding['provider_refund_id']})
    props=tool.inputSchema.get('properties',{})
    args={key:value for key,value in aliases.items() if key in props}
    # Never issue an unscoped list/query because a provider schema changed.
    if not any(key in args for key in ('orderId','order_id','ORDERID','refId','refundId','refund_id')):raise HTTPException(409,'This tool schema is not supported for a single-order lookup.')
    if list(Draft202012Validator(tool.inputSchema).iter_errors(args)):raise HTTPException(409,'Provider tool schema needs review. No call was made.')
    return args

def minimal_report(result,binding):
    if result.isError:raise HTTPException(502,'Paytm returned a tool error. No local payment state was changed.')
    structured=getattr(result,'structuredContent',None)
    if structured is None:
        texts=[item.text for item in result.content if getattr(item,'type',None)=='text']
        if sum(map(len,texts))>65536:raise HTTPException(502,'Provider response is too large.')
        try:structured=json.loads('\n'.join(texts))
        except (ValueError,TypeError):raise HTTPException(502,'Provider response is not structured payment evidence.')
    if len(json.dumps(structured))>65536:raise HTTPException(502,'Provider response is too large.')
    statuses=[];order_refs=[]
    allowed={'SUCCESS','TXN_SUCCESS','FAILURE','TXN_FAILURE','PENDING','PROCESSING','ACCEPTED','CREDITED','REFUND_SUCCESS','REFUND_FAILURE'}
    def walk(value,depth=0):
        if depth>8:return
        if isinstance(value,dict):
            for key,item in value.items():
                if key.lower().replace('_','') in {'orderid'} and isinstance(item,str):order_refs.append(item)
                if key.lower().replace('_','') in {'status','paymentstatus','refundstatus','resultstatus'} and isinstance(item,str) and item.upper() in allowed:statuses.append(item.upper())
                if isinstance(item,(dict,list)):walk(item,depth+1)
        elif isinstance(value,list):
            for item in value:walk(item,depth+1)
    walk(structured)
    if not order_refs or any(ref!=binding['provider_order_id'] for ref in order_refs):raise HTTPException(502,'Provider evidence did not match the bound order. No local state was changed.')
    return {'provider_order_id':binding['provider_order_id'],'reported_statuses':sorted(set(statuses)),'source':'paytm_mcp','read_only':True,'applied_to_workflow':False,'response_digest':hashlib.sha256(json.dumps(structured,sort_keys=True).encode()).hexdigest()}

def install(app,engine):
    @app.get('/api/integrations/paytm/status')
    def status():
        config=configuration()
        return {'enabled':config['enabled'],'configured':bool(config['token'] and config['mid']),'endpoint':config['url'],'read_only':True,'refund_execution':'mock','sarvam_paused':ai_agent.setting('AI_LIVE_ENABLED','false').lower() not in {'true','1','yes'},'available_tool_names':sorted(READ_TOOLS),'connected':False,'note':'No provider request is made by this status check. Supply Paytm-issued access server-side; discovery is explicit.'}
    @app.post('/api/integrations/paytm/discover')
    async def discover():
        try:
            async with connect() as session:tools=await tools_for(session)
            with engine.connection() as db:auth.audit(db,'paytm_tools_discovered')
            return {'tools':[{'name':name,'input_schema':tool.inputSchema} for name,tool in tools.items()],'read_only':True,'refund_execution':'mock'}
        except HTTPException:raise
        except Exception:raise HTTPException(502,'Could not connect to Paytm MCP. Check authorized access and server configuration. No credentials are returned in errors.')
    @app.post('/api/integrations/paytm/orders/{order_id}/evidence')
    async def evidence(order_id:str,kind:Literal['payment','refund']='payment'):
        with engine.connection() as db:
            engine.order_for(db,order_id)
            raw=db.execute('SELECT * FROM paytm_bindings WHERE order_id=?',(order_id,)).fetchone()
        if not raw:raise HTTPException(409,'This local order has no trusted Paytm binding. A card scan cannot create one.')
        binding=dict(raw);config=configuration()
        if binding['merchant_id']!=config['mid']:raise HTTPException(409,'The bound merchant does not match this server connection.')
        try:
            async with connect() as session:
                tools=await tools_for(session);name='fetch_order_status' if kind=='payment' else 'fetch_refund_status'
                if name not in tools:raise HTTPException(409,'The permitted provider tool is unavailable. Keep using the mock workflow.')
                args=arguments_for(tools[name],binding,kind);result=await session.call_tool(name,args);report=minimal_report(result,binding)
            with engine.connection() as db:
                # Ownership is rechecked before persisting evidence. Evidence never changes mock financial records.
                engine.order_for(db,order_id)
                db.execute('INSERT INTO paytm_evidence(order_id,kind,report,created) VALUES (?,?,?,?)',(order_id,kind,json.dumps(report),__import__('time').time()))
                auth.audit(db,'paytm_evidence_read',order_id)
            return report
        except HTTPException:raise
        except Exception:raise HTTPException(502,'Paytm evidence lookup failed. Mock records are unchanged.')
