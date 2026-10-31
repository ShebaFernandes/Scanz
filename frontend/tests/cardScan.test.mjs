import test from 'node:test';
import assert from 'node:assert/strict';
import {cardSuffix,sameCard} from '../lib/cardScan.ts';
test('full-card OCR returns only suffix of a valid synthetic card row',()=>{
 assert.equal(cardSuffix('SAMPLE NAME\n4111 1111 1111 1111\n12/30',90),'1111');
 assert.equal(cardSuffix('4111111111111111',80),'1111');
});
test('expiry, partial, low-confidence and invalid readings are never accepted',()=>{
 for(const text of ['12 30\n1234','4111 1111 1111 1112','0000000000000000','4111\n1111\n1111\n1111'])assert.equal(cardSuffix(text,90),null);
 assert.equal(cardSuffix('4111111111111111',40),null);
});
test('multiple card rows require manual confirmation rather than a guessed suffix',()=>{assert.equal(cardSuffix('4111111111111111\n5555555555554444',90),null);});
test('movement resets stability',()=>{const b={x:40,y:40,width:400,height:250};assert.equal(sameCard(null,b),false);assert.equal(sameCard(b,{...b,x:43}),true);assert.equal(sameCard(b,{...b,x:90}),false);});

test('OCR receives the camera image when no card outline or OpenCV is available',async()=>{
 const {prepareOCRFrame}=await import('../lib/cardScan.ts');
 for(const [cv,box] of [[null,null],[{},null],[null,{x:10,y:10,width:300,height:200}]]){
  let draws=0;const source={width:1920,height:1080};const output={width:0,height:0,getContext:()=>({drawImage:(input)=>{assert.equal(input,source);draws++;}})};
  prepareOCRFrame(cv,source,box,640,false,output);assert.equal(draws,1);assert.equal(output.width,1800);assert.ok(output.height>0);
 }
});
test('a failed vision crop falls back to the original image instead of blocking OCR',async()=>{
 const {prepareOCRFrame}=await import('../lib/cardScan.ts');let draws=0;
 const source={width:1280,height:720};const output={width:0,height:0,getContext:()=>({drawImage:()=>draws++})};
 prepareOCRFrame({imread:()=>{throw new Error('vision unavailable');}},source,{x:10,y:10,width:300,height:200},640,true,output);
 assert.equal(draws,1);assert.equal(output.width,1280);
});

test('a clear card-number row is accepted even when logos reduce page confidence',async()=>{
 const {suffixFromOCR}=await import('../lib/cardScan.ts');
 const data={text:'4111 1111 1111 1111\n1230\n7',confidence:63,blocks:[{paragraphs:[{lines:[{text:'4111 1111 1111 1111',confidence:95},{text:'1230',confidence:0}]}]}]};
 assert.equal(suffixFromOCR(data),'1111');
 data.blocks[0].paragraphs[0].lines[0].confidence=30;assert.equal(suffixFromOCR(data),null);
});
test('row confidence cannot bypass ambiguous multiple cards',async()=>{const {suffixFromOCR}=await import('../lib/cardScan.ts');assert.equal(suffixFromOCR({text:'4111111111111111\n5555555555554444',confidence:95,blocks:[{paragraphs:[{lines:[{text:'4111111111111111',confidence:99}]}]}]}),null);});
