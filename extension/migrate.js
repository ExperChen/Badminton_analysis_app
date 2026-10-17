const Migration = (() => {
  const ORIGIN = 'https://app.getgoodatbadminton.com';
  const SUPA = 'https://adscteqgwmvherkctzfm.supabase.co';
  function publicAnonKey() {
    const value = globalThis.GGAB_PUBLIC_ANON_KEY;
    if (typeof value !== 'string' || !value.trim() || /[\r\n]/.test(value)) {
      throw new Error('GGAB 上游公开 anon key 未配置；请在 extension/config.js 设置 GGAB_PUBLIC_ANON_KEY 并重新加载扩展');
    }
    return value.trim();
  }
  const LOCAL = 'http://127.0.0.1:8000';
  const progress = (stage, data = {}) => ({progress:{stage, data}});
  const SESSION_REFRESH_COOLDOWN_MS = 15 * 60 * 1000;
  let cachedSession = null;
  let sessionRequest = null;

  function validSession(value) {
    return value && typeof value.accessToken === 'string' && value.accessToken.length > 0 &&
      /^[0-9a-f-]{36}$/i.test(value.userId || '') && Number.isFinite(value.expiresAt) &&
      value.expiresAt > Date.now()/1000 + 60;
  }

  async function rememberSession(value) {
    cachedSession = validSession(value) ? {accessToken:value.accessToken,
      userId:value.userId, expiresAt:value.expiresAt} : null;
    // Browser-memory storage survives worker suspension, but not browser exit.
    // Default access is restricted to trusted extension contexts.
    if (cachedSession) await chrome.storage.session.set({ggabSession:cachedSession});
    else await chrome.storage.session.remove('ggabSession');
  }

  async function readTabSession(tab) {
    try {return await chrome.tabs.sendMessage(tab.id, {type:'ggab-session'});} catch {}
    // An extension reload does not inject manifest content scripts into tabs
    // that were already open. Reinstall our own idempotent script in-place.
    if (!chrome.scripting?.executeScript) return null;
    try {
      await chrome.scripting.executeScript({target:{tabId:tab.id}, files:['remote.js']});
      return await chrome.tabs.sendMessage(tab.id, {type:'ggab-session'});
    } catch {return null;}
  }

  async function refreshBackgroundTab(tab) {
    // Only refresh a pre-existing GGAB tab that the user is not viewing.
    // Never interrupt the foreground tab or create/close browser tabs.
    if (!chrome.tabs.reload) return null;
    if (tab.active !== false) {
      if (!chrome.windows?.get || tab.windowId == null) return null;
      try {if ((await chrome.windows.get(tab.windowId)).focused) return null;}
      catch {return null;}
    }
    const previous = (await chrome.storage.session.get('ggabRecovery')).ggabRecovery || {};
    const now = Date.now();
    if (Number.isFinite(previous[tab.id]) && now - previous[tab.id] < SESSION_REFRESH_COOLDOWN_MS) return null;
    const recent = Object.fromEntries(Object.entries(previous).filter(([id,at]) =>
      /^\d+$/.test(id) && Number.isFinite(at) && now - at < SESSION_REFRESH_COOLDOWN_MS));
    await chrome.storage.session.set({ggabRecovery:{...recent,[tab.id]:now}});
    try {await chrome.tabs.reload(tab.id);} catch {return null;}
    for (let attempt = 0; attempt < 12; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 500));
      const response = await readTabSession(tab);
      if (response?.ok && validSession(response)) return response;
    }
    return null;
  }

  async function session() {
    if (validSession(cachedSession)) return cachedSession;
    if (sessionRequest) return sessionRequest;
    sessionRequest = (async () => {
      const stored = (await chrome.storage.session.get('ggabSession')).ggabSession;
      if (validSession(stored)) {cachedSession = stored; return cachedSession;}
      await rememberSession(null);
      const tabs = await chrome.tabs.query({url:ORIGIN + '/*'});
      for (const tab of tabs) {
        const response = await readTabSession(tab);
        if (response?.ok && validSession(response)) {
          await rememberSession(response);
          return cachedSession;
        }
      }
      for (const tab of tabs) {
        const response = await refreshBackgroundTab(tab);
        if (response) {
          await rememberSession(response);
          return cachedSession;
        }
      }
      // A real logout or no existing GGAB tab still requires user login.
      throw new Error('GGAB 登录会话不可用；请在同一浏览器登录并刷新已有 GGAB 页面');
    })();
    try {return await sessionRequest;} finally {sessionRequest = null;}
  }

  async function remoteFetch(url, options) {
    const response = await fetch(url, options);
    if (new URL(response.url || url).pathname.startsWith('/signin') || [401,403].includes(response.status)) {
      await rememberSession(null);
      throw new Error('GGAB 登录会话不可用；请在同一浏览器重新登录');
    }
    return response;
  }

  async function json(url, options = {}, maxBytes = 2000000) {
    const response = await remoteFetch(url, {...options, cache:'no-store', signal:AbortSignal.timeout(25000)});
    const isJson = response.headers.get('content-type')?.includes('application/json');
    if (!response.ok) {
      let detail = '';
      if (isJson) {
        try {
          const body = await response.text();
          if (body.length <= maxBytes) {
            const error = JSON.parse(body);
            detail = [error.error, error.message].find(value => typeof value === 'string') || '';
            const issues = error.issues || error.details?.issues;
            if (Array.isArray(issues)) detail += ' ' + issues.slice(0,4).map(issue =>
              [Array.isArray(issue.path) ? issue.path.join('.') : '',
                typeof issue.message === 'string' ? issue.message : ''].filter(Boolean).join(': ')).join('; ');
          }
        } catch {}
      }
      throw new Error('GGAB 请求失败：HTTP ' + response.status + ' (' + new URL(url).pathname + ')' +
        (detail.trim() ? ' · ' + detail.trim().slice(0,300) : ''));
    }
    if (!isJson) throw new Error('GGAB 返回非 JSON：' + new URL(url).pathname);
    const body = await response.text();
    if (body.length > maxBytes) throw new Error('GGAB 返回的数据过大');
    return JSON.parse(body);
  }
  const ggab = (path, options = {}) => json(ORIGIN + path, {credentials:'include', ...options});
  const supaHeaders = auth => ({apikey:publicAnonKey(), Authorization:'Bearer ' + auth.accessToken});
  const supa = (path, auth, options = {}) => json(SUPA + path, {...options,
    headers:{...supaHeaders(auth), ...(options.headers || {})}});

  async function asset(job, path, headers = {}) {
    const {bridgeToken} = await chrome.storage.local.get('bridgeToken');
    const response = await fetch(LOCAL + '/api/bridge/worker/projects/' +
      encodeURIComponent(job.local_project_id) + path, {headers:{Authorization:'Bearer ' + bridgeToken,
        'X-GGAB-Subject': await identity(), ...headers},
        cache:'no-store', signal:AbortSignal.timeout(45000)});
    if (!response.ok) throw new Error('本地原视频或截图不可用：HTTP ' + response.status);
    return response;
  }

  function originalBox(snapshot) {
    const {x,y,width:w,height:h} = snapshot.box;
    switch (snapshot.rotation) {
      case 0: return [x,y,w,h];
      case 90: return [y,1-x-w,h,w];
      case 180: return [1-x-w,1-y-h,w,h];
      case 270: return [1-y-h,x,h,w];
      default: throw new Error('不支持的截图旋转角度');
    }
  }

  function segments(source) {
    return source.map(item => {
      if (!['near','far'].includes(item.first_server)) throw new Error('片段先发球方无效');
      const result = {startS:item.start,endS:item.end,firstServer:item.first_server};
      for (const [local, remote] of [['a_start','scoreAStart'],['b_start','scoreBStart'],
        ['a_end','scoreAEnd'],['b_end','scoreBEnd']]) if (item[local] != null) result[remote] = item[local];
      return result;
    });
  }

  async function restList(table, query, auth) {
    return supa('/rest/v1/' + table + '?' + query, auth, {headers:{Accept:'application/json'}}, 10000000);
  }
  async function checkVideo(job) {
    const expected = job.video;
    if (!expected || !/^[0-9a-f-]{36}$/i.test(expected.video_id || ''))
      throw new Error('缺少明确的远端视频 ID，不能按名称检测');
    const observed = {};
    const result = (status, reason) => ({video_id:expected.video_id, status, reason,
      rule_version:2, observed});
    try {
      const auth = await session();
      const rows = await restList('videos', 'select=*&id=eq.' + encodeURIComponent(expected.video_id) +
        '&owner_user_id=eq.' + auth.userId, auth);
      if (!Array.isArray(rows)) throw new Error('GGAB 视频接口结构不匹配');
      if (!rows.length) return result('missing', 'GGAB 当前账号的视频库无此视频 ID；片源不可用');
      if (rows.length !== 1 || rows[0].id !== expected.video_id)
        throw new Error('GGAB 视频 ID 不唯一或不匹配');
      const video = rows[0];
      observed.filename = typeof video.title === 'string' ? video.title.slice(0,500) : null;
      observed.metadata_size_bytes = video.size_bytes != null && Number.isSafeInteger(Number(video.size_bytes)) &&
        Number(video.size_bytes) >= 0 ? Number(video.size_bytes) : null;
      observed.duration = video.duration_s != null && Number.isFinite(Number(video.duration_s)) &&
        Number(video.duration_s) >= 0 ? Number(video.duration_s) : null;
      if (video.deleted_at) return result('missing', 'GGAB 视频记录已删除；片源不可用');
      if (video.status === 'error') return result('failed', 'GGAB 视频处理失败');
      if (['uploading','processing','pending'].includes(video.status))
        return result('processing', 'GGAB 视频仍在上传或处理，尚未完成');
      if (video.status !== 'ready') throw new Error('GGAB 返回未知视频状态');
      if (!expected.filename || !Number.isSafeInteger(expected.size_bytes) || expected.size_bytes <= 0)
        throw new Error('本地缺少原片身份信息，不能判定上传完成');
      if (expected.duration != null && observed.duration == null)
        throw new Error('GGAB 未提供有效时长，不能核实原片');
      if (expected.duration != null && Math.abs(observed.duration - expected.duration) >
          Math.max(.25, expected.duration * .001))
        return result('mismatch', `视频时长不匹配：本地 ${expected.duration} 秒，GGAB ${observed.duration} 秒`);
      // title is editable; size_bytes can describe processed playback media.
      // Neither proves that a different original was uploaded. Verify the
      // exact registered original object instead, never a guessed filename.
      const validPath = path => typeof path === 'string' && path.startsWith(auth.userId + '/') &&
        !path.split('/').some(part => !part || part === '.' || part === '..');
      if (!validPath(video.storage_path) || (expected.storage_path != null && !validPath(expected.storage_path)))
        throw new Error('GGAB 原片存储位置不完整或不属于当前账号');
      const path = expected.storage_path || video.storage_path;
      if (expected.storage_path && video.storage_path !== path &&
          video.storage_path !== path.slice(0,path.lastIndexOf('/')+1) + 'progressive.mp4')
        return result('mismatch', 'GGAB 视频存储地址与已登记的上传原片不属于同一视频目录');
      if (!expected.storage_path && String(video.title || '').normalize('NFC').toLowerCase() !==
          expected.filename.normalize('NFC').toLowerCase())
        throw new Error('视频名称已变化且缺少已登记的原片存储地址，需要核实关联，不能按标题猜测');
      // Read only one byte, never download/re-upload the entire video. If a
      // server ignores Range, cancel the body immediately after its headers.
      const response = await remoteFetch(SUPA + '/storage/v1/object/authenticated/videos/' +
        path.split('/').map(encodeURIComponent).join('/'), {method:'GET',cache:'no-store',
        headers:{...supaHeaders(auth), Range:'bytes=0-0'}, signal:AbortSignal.timeout(20000)});
      await response.body?.cancel();
      if (response.status === 404) return result('missing', 'GGAB 原片存储对象不存在；片源不可用');
      const range = /^bytes 0-0\/(\d+)$/.exec(response.headers.get('content-range') || '');
      const bytes = response.status === 206 && range ? Number(range[1]) :
        response.status === 200 ? Number(response.headers.get('content-length')) : NaN;
      if (!Number.isSafeInteger(bytes) || bytes <= 0)
        throw new Error('无法核实 GGAB 原片存储大小：HTTP ' + response.status);
      observed.original_size_bytes = bytes;
      if (bytes !== expected.size_bytes) return result('mismatch',
        `原片实际存储大小不匹配：本地 ${expected.size_bytes} 字节，GGAB ${bytes} 字节；上传可能不完整`);
      const notes = [];
      if (video.title !== expected.filename) notes.push('显示名称已变化，不作为原片身份依据');
      if (observed.metadata_size_bytes !== expected.size_bytes)
        notes.push('GGAB 元数据大小为 ' + (observed.metadata_size_bytes ?? '未提供') + '，不作为原片大小依据');
      return result('ready', 'GGAB 视频 Ready，视频 ID、原片实际存储大小和时长核对通过' +
        (notes.length ? '；' + notes.join('；') : ''));
    } catch (error) {
      return result('unknown', '检测未完成：' + error.message.slice(0,450));
    }
  }
  async function restInsert(table, row, auth) {
    const result = await supa('/rest/v1/' + table, auth, {method:'POST',
      headers:{'Content-Type':'application/json', Prefer:'return=representation'}, body:JSON.stringify(row)});
    if (!Array.isArray(result) || result.length !== 1 || !result[0]?.id) throw new Error('GGAB 球员资料返回结构已变化');
    return result[0].id;
  }
  async function playerProfiles(job, auth) {
    const ids = {};
    for (const player of job.project.players) {
      if (player.is_you && !job.shared_local_users) {
        const query = 'select=id&owner_user_id=eq.' + auth.userId + '&is_primary=eq.true&limit=1';
        const existing = await restList('user_player_profiles', query, auth);
        ids[player.slot] = {userPlayerProfileId:existing[0]?.id || await restInsert('user_player_profiles',
          {owner_user_id:auth.userId, display_name:player.name, workspace_player_id:null, is_primary:true}, auth),
          workspacePlayerId:null};
      } else {
        const query = 'select=id,full_name&owner_user_id=eq.' + auth.userId + '&full_name=eq.' + encodeURIComponent(player.name);
        const existing = await restList('workspace_players', query, auth);
        if (existing.length > 1) throw new Error('GGAB 同名球员资料不唯一，请先在账号中整理');
        ids[player.slot] = {workspacePlayerId:existing[0]?.id || await restInsert('workspace_players',
          {owner_user_id:auth.userId, full_name:player.name}, auth), userPlayerProfileId:null};
      }
    }
    return ids;
  }

  async function step(job, local) {
    const auth = await session();
    if (job.expected_ggab_subject && auth.userId !== job.expected_ggab_subject)
      throw new Error('共享 GGAB 身份已变化，停止当前任务');
    const data = job.data;
    const filename = job.video.filename;
    if (job.stage === 'lookup_video') {
      const videos = await restList('videos', 'select=*&owner_user_id=eq.' + auth.userId, auth);
      const matches = videos.filter(v => v.status !== 'error' &&
        String(v.title || '').toLowerCase() === filename.toLowerCase() &&
        Number(v.size_bytes) === job.video.size_bytes);
      if (matches.length > 1) throw new Error('GGAB 视频库有多个同名同尺寸原片，停止自动复用或重复上传');
      if (matches.length === 1) {
        const match = matches[0];
        if (!match.id || !match.storage_path) throw new Error('GGAB 已有同名同尺寸视频，但存储信息不完整；停止重复上传');
        if (match.status !== 'ready') return progress('wait_video', {source:'reused_existing',
          video_id:match.id, storage_path:match.storage_path});
        if (!Number.isFinite(Number(match.duration_s)) || match.duration_s == null ||
            Math.abs(Number(match.duration_s) - job.video.duration) > Math.max(.25, job.video.duration * .001))
          throw new Error('GGAB 同名同尺寸视频的时长不匹配；停止重复上传，请人工核验');
        return progress('upload_snapshots', {source:'reused_existing', video_id:match.id,
          storage_path:match.storage_path,
          remote_evidence:{remote_video_id:match.id, filename,
            duration:Number(match.duration_s), size_bytes:Number(match.size_bytes), method:'verified_existing_video'}});
      }
      return progress('init_video');
    }
    if (job.stage === 'init_video') {
      const contentType = filename.toLowerCase().endsWith('.mov') ? 'video/quicktime' : 'video/mp4';
      const remote = await ggab('/api/videos/init', {method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({filename, sizeBytes:job.video.size_bytes, contentType})});
      if (!remote.videoId || !remote.storagePath) throw new Error('GGAB 上传初始化未返回视频 ID');
      return progress('start_upload', {source:'uploaded', video_id:remote.videoId, storage_path:remote.storagePath,
        content_type:contentType});
    }
    if (job.stage === 'start_upload') {
      const metadata = [['bucketName','videos'],['objectName',data.storage_path],
        ['contentType',data.content_type],['cacheControl','3600']]
        .map(([key,value]) => key + ' ' + btoa(unescape(encodeURIComponent(value)))).join(',');
      const r = await remoteFetch(SUPA + '/storage/v1/upload/resumable', {method:'POST',
        headers:{...supaHeaders(auth),'Tus-Resumable':'1.0.0','Upload-Length':String(job.video.size_bytes),
          'Upload-Metadata':metadata,'x-upsert':'true'}, signal:AbortSignal.timeout(25000)});
      if (!r.ok || !r.headers.get('location')) throw new Error('GGAB 可续传上传初始化失败：HTTP ' + r.status);
      const tusUrl = new URL(r.headers.get('location'), SUPA).toString();
      if (!tusUrl.startsWith(SUPA + '/storage/v1/upload/resumable/')) throw new Error('GGAB 上传地址不可信');
      return progress('upload_video', {tus_url:tusUrl, upload_offset:0});
    }
    if (job.stage === 'upload_video') {
      if (!data.tus_url?.startsWith(SUPA + '/storage/v1/upload/resumable/')) throw new Error('可续传上传地址不可信');
      const headers = {...supaHeaders(auth),'Tus-Resumable':'1.0.0'};
      const head = await remoteFetch(data.tus_url, {method:'HEAD',headers,signal:AbortSignal.timeout(20000)});
      if (!head.ok) throw new Error('无法读取 GGAB 上传进度：HTTP ' + head.status);
      const offset = Number(head.headers.get('upload-offset'));
      if (!Number.isSafeInteger(offset) || offset < 0 || offset > job.video.size_bytes) throw new Error('GGAB 上传偏移量无效');
      if (offset === job.video.size_bytes) return progress('finalize_video', {upload_offset:offset});
      const end = Math.min(offset + 12 * 1024 * 1024, job.video.size_bytes) - 1;
      const source = await asset(job, '/video', {Range:'bytes=' + offset + '-' + end});
      if (source.status !== 206) throw new Error('本地视频不支持分块读取');
      const chunk = await source.arrayBuffer();
      if (chunk.byteLength !== end - offset + 1) throw new Error('本地视频分块大小不符');
      const patch = await remoteFetch(data.tus_url, {method:'PATCH',headers:{...headers,'Upload-Offset':String(offset),
        'Content-Type':'application/offset+octet-stream'},body:chunk,signal:AbortSignal.timeout(60000)});
      if (!patch.ok) throw new Error('GGAB 分块上传失败：HTTP ' + patch.status);
      const next = Number(patch.headers.get('upload-offset'));
      if (!Number.isSafeInteger(next) || next < end + 1 || next > job.video.size_bytes) throw new Error('GGAB 上传确认偏移量无效');
      return progress(next === job.video.size_bytes ? 'finalize_video' : 'upload_video', {upload_offset:next});
    }
    if (job.stage === 'finalize_video') {
      const response = await remoteFetch(ORIGIN + '/api/videos/' + encodeURIComponent(data.video_id) + '/finalize',
        {method:'POST', credentials:'include', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({durationS:job.video.duration}), signal:AbortSignal.timeout(25000)});
      if (!response.ok || new URL(response.url).pathname.startsWith('/signin'))
        throw new Error('GGAB 视频完成上传失败：HTTP ' + response.status);
      return progress('wait_video');
    }
    if (job.stage === 'wait_video') {
      const rows = await restList('videos', 'select=*&id=eq.' + encodeURIComponent(data.video_id) + '&owner_user_id=eq.' + auth.userId, auth);
      if (rows.length !== 1) throw new Error('GGAB 上传视频未找到');
      if (rows[0].status === 'error') throw new Error('GGAB 视频处理失败');
      if (rows[0].status !== 'ready') return progress('wait_video');
      if (data.source === 'reused_existing' && (rows[0].storage_path !== data.storage_path ||
          String(rows[0].title || '').toLowerCase() !== filename.toLowerCase() ||
          Number(rows[0].size_bytes) !== job.video.size_bytes))
        throw new Error('GGAB 待处理视频的文件名、大小或存储地址已变化；未复用');
      if (Math.abs(Number(rows[0].duration_s) - job.video.duration) > Math.max(.25, job.video.duration * .001))
        throw new Error('GGAB 处理后的视频时长不匹配');
      return progress('upload_snapshots', {remote_evidence:{remote_video_id:data.video_id,filename,
        duration:Number(rows[0].duration_s),size_bytes:job.video.size_bytes,
        method:data.source === 'reused_existing' ? 'verified_existing_video' : 'created_from_local_upload'}});
    }
    if (job.stage === 'upload_snapshots') {
      const paths = {...(data.snapshot_paths || {})};
      const pending = job.project.players.find(player => !paths[player.slot]);
      if (!pending) return progress('setup');
      const snapshot = job.project.snapshots[pending.slot];
      if (!snapshot) throw new Error('本地球员截图缺失');
      const path = auth.userId + '/player-boxes/' + data.video_id + '/' + pending.slot + '-' +
        job.local_project_id + '-' + snapshot.id + '.jpg';
      const source = await asset(job, '/frame/' + encodeURIComponent(pending.slot));
      const frame = await source.arrayBuffer();
      const url = SUPA + '/storage/v1/object/videos/' + path.split('/').map(encodeURIComponent).join('/');
      const uploaded = await remoteFetch(url, {method:'POST',headers:{...supaHeaders(auth),'Content-Type':'image/jpeg',
        'x-upsert':'true','cache-control':'0'},body:frame,signal:AbortSignal.timeout(30000)});
      if (!uploaded.ok) throw new Error('GGAB 球员整帧截图保存失败：HTTP ' + uploaded.status);
      paths[pending.slot] = path;
      return progress('upload_snapshots', {snapshot_paths:paths});
    }
    if (job.stage === 'setup') {
      const profileIds = await playerProfiles(job, auth);
      const players = job.project.players.map(player => {
        const snapshot = job.project.snapshots[player.slot];
        return {label:player.slot,displayName:player.name,handedness:player.handedness,
          box:{xywh:originalBox(snapshot),frameTimeS:snapshot.time,imagePath:data.snapshot_paths[player.slot]}};
      });
      await ggab('/api/tracker/setup', {method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({videoId:data.video_id,gameType:job.project.game_type,nearTeam:'A',
          players,videoSegments:job.project.segments.length ? segments(job.project.segments) : undefined})});
      return progress('create_project', {profile_ids:profileIds});
    }
    if (job.stage === 'create_project') {
      const players = job.project.players.map(player => ({label:player.slot,displayName:player.name,
        handedness:player.handedness,...data.profile_ids[player.slot]}));
      const created = await ggab('/api/tracker/projects', {method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({videoId:data.video_id,title:job.project.title,projectKind:'user_submission',
          gameType:job.project.game_type,matchDate:job.project.match_date || null,
          setupJson:{nearTeam:'A',wizardCompletedAt:new Date().toISOString()},courtCalibrationId:null,players})});
      const remoteId = created.project?.id;
      if (!/^[0-9a-f-]{36}$/i.test(remoteId || '')) throw new Error('GGAB 创建响应没有明确项目 ID；停止重试');
      try {
        await local('projects/' + encodeURIComponent(job.local_project_id) + '/link', {
          remote_project_id:remoteId,remote_url:ORIGIN + '/tracker/' + remoteId,
          status:'review',source:data.source,evidence:data.remote_evidence});
      } catch (error) {
        error.remoteProjectId = remoteId;
        throw error;
      }
      return progress('estimate', {remote_project_id:remoteId});
    }
    if (job.stage === 'estimate') {
      const result = await ggab('/api/tracker/projects/' + data.remote_project_id + '/auto-track-estimate?mode=duration');
      if (!Number.isInteger(result.estimate) || result.estimate < 0) throw new Error('GGAB 未返回明确 Token 费用');
      return progress('await_confirmation', {estimated_cost:result.estimate,
        balance_before:Number.isInteger(result.balance) ? result.balance : null});
    }
    if (job.stage === 'confirm_tracking') {
      const latest = await ggab('/api/tracker/projects/' + data.remote_project_id + '/auto-track-estimate?mode=duration');
      if (!Number.isInteger(latest.estimate) || latest.estimate < 0)
        throw new Error('GGAB 未返回明确 Token 费用；自动跟踪未提交');
      if (latest.estimate !== data.estimated_cost)
        return progress('await_confirmation', {estimated_cost:latest.estimate,
          balance_before:Number.isInteger(latest.balance) ? latest.balance : null,
          estimate_changed_from:data.estimated_cost});
      if (!Number.isSafeInteger(latest.balance)) throw new Error('GGAB 未返回明确 Token 余额；自动跟踪未提交');
      if (latest.balance < latest.estimate)
        return progress('await_confirmation', {estimated_cost:latest.estimate,
          balance_before:Number.isSafeInteger(latest.balance)?latest.balance:null,
          insufficient_ggab_balance:true});
      const result = await ggab('/api/tracker/projects/' + data.remote_project_id + '/submit', {method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({source:'auto_track',mode:'duration'})});
      if (!result.jobId || !['pending','claimed','in_progress'].includes(result.status))
        throw new Error('GGAB 未确认自动跟踪排队；停止重试');
      return progress('processing', {remote_job_id:result.jobId});
    }
    throw new Error('未知迁移阶段：' + job.stage);
  }

  async function identity() {return (await session()).userId;}
  return {step, originalBox, segments, rememberSession, checkVideo, identity};
})();
