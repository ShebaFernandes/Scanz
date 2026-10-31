const fs=require('fs');const path=require('path');
const dest=path.join(__dirname,'public','ocr');fs.mkdirSync(dest,{recursive:true});
fs.copyFileSync(require.resolve('tesseract.js/dist/worker.min.js'),path.join(dest,'worker.min.js'));
fs.copyFileSync(require.resolve('tesseract.js/dist/worker.min.js.LICENSE.txt'),path.join(dest,'worker.LICENSE.txt'));
const core=path.dirname(require.resolve('tesseract.js-core/package.json'));
for(const file of fs.readdirSync(core))if(file.endsWith('.wasm.js')||file.endsWith('.wasm')||file==='LICENSE')fs.copyFileSync(path.join(core,file),path.join(dest,file));
