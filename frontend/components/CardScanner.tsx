'use client';
import { useEffect, useRef, useState } from 'react';
import { Camera, ScanLine, X, LoaderCircle } from 'lucide-react';

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

export default function CardScanner({ onRead, onClose }: { onRead: (last4: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [message, setMessage] = useState('Keep only the last four digits visible. Cover the rest of your card.');
  useEffect(() => {
    let alive = true;
    navigator.mediaDevices?.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1280 } }, audio: false })
      .then(s => { if (!alive) { s.getTracks().forEach(t => t.stop()); return; } stream.current = s; if (video.current) video.current.srcObject = s; })
      .catch(() => setError('Camera unavailable. Allow camera access or use manual entry.'));
    if (!navigator.mediaDevices) setError('Camera requires localhost or HTTPS. Use manual entry.');
    return () => { alive = false; stream.current?.getTracks().forEach(t => t.stop()); };
  }, []);
  async function scan() {
    if (!video.current || !ready) return;
    setBusy(true); setError(''); setMessage('Improving the frame with OpenCV…');
    let worker: Awaited<ReturnType<typeof import('tesseract.js')['createWorker']>> | undefined;
    const mats: CV[] = [];
    try {
      const cv = await loadCV();
      const v = video.current;
      const canvas = document.createElement('canvas');
      canvas.width = v.videoWidth; canvas.height = v.videoHeight;
      canvas.getContext('2d')!.drawImage(v, 0, 0);
      const src = cv.imread(canvas); mats.push(src);
      const gray = new cv.Mat(); mats.push(gray);
      cv.cvtColor(src, gray, cv.COLOR_RGBA2GRAY);
      const blurred = new cv.Mat(); mats.push(blurred);
      cv.GaussianBlur(gray, blurred, new cv.Size(5,5),0);
      const edges = new cv.Mat(); mats.push(edges);
      cv.Canny(blurred, edges,50,150);
      const contours = new cv.MatVector(); mats.push(contours);
      const hierarchy = new cv.Mat(); mats.push(hierarchy);
      cv.findContours(edges,contours,hierarchy,cv.RETR_EXTERNAL,cv.CHAIN_APPROX_SIMPLE);
      let best: {x:number;y:number;width:number;height:number} | null = null;
      let bestArea = 0;
      for(let i=0;i<contours.size();i++) {
        const contour = contours.get(i);
        const rect = cv.boundingRect(contour);
        const area = rect.width*rect.height;
        if(rect.width/rect.height>1.3 && rect.width/rect.height<2.1 && area>src.cols*src.rows*.12 && area>bestArea) { best=rect; bestArea=area; }
        contour.delete();
      }
      const rect = best || { x: Math.round(src.cols*.08), y:Math.round(src.rows*.12), width:Math.round(src.cols*.84),height:Math.round(src.rows*.76) };
      const crop = gray.roi(new cv.Rect(rect.x,rect.y,rect.width,rect.height)); mats.push(crop);
      const enlarged = new cv.Mat(); mats.push(enlarged);
      cv.resize(crop,enlarged,new cv.Size(crop.cols*2,crop.rows*2),0,0,cv.INTER_CUBIC);
      const output = document.createElement('canvas'); cv.imshow(output,enlarged);
      canvas.width=0;canvas.height=0;
      setMessage('Reading digits locally. The first scan downloads the OCR language model…');
      const { createWorker } = await import('tesseract.js');
      worker = await createWorker('eng');
      await worker.setParameters({ tessedit_char_whitelist: '0123456789 ' });
      const result = await worker.recognize(output);
      output.width=0;output.height=0;
      const groups = result.data.text.match(/\b\d{4}\b/g) || [];
      if(groups.length!==1) throw new Error('Show only the last four digits, keep the card level, and try again. You can also enter the digits manually.');
      onRead(groups[0]);
    } catch (e) { setError(e instanceof Error ? e.message : 'Scan failed. Please use manual entry.'); }
    finally { mats.reverse().forEach(m=>m.delete()); if(worker) await worker.terminate(); setBusy(false); }
  }
  return <div className="modal-backdrop"><section className="scanner-modal" role="dialog" aria-modal="true" aria-labelledby="scanner-title"><button className="icon-button modal-close" onClick={onClose} aria-label="Close scanner"><X size={20}/></button><span className="eyebrow">LOCAL CARD SCANNER</span><h2 id="scanner-title">Bring your card into view.</h2><p className="muted">Cover all digits except the last four. Never show your CVV or PIN.</p><div className="camera-view"><video ref={video} autoPlay playsInline muted onLoadedData={()=>setReady(true)}/><div className="camera-guide"><ScanLine size={36}/></div></div><p className="scan-message" role="status">{message}</p>{error&&<p className="error" role="alert">{error}</p>}<button className="primary full" onClick={scan} disabled={busy||!ready}>{busy?<LoaderCircle className="spin" size={18}/>:<Camera size={18}/>} {busy?'Scanning locally…':'Capture & read digits'}</button><button className="text-button full" onClick={onClose}>Use manual entry instead</button><p className="privacy-caption">Frames stay in this browser. Only confirmed last four digits are sent.</p></section></div>;
}
