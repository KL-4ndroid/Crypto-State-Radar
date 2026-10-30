/* Public read view only. Memory is a cache; published snapshots remain authoritative. */
let loadTask=null, currentSnapshot=null, currentMeta=null, metaETag=null;
let refreshTimer=null, electionTimer=null, failureCount=0, retryAfter=0, releaseLeader=null;
let syncTimer=null, syncDeadline=0, syncExpected=null;
let lastAcceptedCheck=0;
let renderedFreshness='';
let nextAgeCheck=0;
const freshnessKey=()=>app.items.map(x=>{const f=freshness(x);return`${f.usable}:${f.expired}:${f.stale}`}).join('|');
const dashboardChannel=typeof BroadcastChannel==='function'?new BroadcastChannel('radar-dashboard-v1'):null;
const coordinated=!!(dashboardChannel&&navigator.locks?.request);
const awake=()=>document.visibilityState!=='hidden'&&navigator.onLine!==false;
const jitter=ms=>Math.round(ms*(0.9+Math.random()*0.2));
function updateNotice(text){$('#updateNotice').textContent=text}
function showMeta(meta,checkedAt=null){
  currentMeta=meta;
  const checked=Date.parse(meta.health_checked_at||'');
  const stale=!Number.isFinite(checked)||Date.now()-checked>75*60000||checked>Date.now()+60000;
  const market=stale?'unknown':meta.services?.market;
  $('#binanceDot').className=`status-dot ${market==='ok'?'ok':market==='partial'?'bad':''}`;
  $('#binanceValue').textContent=market==='ok'?'採集正常':market==='partial'?'資料不齊':'尚待確認';
  $('#binanceFoot').textContent=`採集檢查 ${date(meta.health_checked_at)} · 非即時探測`;
  $('#stateDot').className=`status-dot ${stale?'':'ok'}`;
  $('#stateFoot').textContent=stale?'發布資訊較舊 · 待確認':`最近公開發布 ${date(meta.published_at)}`;
  if(checkedAt!==null)$('#lastChecked').textContent=`最近檢查 ${date(new Date(checkedAt).toISOString())}`;
  $('#marketDataTime').textContent=`市場資料時間 ${date(meta.last_successful_scan_at)}`;
}
function applySnapshot(snapshot){
  const detail=$('#detailDialog'),opened=detail.open,selected=detail.dataset.symbol;
  currentSnapshot=snapshot;
  app.items=Object.entries(snapshot.states||{}).map(([symbol,value])=>({symbol,...value}));
  const active=app.items.filter(x=>freshness(x).usable&&(x.state?.includes('READY')||x.state==='EARLY_WARNING'||x.state?.includes('PRESSURE')||x.state==='TRIGGERED')).length;
  $('#signalValue').textContent=active;$('#signalFoot').textContent=active?`${active} 個標的值得先查看`:'目前沒有高優先訊號';
  $('#trackedValue').textContent=app.items.length;$('#heroTracked').textContent=app.items.length;$('#heroActive').textContent=active;
  $('#coverageStatus').textContent=coverageText(snapshot);$('#modelVersion').textContent=`模型 ${snapshot.model_version||'舊版快照'}`;
  const latest=app.items.reduce((best,x)=>!x.updated_at?best:(!best||new Date(x.updated_at)>new Date(best)?x.updated_at:best),null);
  $('#heroUpdated').textContent=clock(latest);$('#lastUpdated').textContent=latest?`最近標的評估 ${date(latest)}`:'尚未產生市場狀態';
  const scroll=window.scrollY,focus=document.activeElement;
  const rowFocus=focus?.closest('.priority-card[data-symbol],#rows tr[data-symbol],.mobile-card[data-symbol]')?.dataset.symbol;
  render();
  renderedFreshness=freshnessKey();
  if(opened&&selected){if(app.items.some(x=>x.symbol===selected))openDetail(selected);else{$('#detailSubtitle').textContent='此標的已不在目前版本 · 保留先前詳情'}}
  if(rowFocus)document.querySelector(`[data-symbol="${CSS.escape(rowFocus)}"]`)?.focus({preventScroll:true});
  window.scrollTo({top:scroll,behavior:'instant'});
}
async function publicJSON(url,etag=null){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch(url,{cache:'default',signal:controller.signal,headers:etag?{'If-None-Match':etag}:{}});
    if(response.status===304)return{unchanged:true,etag};
    if(!response.ok){
      const raw=response.headers.get('Retry-After');
      retryAfter=raw?Math.max(0,/^\d+$/.test(raw)?Number(raw)*1000:Date.parse(raw)-Date.now()):0;
      throw new Error(`HTTP ${response.status}`);
    }
    return{value:await response.json(),etag:response.headers.get('ETag')};
  }finally{clearTimeout(timeout)}
}
async function loadOnce({afterScan=false}={}){
  app.loading=true;$('#refreshBtn').disabled=true;
  try{
    let changed=false;
    for(let attempt=0;attempt<2;attempt++){
      const result=await publicJSON('/api/dashboard-meta'+(afterScan?'?sync=1':''),afterScan?null:metaETag);
      const meta=result.unchanged?currentMeta:result.value;
      if(!meta||meta.schema_version!==1||!meta.snapshot_id)throw new Error('版本資訊尚未發布');
      if(!awake())return false;
      if(currentSnapshot?.snapshot_id===meta.snapshot_id){
        if(!result.unchanged)metaETag=result.etag;
        showMeta(meta,Date.now());if(renderedFreshness!==freshnessKey())applySnapshot(currentSnapshot);break;
      }
      const latest=await publicJSON('/api/dashboard-latest'+(afterScan?'?sync=1':''));
      const snapshot=latest.value;
      if(snapshot?.schema_version!==1||snapshot.snapshot_id!==meta.snapshot_id){metaETag=null;if(attempt===0)continue;throw new Error('版本正在切換，稍後重試')}
      // Only commit a matching pair; stale/late responses never mix two snapshots.
      applySnapshot(snapshot);showMeta(meta,Date.now());metaETag=result.etag;changed=true;break;
    }
    failureCount=0;retryAfter=0;
    updateNotice(syncDeadline&&currentSnapshot?.snapshot_id===syncExpected?'掃描已完成，等待公開版本同步':changed?'已載入新版本':'已是最新版本');
    lastAcceptedCheck=Date.now();
    dashboardChannel?.postMessage(changed
      ?{type:'snapshot',schema:1,checkedAt:lastAcceptedCheck,meta:currentMeta,snapshot:currentSnapshot}
      :{type:'meta',schema:1,checkedAt:lastAcceptedCheck,meta:currentMeta});
    return true;
  }catch(error){
    failureCount++;
    updateNotice(currentSnapshot?'更新暫時失敗 · 保留上次資料，稍後重試':'尚無可用市場資料 · 稍後重試');
    dashboardChannel?.postMessage({type:'read-error',schema:1,checkedAt:Date.now()});
    if(!currentSnapshot){$('#emptyTitle').textContent='市場資料尚未就緒';$('#emptyText').textContent='請稍後重新檢查；服務暫時無法提供公開版本。';$('#empty').classList.add('show')}
    return false;
  }finally{app.loading=false;$('#refreshBtn').disabled=false}
}
async function load(options={}){
  if(!awake())return false;
  if(loadTask){
    if(options.afterScan){await loadTask;return load(options)}
    return loadTask;
  }
  loadTask=loadOnce(options);
  try{return await loadTask}finally{loadTask=null}
}
function scheduleRefresh(){
  clearTimeout(refreshTimer);
  if(!awake())return;
  const delay=failureCount?Math.max(Math.min(300000,15000*2**Math.min(failureCount-1,5)),retryAfter):60000;
  refreshTimer=setTimeout(async()=>{await load();scheduleRefresh()},Math.max(jitter(delay),retryAfter));
}
function stopLeader(){
  clearTimeout(refreshTimer);clearTimeout(electionTimer);
  if(releaseLeader){releaseLeader();releaseLeader=null}
}
function elect(){
  if(!awake())return;
  if(!coordinated){
    if(!failureCount&&Date.now()-lastAcceptedCheck>5000)load().finally(scheduleRefresh);
    else scheduleRefresh();
    return;
  }
  navigator.locks.request('radar-dashboard-v1',{ifAvailable:true},async lock=>{
    if(!lock)return;
    const released=new Promise(resolve=>{releaseLeader=resolve});
    if(!awake()){releaseLeader();return}
    if(!failureCount&&Date.now()-lastAcceptedCheck>5000)await load();
    if(awake())scheduleRefresh();
    await released;
  }).catch(()=>{load().finally(scheduleRefresh)}).finally(()=>{
    if(awake())electionTimer=setTimeout(elect,2000);
  });
}
if(dashboardChannel)dashboardChannel.onmessage=event=>{
  const message=event.data;
  if(message?.type==='hello'&&releaseLeader&&currentSnapshot){dashboardChannel.postMessage({type:'snapshot',schema:1,checkedAt:lastAcceptedCheck,meta:currentMeta,snapshot:currentSnapshot});return}
  if(message?.schema!==1||!awake())return;
  if(loadTask||!Number.isFinite(message.checkedAt)||message.checkedAt<=lastAcceptedCheck)return;
  if(message.type==='read-error'){updateNotice(currentSnapshot?'其他分頁更新失敗 · 保留上次資料':'公開資料尚待確認');return}
  if(!['snapshot','meta'].includes(message.type)||message.meta?.schema_version!==1)return;
  const snapshot=message.type==='snapshot'?message.snapshot:currentSnapshot;
  if(snapshot?.schema_version!==1||message.meta.snapshot_id!==snapshot.snapshot_id)return;
  lastAcceptedCheck=message.checkedAt;
  if(currentSnapshot?.snapshot_id!==snapshot.snapshot_id)applySnapshot(snapshot);
  // Its ETag is not ours; revalidate without a validator on the next local check.
  metaETag=null;
  showMeta(message.meta,message.checkedAt);failureCount=0;retryAfter=0;updateNotice('已同步其他分頁的公開版本');
};
function resume(){stopLeader();clearTimeout(syncTimer);if(awake()){load().finally(elect);resumeSync()}else updateNotice(navigator.onLine===false?'離線 · 保留上次資料':'背景更新已暫停')}
document.addEventListener('visibilitychange',resume);window.addEventListener('online',resume);window.addEventListener('offline',resume);
window.addEventListener('pagehide',event=>{stopLeader();clearTimeout(syncTimer);if(!event.persisted)dashboardChannel?.close()});
window.addEventListener('pageshow',event=>{if(event.persisted)resume()});
function resumeSync(){
  clearTimeout(syncTimer);
  if(!syncDeadline||!awake())return;
  if(currentSnapshot&&currentSnapshot.snapshot_id!==syncExpected){syncDeadline=0;updateNotice('掃描結果已同步');return}
  if(Date.now()>=syncDeadline){syncDeadline=0;updateNotice('掃描結果仍待同步 · 將按一般週期檢查');return}
  syncTimer=setTimeout(async()=>{await load({afterScan:true});resumeSync()},5000);
}
window.refreshRadar=options=>load(options||{});
window.refreshRadarAge=()=>{
  if(!currentMeta||Date.now()<nextAgeCheck)return;
  nextAgeCheck=Date.now()+60000;
  showMeta(currentMeta);
  if(renderedFreshness!==freshnessKey())applySnapshot(currentSnapshot);
};
window.radarSnapshotId=()=>currentSnapshot?.snapshot_id;
window.waitForRadarPublication=async expected=>{
  syncExpected=expected??currentSnapshot?.snapshot_id;syncDeadline=Date.now()+45000;
  await load({afterScan:true});resumeSync();
  return !!currentSnapshot&&currentSnapshot.snapshot_id!==syncExpected;
};
dashboardChannel?.postMessage({type:'hello',schema:1});
load().finally(elect);
