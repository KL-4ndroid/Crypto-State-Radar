// Run with NODE_PATH pointing to an installed Playwright package directory.
const {chromium} = require('playwright');
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const rendered=spawnSync(process.env.PYTHON || 'python',['-c','from api.dashboard import _render_dashboard; import sys; sys.stdout.buffer.write(_render_dashboard())'],{cwd:root,encoding:'utf8',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},maxBuffer:2000000});
if(rendered.status!==0)throw new Error(rendered.stderr);
const now=new Date();
const ago=min=>new Date(now.getTime()-min*60000).toISOString();
const state=(symbol,extra={})=>({symbol,state:'LONG_READY',direction:'LONG',updated_at:ago(1),expires_at:ago(-240),evaluation_status:'fresh',ready_price:100,last_price:100,pump_score:70,dump_score:10,...extra});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let scanned=false,stateCalls=0,scanCalls=0;
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.hostname!=='radar.local')return route.abort();
  if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:rendered.stdout});
  let body={reachable:true,symbols:5,schema_version:1};
  if(url.pathname==='/api/scan'){
   assert.equal(request.method(),'POST');assert.equal(await request.headerValue('authorization'),'Bearer test-only');
   scanned=true;scanCalls++;
   body={summary:{mode:'live',snapshot_published:true,enrichment_succeeded:1,pending:0}};
  }
  if(url.pathname==='/api/state'){
   stateCalls++;
   body={model_version:'V2.4.0-alpha',states:{A:state('A',{last_price:scanned?999:100}),B:state('B',{expires_at:ago(1)}),C:state('C',{evaluation_status:'pending'}),D:state('D',{updated_at:ago(120)}),E:state('E',{state:'WATCH'})},last_scan:{processed_at:ago(1),enrichment_succeeded:2,pending:3,enrichment_errors:1}};
   // The second refresh is in flight when manual scan completes.
   if(stateCalls===2)await new Promise(r=>setTimeout(r,250));
  }
  return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
 });
 try{
  await page.goto('http://radar.local/');
  await page.waitForFunction(()=>document.querySelector('#priorityCount').textContent==='1 個');
  assert.equal(await page.locator('#signalValue').textContent(),'1');
  assert.match(await page.locator('#coverageStatus').textContent(),/待處理 3/);
  assert.match(await page.locator('#rows').textContent(),/確認期限已過/);
  assert.match(await page.locator('#rows').textContent(),/待重新確認/);
  await page.evaluate(()=>{window.refreshRadar()});
  await page.locator('#manualScanBtn').click();
  await page.locator('#manualScanToken').fill('test-only');
  await page.locator('#manualScanRun').click();
  await page.waitForFunction(()=>!document.querySelector('#manualScanDialog').open);
  assert.equal(scanCalls,1);assert.ok(stateCalls>=3);
  assert.equal(await page.evaluate(()=>app.items.find(x=>x.symbol==='A').last_price),999);
  assert.equal(await page.locator('#manualScanToken').inputValue(),'');
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  assert.equal(errors.length,0,errors.join('\n'));
  const dir=path.join(root,'artifacts','verification');fs.mkdirSync(dir,{recursive:true});
  await page.screenshot({path:path.join(dir,'dashboard-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('.mobile-card[data-symbol="A"]').click();
  await page.waitForFunction(()=>document.querySelector('#detailDialog').open);
  assert.match(await page.locator('#detailGrid').textContent(),/最近成功評估/);
  await page.locator('#closeDetail').click();
  await page.screenshot({path:path.join(dir,'dashboard-mobile.png'),fullPage:true});
  fs.writeFileSync(path.join(dir,'browser-result.json'),JSON.stringify({desktop:'1440x900',mobile:'390x844',stateCalls,scanCalls,pageErrors:errors,manualRefreshRace:'passed',expiredReady:'excluded',tokenPersistence:'none'},null,2));
  console.log('Browser acceptance passed: desktop/mobile, freshness, manual-refresh race, token clearing.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
