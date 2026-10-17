importScripts('config.js', 'migrate.js');
const LOCAL = 'http://127.0.0.1:8000';
const ALARM = 'ggab-sync';
let running;
let lastTokenSync=0;
let tokenSync;
function tokenSyncEnabled() {
  const version=chrome.runtime.getManifest?.().version?.split('.').map(Number);
  return Boolean(version && (version[0]>1 || (version[0]===1 && version[1]>=5)));
}
async function syncTokens() {
  if(!tokenSyncEnabled())return {ok:false};
  if(tokenSync)return tokenSync;
  if(Date.now()-lastTokenSync<30000)return {ok:true,skipped:true};
  tokenSync=(async()=>{try {
    const {bridgeToken}=await chrome.storage.local.get('bridgeToken');
    if(!bridgeToken)throw new Error('请管理员连接后台扩展');
    const balance=await ggabJson('https://app.getgoodatbadminton.com/api/tokens/balance');
    if(!Number.isSafeInteger(balance.total)||balance.total<0)throw new Error('GGAB 未返回明确 Token 余额');
    let items=[],cursor=null,activityError;
    try {
    for(let page=0;page<10;page++) {
      const activity=await ggabJson('https://app.getgoodatbadminton.com/api/tokens/activity?limit=25'+(cursor?'&before='+encodeURIComponent(cursor):''));
      if(!Array.isArray(activity.items))throw new Error('GGAB Token 明细格式无效');
      items.push(...activity.items);if(!activity.nextCursor)break;
      if(activity.nextCursor===cursor)throw new Error('GGAB Token 明细分页未前进');
      cursor=activity.nextCursor;
    }
    } catch(error) {activityError=error.message.slice(0,500);}
    await local('token-balance',{total:balance.total,activity:items,...(activityError?{activity_error:activityError}:{})});lastTokenSync=Date.now();
    return {ok:true};
  } catch(error) {
    await local('token-balance',{error:error.message.slice(0,500)}).catch(()=>{});
    await reportError(error);
    return {ok:false};
  }} )().finally(()=>{tokenSync=null;});
  return tokenSync;
}
const MAX_BATCH_STEPS = 32;
const MAX_BATCH_MS = 120000;
async function ensureAlarm() {
  // Chrome may clear extension alarms after a browser restart or update.
  // Recheck whenever the worker starts or the local page wakes it.
  if (!await chrome.alarms.get(ALARM))
    await chrome.alarms.create(ALARM, {periodInMinutes:1});
}
function reportError(error) {
  return chrome.storage.local.set({bridgeError:{at:new Date().toISOString(), message:error.message}});
}
function parseProject(payload, expectedId) {
  const project = payload?.project;
  if (!project || project.id !== expectedId) throw new Error('GGAB 项目 ID 与本地关联不匹配，保留本地状态');
  const statuses = {setup:'review', review:'review', tracking:'processing', processing:'processing', finalized:'finalized'};
  const status = statuses[project.status];
  if (!status) throw new Error('GGAB 返回未知项目状态，保留本地状态');
  return {status, diagnostic:{source:'ggab_project_api', remote_project_id:project.id,
    remote_status:project.status, title:typeof project.title === 'string' ? project.title.slice(0,200) : null}};
}
async function local(path, body) {
  const {bridgeToken} = await chrome.storage.local.get('bridgeToken');
  if (!bridgeToken) throw new Error('请在本地 Match Tracker 连接后台扩展');
  const r = await fetch(LOCAL + '/api/bridge/worker/' + path, {
    method: 'POST', headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + bridgeToken,
      'X-GGAB-Subject': await Migration.identity()},
    body: JSON.stringify(body), signal: AbortSignal.timeout(path.endsWith('/shots-export') || path.startsWith('full-export/') ? 60000 : 15000), cache: 'no-store'});
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(typeof detail.detail === 'string' ? detail.detail.slice(0,500) : '本地桥接失败：HTTP ' + r.status);
  }
  return r.json();
}
async function ggabJson(url) {
  const r = await fetch(url, {credentials:'include', cache:'no-store', signal:AbortSignal.timeout(25000)});
  if (new URL(r.url).pathname.startsWith('/signin') || [401,403].includes(r.status)) throw new Error('GGAB 登录会话不可用');
  if (!r.ok || r.url !== url || !r.headers.get('content-type')?.includes('application/json'))
    throw new Error('GGAB 标注接口不可用：HTTP ' + r.status);
  const content = await r.text();
  if (content.length > 25000000) throw new Error('GGAB 标注数据超过大小限制');
  return JSON.parse(content);
}
async function exportFullCsv(job) {
  let ticket;
  try {
    const started = await local('projects/' + encodeURIComponent(job.local_project_id) + '/full-export/start',
      {remote_project_id:job.remote_project_id});
    if (started.skipped) return;
    ticket = started.ticket;
    if (typeof ticket !== 'string' || !/^[\w-]{32,100}$/.test(ticket)) throw new Error('全量导出任务凭据无效');
    // The official Export button uses this unfiltered, account-wide endpoint.
    const url = 'https://app.getgoodatbadminton.com/api/export/shots.csv';
    const response = await fetch(url, {credentials:'include',cache:'no-store',
      headers:{Accept:'text/csv'},signal:AbortSignal.timeout(45000)});
    if ([401,403].includes(response.status) || new URL(response.url).pathname.startsWith('/signin'))
      throw new Error('GGAB 登录已失效，全量 CSV 未更新');
    if (!response.ok || response.url !== url || !response.headers.get('content-type')?.includes('text/csv'))
      throw new Error('GGAB 全量 CSV 导出失败：HTTP ' + response.status);
    if (Number(response.headers.get('content-length')) > 50000000) throw new Error('GGAB 全量 CSV 超过大小限制');
    const bytes = await response.arrayBuffer();
    if (!bytes.byteLength || bytes.byteLength > 50000000) throw new Error('GGAB 全量 CSV 为空或超过大小限制');
    const csv_text = new TextDecoder('utf-8',{fatal:true}).decode(bytes);
    await local('full-export/' + encodeURIComponent(ticket), {csv_text});
  } catch (error) {
    if (ticket) {
      // A failed download/import retains the last successfully imported data.
      // The next normal status observation safely retries this read-only work.
      await local('full-export/' + encodeURIComponent(ticket), {error:error.message.slice(0,500)}).catch(() => {});
    }
    await chrome.storage.local.set({bridgeError:{at:new Date().toISOString(), message:'全量 CSV 同步失败：' + error.message}});
  }
}
async function processOne() {
    const {job} = await local('claim', {extension_version:chrome.runtime.getManifest?.().version || null});
    if (!job) return false;
    let result;
    try {
      if (job.kind === 'migrate') {
        result = await Migration.step(job, local);
      } else if (job.kind === 'check_video') {
        result = {video_check:await Migration.checkVideo(job)};
      } else {
      if (!['sync_status','inspect_remote'].includes(job.kind) || !/^https:\/\/app\.getgoodatbadminton\.com\/tracker\/[a-z0-9-]+$/i.test(job.remote_url)) throw new Error('拒绝未知任务或非 GGAB 地址');
      const api = 'https://app.getgoodatbadminton.com/api/tracker/projects/' + job.remote_project_id;
      if (!/^[0-9a-f-]{36}$/i.test(job.remote_project_id) || job.remote_url !== 'https://app.getgoodatbadminton.com/tracker/' + job.remote_project_id) throw new Error('GGAB 项目 ID 与地址不匹配');
      const r = await fetch(api, {credentials: 'include', cache: 'no-store', signal: AbortSignal.timeout(25000)});
      if (new URL(r.url).pathname.startsWith('/signin') || [401,403].includes(r.status)) throw new Error('GGAB 登录已失效或后台请求无法使用登录会话；请在同一浏览器登录');
      if (!r.ok || r.url.split('?')[0] !== api) throw new Error('GGAB 项目接口不可用或地址不匹配：HTTP ' + r.status);
      if (!r.headers.get('content-type')?.includes('application/json')) throw new Error('GGAB 项目接口返回非 JSON，保留本地状态');
      const content = await r.text();
      if (content.length > 2000000) throw new Error('GGAB 项目数据超出大小限制');
      result = parseProject(JSON.parse(content), job.remote_project_id);
      // The official editor uses settle-info to identify the exact hold
      // transaction. Activity id + nonpending amount is the final charge.
      if(job.kind==='sync_status' && tokenSyncEnabled()) {
        try {
          const info=await ggabJson(api+'/settle-info');
          if(['auto_track','auto_track_classify_only'].includes(info.submissionSource) &&
              typeof info.holdTransactionId==='string' && info.holdTransactionId.length>0 && info.holdTransactionId.length<=200)
            result.hold_transaction_id=info.holdTransactionId;
        } catch { /* Retain the quota hold until a verifiable receipt arrives. */ }
      }
      if (job.kind === 'sync_status' && job.video)
        result.video_check = await Migration.checkVideo(job);
      }
    } catch (error) {result = {error: error.message.slice(0,500),
      ...(error.remoteProjectId ? {diagnostic:{remote_project_id:error.remoteProjectId}} : {})};}
    const accepted = await local('jobs/' + encodeURIComponent(job.id) + '/result', {lease:job.lease, ...result});
    if (accepted.ok && job.kind === 'sync_status' && job.needs_export && result?.status === 'finalized') {
      await exportFullCsv(job);
    }
    // Each chunk is committed locally before claiming the next lease. Polling,
    // failures and user confirmation yield back to the periodic recovery alarm.
    return Boolean(accepted.ok && result.progress &&
      !['wait_video','await_confirmation','processing','complete'].includes(result.progress.stage));
}
async function sync() {
  if (running) return running;
  let continueSoon = false;
  running = (async () => {
    await syncTokens();
    const started = Date.now();
    for (let step = 0; step < MAX_BATCH_STEPS; step++) {
      if (!await processOne()) return;
      if (Date.now() - started >= MAX_BATCH_MS) break;
    }
    continueSoon = true;
  })().catch(reportError).finally(() => {
    running = null;
    // Yield between bounded batches without imposing an alarm-sized gap.
    // If the browser suspends this worker, the ordinary alarm resumes via HEAD.
    if (continueSoon) setTimeout(() => sync(), 0);
  });
  return running;
}
async function schedule() {
  // A failed alarm check must not block a direct local-page wake.
  await ensureAlarm().catch(reportError);
  await sync();
}
chrome.runtime.onInstalled.addListener(schedule);
chrome.runtime.onStartup.addListener(schedule);
chrome.alarms.onAlarm.addListener(a => {if(a.name === ALARM) sync();});
chrome.runtime.onMessage.addListener((message,sender,reply) => {
  if(sender.id !== chrome.runtime.id || !sender.tab) return;
  let origin;
  try {origin = new URL(sender.url).origin;} catch {return;}
  if(origin === 'https://app.getgoodatbadminton.com' && message?.type === 'ggab-session-updated') {
    Migration.rememberSession(message.session?.ok ? message.session : null)
      .then(() => {
        reply({ok:true});
        if (message.session?.ok) {
          // A freshly restored login should resume pending work without
          // waiting for the next one-minute alarm.
          const current = running;
          if (current) current.then(() => sync());
          else sync();
        }
      }).catch(() => reply({ok:false}));
    return true;
  }
  if(origin !== LOCAL) return;
  if(message.type === 'sync-tokens') {
    // Refreshing account data never claims jobs or authorizes tracking.
    syncTokens().then(reply).catch(()=>reply({ok:false}));
    return true;
  }
  if(message.type === 'wake') {
    // A local page is a backup clock if the browser silently drops alarms.
    // This only resumes already paired work; it cannot authorize a fee.
    reply({ok:true});
    chrome.storage.local.get('bridgeToken').then(({bridgeToken}) =>
      bridgeToken ? schedule() : ensureAlarm()).catch(reportError);
    return;
  }
  if(message.type !== 'pair' || typeof message.token !== 'string' || message.token.length < 32) return;
  chrome.storage.local.set({bridgeToken:message.token})
    .then(async () => local('source', {user_id:await Migration.identity()}))
    .then(schedule).then(() => reply({ok:true})).catch(() => reply({ok:false}));
  return true;
});
ensureAlarm().catch(reportError);
