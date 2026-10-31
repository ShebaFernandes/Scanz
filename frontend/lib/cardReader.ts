import type { LoggerMessage } from 'tesseract.js';
/** Shared by the camera and the on-device diagnostic. All assets are served by this app. */
export async function createCardReader(logger?:(event:LoggerMessage)=>void,errorHandler?:(error:unknown)=>void){
 const {createWorker}=await import('tesseract.js');
 const worker=await createWorker('eng',1,{workerPath:'/ocr/worker.min.js',corePath:'/ocr',langPath:'/ocr',gzip:false,logger,errorHandler});
 try{await worker.setParameters({tessedit_char_whitelist:'0123456789 -',tessedit_pageseg_mode:'11' as any});return worker;}catch(e){await worker.terminate();throw e;}
}

/** Normalize dark/light cards before OCR. Both camera and diagnostic use this path. */
export async function readCardImage(worker:Awaited<ReturnType<typeof createCardReader>>,input:HTMLCanvasElement|HTMLImageElement){
 const {suffixFromOCR}=await import('./cardScan');
 const width=input instanceof HTMLImageElement?input.naturalWidth:input.width;
 const height=input instanceof HTMLImageElement?input.naturalHeight:input.height;
 const canvas=document.createElement('canvas');const scale=Math.min(1,1200/width);canvas.width=Math.round(width*scale);canvas.height=Math.round(height*scale);
 const context=canvas.getContext('2d',{willReadFrequently:true})!;context.drawImage(input,0,0,canvas.width,canvas.height);
 try{
  const pixels=context.getImageData(0,0,canvas.width,canvas.height);const values=pixels.data;let total=0,count=0;
  for(let i=0;i<values.length;i+=64){total+=.299*values[i]+.587*values[i+1]+.114*values[i+2];count++;}
  const dark=total/Math.max(1,count)<125;
  for(let i=0;i<values.length;i+=4){let v=.299*values[i]+.587*values[i+1]+.114*values[i+2];if(dark)v=255-v;v=Math.max(0,Math.min(255,(v-128)*1.3+128));values[i]=values[i+1]=values[i+2]=v;}
  context.putImageData(pixels,0,0);
  let result={suffix:null as string|null,pageConfidence:0,rowConfidence:0};
  for(const mode of ['11','6']){
   await worker.setParameters({tessedit_pageseg_mode:mode as any});
   const {data}=await worker.recognize(canvas,{}, {text:true,blocks:true});
   try{result={suffix:suffixFromOCR(data),pageConfidence:data.confidence,rowConfidence:Math.max(0,...(data.blocks||[]).flatMap(b=>b.paragraphs.flatMap(p=>p.lines.filter(l=>l.text.replace(/\D/g,'').length>=13).map(l=>l.confidence))))};if(result.suffix)break;}finally{data.text='';data.blocks=null;}
  }
  return result;
 }finally{canvas.width=0;canvas.height=0;}
}
