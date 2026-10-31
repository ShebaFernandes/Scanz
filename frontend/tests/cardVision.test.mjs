import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {findCardBox} from '../lib/cardScan.ts';
const require=createRequire(import.meta.url);
const cv=await require('@techstark/opencv-js');
function image(){return new cv.Mat(600,960,cv.CV_8UC4,new cv.Scalar(20,20,20,255));}
test('real OpenCV finds a fully visible landscape card',()=>{
 const src=image();try{cv.rectangle(src,new cv.Point(160,100),new cv.Point(800,500),new cv.Scalar(235,235,235,255),-1);const box=findCardBox(cv,src);assert.ok(box);assert.ok(Math.abs(box.x-160)<5&&Math.abs(box.width-640)<10);}finally{src.delete();}
});
test('real OpenCV refuses an empty view or a cropped card',()=>{
 const src=image();try{assert.equal(findCardBox(cv,src),null);cv.rectangle(src,new cv.Point(0,100),new cv.Point(640,500),new cv.Scalar(235,235,235,255),-1);assert.equal(findCardBox(cv,src),null);}finally{src.delete();}
});

test('a smaller card no longer needs to fill most of the camera view',()=>{
 const src=image();try{cv.rectangle(src,new cv.Point(310,200),new cv.Point(650,414),new cv.Scalar(235,235,235,255),-1);assert.ok(findCardBox(cv,src));}finally{src.delete();}
});
test('a tilted card is detected and straightened into a readable landscape crop',async()=>{
 const {flattenCard}=await import('../lib/cardScan.ts');const src=image();let flat;
 try{
  const corners=[{x:220,y:80},{x:780,y:220},{x:690,y:560},{x:130,y:420}];
  for(let i=0;i<4;i++)cv.line(src,new cv.Point(corners[i].x,corners[i].y),new cv.Point(corners[(i+1)%4].x,corners[(i+1)%4].y),new cv.Scalar(235,235,235,255),7);
  const box=findCardBox(cv,src);assert.ok(box);assert.equal(box.corners.length,4);
  flat=flattenCard(cv,src,box,1);assert.equal(flat.cols,1400);assert.equal(flat.rows,880);
 }finally{flat?.delete();src.delete();}
});
test('square objects are not treated as cards',()=>{const src=image();try{cv.rectangle(src,new cv.Point(200,80),new cv.Point(650,530),new cv.Scalar(235,235,235,255),-1);assert.equal(findCardBox(cv,src),null);}finally{src.delete();}});
