'use client';
import {useRef,useState} from 'react';
import {createCardReader,readCardImage} from '../lib/cardReader';
export default function CardPhoto({onRead}:{onRead:(value:string)=>void}){
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');const input=useRef<HTMLInputElement>(null);
 async function recognize(file:File){
  setError('');if(!file.type.startsWith('image/')||file.size>12*1024*1024){setError('Choose a card photo smaller than 12 MB.');return;}
  setBusy(true);let worker:Awaited<ReturnType<typeof createCardReader>>|undefined;let image:ImageBitmap|undefined;const canvas=document.createElement('canvas');
  try{image=await createImageBitmap(file);const scale=Math.min(1,1800/image.width,1800/image.height);canvas.width=Math.round(image.width*scale);canvas.height=Math.round(image.height*scale);canvas.getContext('2d')!.drawImage(image,0,0,canvas.width,canvas.height);worker=await createCardReader();const result=await readCardImage(worker,canvas);if(!result.suffix)throw new Error('We could not read the complete number. Try a sharper photo or enter the last four digits.');onRead(result.suffix);}catch(e){setError(e instanceof Error?e.message:'Photo could not be read. Enter the last four digits instead.');}finally{image?.close();canvas.width=0;canvas.height=0;await worker?.terminate();setBusy(false);if(input.current)input.current.value='';}
 }
 return <div className="card-photo"><input ref={input} type="file" accept="image/*" aria-label="Choose a card photo" hidden onChange={e=>{const file=e.target.files?.[0];if(file)void recognize(file);}}/><button className="text-button" disabled={busy} onClick={()=>input.current?.click()}>{busy?'Reading photo on your device…':'Camera struggling? Choose a card photo'}</button>{error&&<p role="alert" className="error">{error}</p>}<small>Use the numbered side only. Never include your CVV. Photos are not uploaded.</small></div>;
}
