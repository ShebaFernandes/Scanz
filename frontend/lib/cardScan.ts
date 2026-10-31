export type CardPoint={x:number;y:number};
export type CardBox={x:number;y:number;width:number;height:number;corners?:CardPoint[]};
export function sameCard(a:CardBox|null,b:CardBox):boolean{
 if(!a)return false;
 const scale=Math.max(a.width,a.height);
 return Math.abs(a.x-b.x)<scale*.065&&Math.abs(a.y-b.y)<scale*.065&&Math.abs(a.width-b.width)<scale*.075&&Math.abs(a.height-b.height)<scale*.075;
}
function luhn(value:string):boolean{
 let sum=0;let double=false;
 for(let i=value.length-1;i>=0;i--){let n=Number(value[i]);if(double){n*=2;if(n>9)n-=9;}sum+=n;double=!double;}
 return sum%10===0;
}
/** Parse in browser memory only. Return the suffix, never the complete number. */
export function cardSuffix(text:string,confidence:number):string|null{
 if(confidence<65)return null;
 const candidates=new Set<string>();
 for(const line of text.split(/[\r\n]+/)){
  // Card numbers must occupy a row: don't concatenate dates, amounts, or other rows.
  const row=line.trim();if(!/^[\d\s-]+$/.test(row))continue;
  const number=row.replace(/[\s-]/g,'');
  if(number.length>=13&&number.length<=19&&!/^(\d)\1+$/.test(number)&&luhn(number))candidates.add(number);
 }
 if(candidates.size!==1)return null;
 return [...candidates][0].slice(-4);
}

/** Find a fully visible landscape card. Caller retains ownership of the source image. */
export function findCardBox(cv:any,src:any):CardBox|null{
 const mats:any[]=[];
 try{
  const gray=new cv.Mat();mats.push(gray);cv.cvtColor(src,gray,cv.COLOR_RGBA2GRAY);
  const blurred=new cv.Mat();mats.push(blurred);cv.GaussianBlur(gray,blurred,new cv.Size(5,5),0);
  const edges=new cv.Mat();mats.push(edges);cv.Canny(blurred,edges,45,130);
  const kernel=cv.getStructuringElement(cv.MORPH_RECT,new cv.Size(3,3));mats.push(kernel);cv.morphologyEx(edges,edges,cv.MORPH_CLOSE,kernel);
  const contours=new cv.MatVector();mats.push(contours);const hierarchy=new cv.Mat();mats.push(hierarchy);
  cv.findContours(edges,contours,hierarchy,cv.RETR_EXTERNAL,cv.CHAIN_APPROX_SIMPLE);
  let box:CardBox|null=null;let largest=0;
  for(let i=0;i<contours.size();i++){
   const contour=contours.get(i);const polygon=new cv.Mat();
   try{
    cv.approxPolyDP(contour,polygon,.025*cv.arcLength(contour,true),true);
    const rect=cv.boundingRect(polygon);const area=Math.abs(cv.contourArea(polygon));const rotated=cv.minAreaRect(polygon);const ratio=Math.max(rotated.size.width,rotated.size.height)/Math.max(1,Math.min(rotated.size.width,rotated.size.height));
    if(polygon.rows===4&&cv.isContourConvex(polygon)&&ratio>1.3&&ratio<2.05&&area>src.cols*src.rows*.10&&area>largest&&rect.x>5&&rect.y>5&&rect.x+rect.width<src.cols-5&&rect.y+rect.height<src.rows-5){box={...rect,corners:Array.from({length:4},(_,n)=>({x:polygon.data32S[n*2],y:polygon.data32S[n*2+1]}))};largest=area;}
   }finally{polygon.delete();contour.delete();}
  }
  return box;
 }finally{mats.reverse().forEach(m=>m.delete());}
}

/** Sort corners for a landscape crop; preserves a modestly tilted card's reading direction. */
export function orderedCorners(points:CardPoint[]):CardPoint[]{
 const center={x:points.reduce((n,p)=>n+p.x,0)/4,y:points.reduce((n,p)=>n+p.y,0)/4};
 let sorted=[...points].sort((a,b)=>Math.atan2(a.y-center.y,a.x-center.x)-Math.atan2(b.y-center.y,b.x-center.x));
 const first=sorted.reduce((best,p,i)=>p.x+p.y<sorted[best].x+sorted[best].y?i:best,0);
 sorted=[...sorted.slice(first),...sorted.slice(0,first)];
 return sorted;
}
export function flattenCard(cv:any,source:any,box:CardBox,scale:number):any{
 const corners=box.corners||[{x:box.x,y:box.y},{x:box.x+box.width,y:box.y},{x:box.x+box.width,y:box.y+box.height},{x:box.x,y:box.y+box.height}];
 const ordered=orderedCorners(corners);const mats:any[]=[];
 try{
  const input=cv.matFromArray(4,1,cv.CV_32FC2,ordered.flatMap(p=>[p.x*scale,p.y*scale]));mats.push(input);
  const output=cv.matFromArray(4,1,cv.CV_32FC2,[0,0,1399,0,1399,879,0,879]);mats.push(output);
  const transform=cv.getPerspectiveTransform(input,output);mats.push(transform);
  const flattened=new cv.Mat();
  try{cv.warpPerspective(source,flattened,transform,new cv.Size(1400,880),cv.INTER_LINEAR,cv.BORDER_REPLICATE);return flattened;}catch(e){flattened.delete();throw e;}
 }finally{mats.reverse().forEach(m=>m.delete());}
}

/** OCR always gets a frame, even without OpenCV, an outline, or a successful crop. */
export function prepareOCRFrame(cv:any,capture:any,box:CardBox|null,detectedWidth:number,enhance:boolean,output:any):void{
 const plain=()=>{const scale=Math.min(1,1800/capture.width);output.width=Math.round(capture.width*scale);output.height=Math.round(capture.height*scale);output.getContext('2d').drawImage(capture,0,0,output.width,output.height);};
 if(!cv||!box){plain();return;}
 const mats:any[]=[];
 try{
  const src=cv.imread(capture);mats.push(src);const flat=flattenCard(cv,src,box,capture.width/detectedWidth);mats.push(flat);
  const gray=new cv.Mat();mats.push(gray);cv.cvtColor(flat,gray,cv.COLOR_RGBA2GRAY);
  if(enhance){const contrast=new cv.Mat();mats.push(contrast);cv.normalize(gray,contrast,0,255,cv.NORM_MINMAX);cv.imshow(output,contrast);}else cv.imshow(output,gray);
 }catch{plain();}finally{mats.reverse().forEach(m=>m.delete());}
}

export type OCRCardData={text:string;confidence:number;blocks?:{paragraphs:{lines:{text:string;confidence:number}[]}[]}[]|null};
/** Ignore low-confidence logos/dates, but require a confident valid number row. */
export function suffixFromOCR(data:OCRCardData):string|null{
 // First retain the existing ambiguity fence across the complete image.
 const unique=cardSuffix(data.text,100);if(!unique)return null;
 const lines=data.blocks?.flatMap(b=>b.paragraphs.flatMap(p=>p.lines))||[];
 if(lines.some(line=>cardSuffix(line.text,line.confidence)===unique))return unique;
 return cardSuffix(data.text,data.confidence);
}
