// Run with Node; exercises the shipped layout and mode functions in real Edge.
const fs = require('node:fs');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const source = fs.readFileSync('contract_kb/interfaces/web/static/js/review_workspace.js', 'utf8');
const css = fs.readFileSync('contract_kb/interfaces/web/static/css/app.css', 'utf8');
const functions = ['renderContractColumns', 'syncClauseHeights', 'setViewMode'].map(name => {
  const start = source.indexOf(`function ${name}(`);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}).join('\n');
const html = `<style>${css}</style>
<button id="btnModeSide"></button><button id="btnModeReviewed"></button><button id="btnModeSingle"></button><button id="btnModeDiff"></button>
<div class="review-workspace"><div class="contract-column" id="colStandardPane"><div class="contract-scroll-container"><div class="contract-paper-canvas" id="paperStandard"></div></div></div>
<div class="contract-column" id="colReviewedPane"><div class="contract-scroll-container"><div class="contract-paper-canvas" id="paperReviewed"></div></div></div><aside id="annotationsSidebar"></aside></div>
<script>let currentViewMode='side';
const currentData={aligned_blocks:[{block_id:'a',status:'MODIFIED',standard_text:'合同条款测试'.repeat(60),reviewed_text:'送审条款'.repeat(30)}, {block_id:'b',status:'MATCH',standard_text:'相同',reviewed_text:'相同'}]};
const detectBlockCategory=()=>''; const escapeHtml=x=>x; const formatContractHtml=x=>x;
${functions}
window.run=async()=>{
const pause=()=>new Promise(r=>setTimeout(r,350));
setViewMode('side'); await pause();
const baseline=document.getElementById('std-a').getBoundingClientRect().height;
const results=[];
for(const mode of ['reviewed','single','side','diff','reviewed','side']) {
 setViewMode(mode); await pause();
 const stdW = document.getElementById('colStandardPane').getBoundingClientRect().width;
 const revW = document.getElementById('colReviewedPane').getBoundingClientRect().width;
 const sideW = document.getElementById('annotationsSidebar').getBoundingClientRect().width;
 const h = document.getElementById('std-a') ? document.getElementById('std-a').getBoundingClientRect().height : 0;
 results.push({mode, height: h, baseline, stdW, revW, sideW});
}
return results;
};</script>`;
(async()=>{
 const child=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', ['--headless','--disable-gpu','--no-first-run','--remote-debugging-port=9341',`--user-data-dir=${process.cwd()}/tmp/view-mode-edge`,'about:blank'], {windowsHide:true,stdio:'ignore'});
 let ws;
 try {
  let pages;
  for(let i=0;i<80;i++){try{pages=await(await fetch('http://127.0.0.1:9341/json')).json();break;}catch{await new Promise(r=>setTimeout(r,100));}}
  ws=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl); await new Promise(r=>ws.onopen=r);
  let id=0;const pending=new Map();ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){pending.get(m.id)(m);pending.delete(m.id);}};
  const send=(method,params={})=>new Promise(r=>{pending.set(++id,r);ws.send(JSON.stringify({id,method,params}));});
  await send('Emulation.setDeviceMetricsOverride',{width:1946,height:1049,deviceScaleFactor:1,mobile:false});
  await send('Page.enable');
  await send('Page.setDocumentContent',{frameId:(await send('Page.getFrameTree')).result.frameTree.frame.id,html});
  const result=await send('Runtime.evaluate',{expression:'run()',awaitPromise:true,returnByValue:true});
  assert.ok(!result.result.exceptionDetails, JSON.stringify(result));
  const rows=result.result.result.value; console.log(rows);
  for(const row of rows) {
    if (row.mode === 'side') {
      assert.ok(Math.abs(row.height-row.baseline)<2, `inflated height in ${row.mode}: ${row.height} vs ${row.baseline}`);
      assert.ok(row.stdW > 500, `stdW in side mode: ${row.stdW}`);
      assert.ok(row.revW > 500, `revW in side mode: ${row.revW}`);
      assert.ok(row.sideW >= 320, `sideW in side mode: ${row.sideW}`);
    } else if (row.mode === 'reviewed' || row.mode === 'single') {
      assert.equal(row.stdW, 0, `stdW in ${row.mode} mode: ${row.stdW}`);
      assert.ok(row.revW > 1000, `revW in ${row.mode} mode: ${row.revW}`);
      assert.ok(row.sideW >= 320, `sideW in ${row.mode} mode: ${row.sideW}`);
    }
  }
 } finally {ws?.close();child.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
