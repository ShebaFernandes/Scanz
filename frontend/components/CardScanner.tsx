'use client';
import { useEffect, useRef, useState } from 'react';
import { ScanLine, X, LoaderCircle, ShieldCheck, SwitchCamera, Flashlight, RotateCcw } from 'lucide-react';

import { findCardBox, prepareOCRFrame, type CardBox } from '../lib/cardScan';

import {createCardReader,readCardImage} from '../lib/cardReader';

type CV = any; // OpenCV's generated runtime has a dynamic WASM API.
let cvPromise: Promise<CV> | null = null;
async function loadCV(): Promise<CV> {
  if (!cvPromise) cvPromise = new Promise((resolve, reject) => {
    const w = window as any;
    const ready = () => {
      const cv = w.cv;
      if (cv && typeof cv.then === 'function') cv.then(resolve, reject);
      else if (cv?.Mat) resolve(cv);
      else if (cv) cv.onRuntimeInitialized = () => resolve(cv);
      else reject(new Error('Computer vision could not initialize. Please use manual entry.'));
    };
    const script = document.createElement('script');
    script.src = '/opencv.js'; script.onload = ready;
    script.onerror = () => { cvPromise = null; reject(new Error('Could not load OpenCV. Please use manual entry.')); };
    document.body.appendChild(script);
  });
  return cvPromise;
}

export default function CardScanner({ onRead, onClose, testVideo }: { onRead: (last4: string) => void; onClose: () => void; testVideo?: MediaStream }) {
 const video=useRef<HTMLVideoElement>(null);const track=useRef<MediaStreamTrack|null>(null);
 const read=useRef(onRead);read.current=onRead;
 const [error,setError]=useState('');const [message,setMessage]=useState('Allow camera access to start.');
 const [stage,setStage]=useState<'loading'|'find'|'hold'|'read'|'check'>('loading');
 const [facing,setFacing]=useState<'environment'|'user'>('environment');const [attempt,setAttempt]=useState(0);
 const [torchAvailable,setTorchAvailable]=useState(false);const [torch,setTorch]=useState(false);
 const [zoom,setZoom]=useState(1);const [zoomRange,setZoomRange]=useState<{min:number;max:number;step:number}|null>(null);
 const [aspect,setAspect]=useState(16/9);const [outline,setOutline]=useState<CardBox|null>(null);
 useEffect(()=>{
  let alive=true;let timer:ReturnType<typeof setTimeout>|undefined;let media:MediaStream|null=null;
  let worker:Awaited<ReturnType<typeof import('tesseract.js')['createWorker']>>|undefined;
  let candidate='';let matches=0;let reading=false;let lastRead=0;let lastMatch=0;let cvRuntime:CV|null=null;
  // Edge detection is an optional enhancement. It must never gate OCR.
  void loadCV().then(cv=>{if(alive)cvRuntime=cv;}).catch(()=>{});
  const disposeWorker=()=>{const w=worker;worker=undefined;if(w)void w.terminate().catch(()=>{});};
  const stopCamera=()=>media?.getTracks().forEach(t=>t.stop());
  setError('');setOutline(null);setStage('loading');setTorchAvailable(false);setTorch(false);setZoomRange(null);setZoom(1);setMessage('Opening your camera…');
  const resources=createCardReader(event=>{if(alive&&event.status==='loading language traineddata')setMessage('Loading the card reader…');},()=>{if(alive)setError('The card reader could not finish. Tap Try again or enter the last four digits.');}).then(async w=>{if(!alive){await w.terminate();return null;}worker=w;return w;});
  // Attach rejection handling immediately, including when camera permission is denied first.
  void resources.catch(()=>{});
  async function start(){
   try{
    if(!testVideo&&!navigator.mediaDevices?.getUserMedia)throw new Error('Camera scanning needs localhost or HTTPS. Enter the last four digits instead.');
    media=testVideo||await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:facing},width:{ideal:1920},height:{ideal:1080},frameRate:{ideal:24,max:30}},audio:false});
    if(!alive){stopCamera();return;}
    const camera=media.getVideoTracks()[0];track.current=camera;
    const capabilities=camera.getCapabilities?.() as (MediaTrackCapabilities&{focusMode?:string[];torch?:boolean;zoom?:{min:number;max:number;step:number}})|undefined;
    setTorchAvailable(!!capabilities?.torch);
    if(capabilities?.zoom){setZoomRange(capabilities.zoom);setZoom((camera.getSettings() as MediaTrackSettings&{zoom?:number}).zoom||capabilities.zoom.min);}
    if(capabilities?.focusMode?.includes('continuous'))void camera.applyConstraints({advanced:[{focusMode:'continuous'} as MediaTrackConstraintSet]}).catch(()=>{});
    camera.onended=()=>{if(alive){setError('The camera stopped. Try again or enter the last four digits.');setStage('find');}};
    if(video.current){video.current.srcObject=media;await video.current.play();setAspect(video.current.videoWidth/video.current.videoHeight||16/9);}
    setMessage('Preparing the scanner. This can take a moment the first time.');
    const w=await resources;if(!alive||!w)return;
    setStage('find');setMessage('Show the card number to the camera. Scanning is already running.');
    async function recognize(box:CardBox|null,frameWidth:number){
     const mats:CV[]=[];const capture=document.createElement('canvas');const output=document.createElement('canvas');
     try{
      const v=video.current;if(!v||!worker||!alive)return;
      capture.width=v.videoWidth;capture.height=v.videoHeight;capture.getContext('2d')!.drawImage(v,0,0);
      prepareOCRFrame(cvRuntime,capture,box,frameWidth,Math.floor(lastRead/1000)%2===0,output);
      setStage(matches?'check':'read');setMessage(matches?'Digits found. Checking once more…':'Scanning automatically. Keep the number clear and readable.');
      const {suffix}=await readCardImage(worker,output);
      if(!alive)return;
      if(!suffix){setMessage('Bring the number a little closer, or tilt away from reflections.');return;}
      const now=performance.now();matches=suffix===candidate&&now-lastMatch<20000?matches+1:1;candidate=suffix;lastMatch=now;
      if(matches<2){setStage('check');setMessage('Digits found. Checking once more…');return;}
      alive=false;stopCamera();disposeWorker();read.current(suffix);
     }catch{if(alive){setError('Reading failed. Tap Try again or enter the last four digits.');setMessage('Your camera is ready, but the card reader needs a retry.');}}
     finally{capture.width=0;capture.height=0;output.width=0;output.height=0;mats.reverse().forEach(m=>m.delete());reading=false;}
    }
    function tick(){
     if(!alive)return;const canvas=document.createElement('canvas');const mats:CV[]=[];
     try{
      const v=video.current;if(!v||v.readyState<2||document.hidden)return;
      canvas.width=Math.min(640,v.videoWidth);canvas.height=Math.round(canvas.width*v.videoHeight/v.videoWidth);
      let box:CardBox|null=null;
      if(cvRuntime){try{canvas.getContext('2d')!.drawImage(v,0,0,canvas.width,canvas.height);const src=cvRuntime.imread(canvas);mats.push(src);box=findCardBox(cvRuntime,src);}catch{/* OCR works without a detected rectangle. */}}
      setOutline(box?{x:box.x/canvas.width,y:box.y/canvas.height,width:box.width/canvas.width,height:box.height/canvas.height}:null);
      const now=performance.now();
      if(!reading&&now-lastRead>850){reading=true;lastRead=now;void recognize(box,canvas.width);}
     }finally{canvas.width=0;canvas.height=0;mats.reverse().forEach(m=>m.delete());if(alive)timer=setTimeout(tick,250);}
    }
    tick();
   }catch(e){
    if(alive){alive=false;stopCamera();disposeWorker();setStage('find');const name=e instanceof DOMException?e.name:'';setError(name==='NotAllowedError'?'Allow camera access in your browser, then try again.':name==='NotFoundError'?'No camera was found. Enter the last four digits instead.':e instanceof Error?e.message:'The camera could not start. Try again or enter the digits.');}
   }
  }
  void start();
  return()=>{alive=false;if(timer)clearTimeout(timer);stopCamera();disposeWorker();track.current=null;};
 },[facing,attempt,testVideo]);
 async function toggleTorch(){try{await track.current?.applyConstraints({advanced:[{torch:!torch} as MediaTrackConstraintSet]});setTorch(!torch);}catch{setMessage('The light is unavailable. Try a brighter spot instead.');}}
 const active=stage==='read'||stage==='check';
 return <div className="modal-backdrop"><section className="scanner-modal automatic-scanner smooth-scanner" role="dialog" aria-modal="true" aria-labelledby="scanner-title"><button className="icon-button modal-close" onClick={onClose} aria-label="Close scanner"><X size={20}/></button><span className="eyebrow">{testVideo?'SYNTHETIC VIDEO TEST — NO CAMERA':'AUTOMATIC CARD SCAN'}</span><h2 id="scanner-title">Hold your card in view</h2><p className="muted">Use the side with the long card number. Fill most of the camera view, keep all number groups visible, and avoid glare. No capture button is needed.</p><div className={'camera-view '+(outline?'card-found':'')} style={{aspectRatio:aspect}}><video ref={video} autoPlay playsInline muted/><div className="camera-guide"><ScanLine size={25}/><span>{active?'Scanning automatically…':'Show the card number'}</span></div>{outline&&<div className="detected-card-outline" style={{left:`${outline.x*100}%`,top:`${outline.y*100}%`,width:`${outline.width*100}%`,height:`${outline.height*100}%`}}/>}{stage==='loading'&&<div className="scanner-loading"><LoaderCircle className="spin" size={25}/><span>Getting ready…</span></div>}</div><div className="scanner-stages" aria-label="Scan progress"><span className={stage!=='loading'?'complete':''}>1. Camera ready</span><span className={active?'complete':''}>2. Read number</span><span className={stage==='check'?'complete':''}>3. Double-check</span></div><p className="scan-message" role="status">{!error&&<LoaderCircle className="spin" size={16}/>} {message}</p>{error&&<p className="error" role="alert">{error}</p>}{zoomRange&&<label className="scanner-zoom">Make the number larger<input type="range" min={zoomRange.min} max={Math.min(zoomRange.max,4)} step={zoomRange.step||0.1} value={zoom} onChange={e=>{const value=Number(e.target.value);setZoom(value);void track.current?.applyConstraints({advanced:[{zoom:value} as MediaTrackConstraintSet]}).catch(()=>setMessage('Move the card closer instead.'));}}/></label>}<div className="scanner-camera-tools"><button className="text-button" onClick={()=>setFacing(facing==='environment'?'user':'environment')}><SwitchCamera size={16}/>Switch camera</button>{torchAvailable&&<button className="text-button" aria-pressed={torch} onClick={toggleTorch}><Flashlight size={16}/>{torch?'Light off':'Add light'}</button>}{error&&<button className="text-button" onClick={()=>setAttempt(n=>n+1)}><RotateCcw size={16}/>Try again</button>}</div><button className="secondary full" onClick={onClose}>Enter the last four digits instead</button><p className="privacy-caption"><ShieldCheck size={14}/>Your image stays on this device. Only the last four digits are kept for you to confirm. Never show your CVV or PIN.</p></section></div>;
}
