'use client';
import {useEffect,useState} from 'react';
export type Watch={started:number;deadline:number;last_checked:number|null;checks:number;interval_seconds:number;next_check:number|null;active:boolean;source:string};
const time=(n:number)=>new Date(n*1000).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
export default function PaymentWatch({watch}:{watch:Watch}){
 const [now,setNow]=useState(Date.now()/1000);useEffect(()=>{const t=setInterval(()=>setNow(Date.now()/1000),1000);return()=>clearInterval(t);},[]);
 const remaining=watch.next_check?Math.max(0,Math.ceil(watch.next_check-now)):0;
 return <section className="payment-watch" aria-label="Automatic payment checks"><span className="mini-pill">Mock Paytm · Automatic follow-up</span><h3>{watch.active?'We’re checking your payment for you':'Payment check history'}</h3><p>{watch.active?'You don’t need to call support. Checks continue on the server even if you close this page.':'Your checks and evidence have been saved.'}</p><dl><div><dt>Checks completed</dt><dd>{watch.checks}</dd></div><div><dt>Last check</dt><dd>{watch.last_checked?time(watch.last_checked):'Starting shortly'}</dd></div><div><dt>Next check</dt><dd aria-live="off">{watch.active&&watch.next_check?`${time(watch.next_check)} · ${remaining?`in ${remaining}s`:'checking shortly'}`:'No further check scheduled'}</dd></div><div><dt>Escalation deadline</dt><dd>{time(watch.deadline)}</dd></div></dl><small>{watch.active?'If confirmation is still missing at the deadline, we’ll save the evidence for specialist review. No refund is submitted while payment is pending.':'This deadline applied to pending-payment confirmation. A refund or review may now have its own next steps.'} All payment results are simulated.</small></section>;
}
