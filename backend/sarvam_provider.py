"""Sarvam Chat Completions v2 adapter. No payment credentials are involved."""
import json
import httpx

URL = 'https://api.sarvam.ai/v2/chat/completions'


def chat_tools(definitions):
    return [{'type':'function','function':{
        'name':t['name'],'description':t['description'],'parameters':t['parameters'],
    }} for t in definitions]


async def run(context, execute_tool, log_usage, *, key, model, system, tools,
              unavailable, transport=None):
    messages=[{'role':'system','content':system},
              {'role':'user','content':json.dumps(context,ensure_ascii=False)}]
    async with httpx.AsyncClient(timeout=35,transport=transport) as client:
        for _ in range(8):
            try:
                response=await client.post(URL,headers={'api-subscription-key':key},json={
                    'model':model,'messages':messages,'tools':chat_tools(tools),
                    'tool_choice':'required','parallel_tool_calls':False,
                    'max_tokens':600,'temperature':0.1,
                })
                response.raise_for_status()
                data=response.json()
                message=data['choices'][0]['message']
                calls=message.get('tool_calls') or []
                if not isinstance(calls,list): raise ValueError()
            except httpx.HTTPStatusError as exc:
                raise unavailable(f'Sarvam request failed (HTTP {exc.response.status_code}). Check your Sarvam key, model access, credits, and rate limits, then retry.') from None
            except (httpx.HTTPError,ValueError,KeyError,IndexError,TypeError):
                raise unavailable('Sarvam could not be reached or returned an invalid response. Your case is saved; retry or request human review.') from None
            await log_usage(data.get('usage',{}))
            if len(calls)!=1:
                raise unavailable('Sarvam did not return a single valid tool call. Retry the investigation or request human review.')
            call=calls[0]
            function=call.get('function',{})
            if not isinstance(call.get('id'),str) or not isinstance(function,dict):
                raise unavailable('Sarvam returned an invalid tool call. No action was executed.')
            # Replay only the public assistant message and tool calls. Never expose
            # or persist provider reasoning_content in the UI or audit records.
            messages.append({'role':'assistant','content':message.get('content'),'tool_calls':calls})
            try:
                args=json.loads(function.get('arguments','{}'))
                if not isinstance(args,dict): raise ValueError()
                result=await execute_tool(function.get('name',''),args)
            except (ValueError,TypeError):
                result={'error':'Invalid tool arguments. Follow the declared schema.'}
            messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result,ensure_ascii=False)})
            if function.get('name')=='finish_case_brief' and result.get('saved'):
                return
    raise unavailable('Sarvam reached the investigation tool-call limit. Retry or request human review.')
