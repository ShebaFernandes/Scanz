"""Live Sarvam smoke check against an isolated temporary mock-payment database."""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend import main

async def check():
    with tempfile.TemporaryDirectory(prefix='resolve-sarvam-') as directory:
        main.DB=Path(directory)/'live-check.sqlite3'
        main.STEPS_SECONDS=0
        main.initialize()
        scenarios=[
            ('ORD-1042','Paise kat gaye aur order cancel ho gaya. Please mera refund kar do.','RESOLVED'),
            ('ORD-1043','Just check my payment status. Do not issue any refund.','INFORMATION_PROVIDED'),
        ]
        for order_id,complaint,expected in scenarios:
            case=main.create_case(main.CreateCase(order_id=order_id,complaint=complaint,agent_mode='ai'))
            with main.connection() as db:
                db.execute("UPDATE cases SET state='AI_RUNNING',due=? WHERE id=?",(time.time()+600,case['id']))
            await main.investigate(case['id'])
            for _ in range(5):main.advance_once()
            with main.connection() as db: result=main.snapshot(db,case['id'])
            report={'order':order_id,'provider':main.ai_agent.provider_name(),'state':result['state'],'expected':expected,'tools':[t['name'] for t in result['tools']],'brief_saved':bool(result['brief']),'tokens':result['ai_tokens'],'error':result['ai_error'],'refund':result['refund']['status'] if result['refund'] else None}
            print(json.dumps(report,ensure_ascii=False),flush=True)
            if result['state']!=expected or not result['brief']:
                raise SystemExit('Live Sarvam scenario did not pass. See the sanitized result above.')

if __name__=='__main__':asyncio.run(check())
