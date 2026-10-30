const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const path=require('node:path');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
(async()=>{
 const child=spawn(process.env.PYTHON||'python',['tools/preview_dashboard.py'],{cwd:root,windowsHide:true,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
 let browser;
 try{
  const url=await new Promise((resolve,reject)=>{let out='';child.stdout.on('data',data=>{out+=data;if(out.includes('\n')){try{resolve(JSON.parse(out.split('\n')[0]).url)}catch(e){reject(e)}}});child.once('exit',code=>reject(new Error('preview server exited '+code)));child.stderr.on('data',data=>process.stderr.write(data))});
  browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.locator('#manualScanBtn').click();
  await page.locator('#manualScanToken').fill('test-only');
  const responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/scan')&&r.request().method()==='POST');
  await page.locator('#manualScanRun').click();
  const response=await responsePromise;
  assert.equal(response.status(),200);
  const payload=await response.json();
  assert.equal(payload.summary.enrichment_succeeded,3);
  assert.equal(payload.summary.signals,2);
  assert.equal(payload.summary.snapshot_published,true);
  await page.waitForFunction(()=>!document.querySelector('#manualScanDialog').open);
  assert.equal(await page.locator('#priorityCount').textContent(),'2 個');
  const snapshot=await (await page.request.get(url+'/api/state')).json();
  assert.equal(snapshot.revision,1);assert.equal(snapshot.states.UPUSDT.state,'LONG_READY');assert.equal(snapshot.states.DOWNUSDT.state,'SHORT_READY');
  assert.equal(await page.locator('#manualScanToken').inputValue(),'');
  assert.equal(errors.length,0,errors.join('\n'));
  const out=path.join(root,'artifacts/verification');fs.mkdirSync(out,{recursive:true});
  fs.writeFileSync(path.join(out,'integration-result.json'),JSON.stringify({fixtureMarkets:true,productionWrites:false,flow:'browser -> real scan handler -> classifier/lifecycle -> SQLite ledger -> file publication -> real state API -> dashboard',summary:payload.summary,pageErrors:errors},null,2));
  await page.screenshot({path:path.join(out,'integration-desktop.png'),fullPage:true});
  await page.request.get(url+'/__shutdown');
  console.log('Isolated integration passed: browser -> real API -> SQLite transaction -> snapshot -> dashboard.');
 }finally{if(browser)await browser.close();child.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
