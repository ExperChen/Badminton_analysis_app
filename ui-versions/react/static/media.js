"use strict";

// Files are sent only to this local FastAPI service. Existing uploads survive reloads.
let pendingVideo = null, mediaBusy = false, clipPoll = null, planVersion = 0;
let serverVideoVersion = null, mediaSession = null, mediaPlan = null, mediaAvailable = true;
let mediaLoading = true;
const durationText = seconds => `${Math.floor(seconds/60)}分${(seconds%60).toFixed(1)}秒`;

function renderSessions() {
  $("sessions-total").textContent = `${matches.length} 场比赛 · ${matches.filter(m=>m.has_video).length} 已有视频`;
  $("session-cards").innerHTML=matches.map(m=>`<div><button style="width:100%" class="session-card ${m.match_id===$("match").value?"selected":""}" data-session="${esc(m.match_id)}"><strong>${esc(m.title)}</strong><span>${esc(m.date||"本地视频")} · ${m.rallies} 回合 · ${m.shots} 拍</span><small class="${m.has_video?"ready":"missing"}">${m.has_video?"● 视频已关联":"○ 待关联视频"}${m.tagged?"":" · 尚无时间标注"}</small></button>${m.can_delete?`<button data-delete-session="${esc(m.match_id)}" aria-label="删除视频 ${esc(m.title)}">删除视频</button>`:""}</div>`).join("");
}
async function deleteSession(sessionId) {
  if(mediaBusy)return;
  const session=matches.find(m=>m.match_id===sessionId);
  if(!session?.can_delete)return;
  const disposition=session.video?.storage==='google_drive'?'视频记录 将移除，视频存储 中的原视频会保留。':'未匹配的 视频记录 将移除，视频副本移入 回收目录。';
  if(!window.confirm(`删除“${session.title}”？\n${disposition}`))return;
  mediaBusy=true;enableMedia();
  const selected=$("match").value===sessionId;
  if(selected){clearTimeout(clipPoll);resetVideo();pendingVideo=null;serverVideoVersion=null;}
  try {
    const response=await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`,{method:"DELETE",headers:{"X-Yuxu-Local":"1"}});
    const result=await response.json();
    if(!response.ok)throw new Error(result.detail||"删除失败");
    await refreshSessions();
    if(matches.length)await load(selected);
    else{data=null;mediaPlan=null;$("content").hidden=true;if($("clip-panel"))$("clip-panel").hidden=true;$("status").textContent="暂无比赛，请添加视频。";}
    $("import-status").textContent=window.AppText.error(result.message);
  }catch(error){message("无法删除视频",error.message);if(selected)attachSavedVideo();}
  finally{mediaBusy=false;enableMedia();}
}
async function refreshSessions() {
  const mainId=$("match").value, comparison=$("compare-match").value;
  matches=await getJSON("/api/matches");
  const options=matches.map(m=>`<option value="${esc(m.match_id)}">${esc(m.title)}${m.date?" · "+esc(m.date):""}</option>`).join("");
  $("match").innerHTML=options;$("compare-match").innerHTML=options;
  const main=matches.find(m=>m.match_id===mainId)||matches.find(m=>m.local_session_id===mainId);
  const compared=matches.find(m=>m.match_id===comparison)||matches.find(m=>m.local_session_id===comparison);
  if(main)$("match").value=main.match_id;
  if(compared)$("compare-match").value=compared.match_id;
  $("match").disabled=!matches.length;
  $("library-count").textContent=matches.length;
  $("library-detail").textContent=`${matches.reduce((n,m)=>n+m.shots,0)} 拍 · ${matches.reduce((n,m)=>n+m.rallies,0)} 回合`;
  renderSessions();
}
function validPadding() {
  const before=Number($("clip-before").value), after=Number($("clip-after").value);
  if(!Number.isFinite(before)||!Number.isFinite(after)||before<0||before>5||after<0||after>5)throw new Error("回合前后保留时间应为 0–5 秒。");
  return {before,after};
}
function enableMedia() {
  if($("save-video"))$("save-video").disabled=mediaBusy||mediaLoading||!pendingVideo||pendingVideo.matchId!==$("match").value;
  if($("generate-clip"))$("generate-clip").disabled=mediaBusy||mediaLoading||!mediaPlan||!mediaAvailable||Boolean(pendingVideo);
  $("import-videos").disabled=mediaBusy;
}
async function updateClipPlan() {
  if(!$("clip-panel"))return;
  const token=++planVersion, session=$("match").value;
  mediaPlan=null;enableMedia();
  if(!mediaAvailable){$("clip-plan").textContent="剪辑服务不可用，暂时无法剪辑视频。";return;}
  if(pendingVideo?.matchId===session){$("clip-plan").textContent="已选择新视频。请先点击“保存所选视频到 视频记录”，再生成精剪。";return;}
  if(!data?.video){$("clip-plan").textContent="这场比赛还没有视频。在回放区选择视频并保存，或通过上方“添加视频”导入同名文件。";return;}
  try {
    const padding=validPadding(), params=new URLSearchParams(padding);
    if($("game").value)params.set("game",$("game").value);
    const plan=await getJSON(`/api/matches/${encodeURIComponent(session)}/clip-plan?${params}`);
    if(token!==planVersion||session!==$("match").value)return;
    mediaPlan=plan;
    $("clip-plan").textContent=`${$("game").value?"当前第 "+$("game").value+" 局":"全部局数"} · ${plan.rally_count} 个回合 → ${plan.segment_count} 段 · 保留 ${durationText(plan.kept_duration)} · 从原视频移除 ${durationText(plan.removed_duration)}`;
  }catch(error){if(token===planVersion)$("clip-plan").textContent=window.AppText.error(error.message);}
  enableMedia();
}
function attachSavedVideo() {
  if(!data?.video||pendingVideo?.matchId===data.match_id)return;
  const version=`${data.match_id}:${data.video.updated_at}`;
  if(version===serverVideoVersion&&videoMatch===data.match_id)return;
  if (videoURL && videoURL.startsWith("blob:")) resetVideo();
  else {
    const video=$("video"); video.pause(); video.removeAttribute("src"); video.load(); video.hidden=true; $("video-empty").hidden=false;
  }
  videoURL=`/api/matches/${encodeURIComponent(data.match_id)}/video?v=${encodeURIComponent(data.video.updated_at)}`;
  videoMatch=data.match_id;serverVideoVersion=version;
  const video=$("video");video.src=window.YuxuPath(videoURL);video.hidden=false;$("video-empty").hidden=true;
  $("video-name").textContent=data.video.filename;video.playbackRate=Number($("speed").value);video.load();
}
function uploadVideo(file, sessionId=null) {
  return window.YuxuUploadVideo(file, {sessionId, onProgress: event => {
    const percent=Math.round(event.loaded/event.total*100);
    $("upload-progress").value=event.phase==='complete'?100:Math.min(99,percent);
    const stage=event.phase==='checking'?'核对已上传片段':event.phase==='verifying'?'上传完成，正在验证视频':'正在上传';
    $("import-status").textContent=`${stage} ${file.name} · ${percent}%`;
  }});
}
async function importFiles(files, explicitId=null) {
  if(mediaBusy||!files.length)return;
  mediaBusy=true;enableMedia();$("upload-progress").hidden=false;
  const failures=[], successes=[];
  for(const file of files){
    $("upload-progress").value=0;
    try{successes.push(await uploadVideo(file,explicitId));}catch(error){failures.push(`${file.name}：${window.AppText.error(error.message)}`);}
  }
  mediaBusy=false;$("upload-progress").hidden=true;
  if(explicitId&&successes.length)pendingVideo=null;
  try{await refreshSessions();if(!data&&successes.length)$("match").value=successes[0].match_id;await load();}catch(error){failures.push(window.AppText.error(error.message));}
  const reused=successes.filter(item=>item.reused_local_file).length;
  const driveCount=successes.filter(item=>item.storage==='google_drive').length;
  $("import-status").textContent=`已关联 ${successes.length} 个视频，${driveCount} 个保存到视频存储，${reused} 个复用现有原片。${failures.length?" 失败："+failures.join("；"):"同名比赛已关联；其他视频已创建独立 视频记录。"}`;
  enableMedia();
}
function drawJobs(jobs) {
  $("clip-jobs").innerHTML=jobs.slice(0,5).map(job=>{
    const scope=job.game?`第 ${job.game} 局`:"全部局数";
    const title=`${scope} · ${job.plan.rally_count} 回合 · ${job.plan.score_overlay?.enabled?"含动态比分":"无比分旧版"} · ${new Date(job.created_at).toLocaleString()}`;
    if(job.status==="complete")return `<div class="clip-job"><div><strong>✓ 精剪完成</strong><small>${esc(title)} · ${durationText(job.output_duration)}</small></div><a class="button" href="${esc(window.YuxuPath(job.download_url))}">↓ 下载 视频</a><a class="timeline-link" href="${esc(window.YuxuPath(job.timeline_url))}">时间轴</a></div>`;
    if(job.status==="failed")return `<div class="clip-job failed"><div><strong>剪辑未完成</strong><small>${esc(title)}</small><p>${esc(window.AppText.error(job.error))}</p></div></div>`;
    return `<div class="clip-job"><div><strong>${job.status==="queued"?"等待剪辑":"正在剪辑"} · ${job.progress}%</strong><small>${esc(title)}</small><progress max="100" value="${job.progress}"></progress></div></div>`;
  }).join("");
}
async function refreshJobs() {
  if(!$("clip-panel"))return;
  clearTimeout(clipPoll);const session=$("match").value;
  if(!session)return;
  try {
    const jobs=await getJSON(`/api/matches/${encodeURIComponent(session)}/clips`);
    if(session!==$("match").value)return;
    drawJobs(jobs);
    if(jobs.some(j=>["running","queued"].includes(j.status)))clipPoll=setTimeout(refreshJobs,2000);
  }catch(error){if(session===$("match").value)$("clip-jobs").textContent=window.AppText.error(error.message);}
}
async function generateClip() {
  if(mediaBusy||!mediaPlan)return;
  const session=$("match").value;
  try {
    const settings={...validPadding(),game:$("game").value?Number($("game").value):null};
    mediaBusy=true;enableMedia();
    const response=await fetch(`/api/matches/${encodeURIComponent(session)}/clips`,{method:"POST",headers:{"Content-Type":"application/json","X-Yuxu-Local":"1"},body:JSON.stringify(settings)});
    const job=await response.json();if(!response.ok)throw new Error(typeof job.detail==="string"?job.detail:"剪辑请求失败，请检查设置。");
    if(session===$("match").value){drawJobs([job]);await refreshJobs();}
  }catch(error){message("无法生成精剪",error.message);}finally{mediaBusy=false;enableMedia();}
}
function mediaLoaded() {
  mediaLoading=false;
  const current=$("match").value;
  if(mediaSession!==current){pendingVideo=null;serverVideoVersion=null;mediaSession=current;}
  attachSavedVideo();renderSessions();updateClipPlan();refreshJobs();
}
$("import-videos").addEventListener("click",()=>$("session-files").click());
$("session-files").addEventListener("change",e=>{const files=[...e.target.files];e.target.value="";importFiles(files);});
if($("save-video"))$("save-video").addEventListener("click",()=>{if(pendingVideo)importFiles([pendingVideo.file],pendingVideo.matchId);});
if($("generate-clip"))$("generate-clip").addEventListener("click",generateClip);
if($("clip-before"))$("clip-before").addEventListener("input",updateClipPlan);if($("clip-after"))$("clip-after").addEventListener("input",updateClipPlan);
$("session-cards").addEventListener("click",e=>{const remove=e.target.closest("[data-delete-session]");if(remove){deleteSession(remove.dataset.deleteSession);return;}const b=e.target.closest("[data-session]");if(b){$("match").value=b.dataset.session;load(true);}});
document.addEventListener("yuxu:sessions-loaded",renderSessions);
document.addEventListener("yuxu:loading",()=>{mediaLoading=true;++planVersion;mediaPlan=null;enableMedia();});
document.addEventListener("yuxu:match-loaded",mediaLoaded);
document.addEventListener("yuxu:video-selected",event=>{pendingVideo=event.detail;serverVideoVersion=null;updateClipPlan();});
$("match").addEventListener("change",()=>{++planVersion;mediaPlan=null;enableMedia();});
$("game").addEventListener("change",()=>{++planVersion;mediaPlan=null;enableMedia();});
window.addEventListener("beforeunload",()=>clearTimeout(clipPoll));
if($("clip-panel"))getJSON("/api/video-tools").then(result=>{mediaAvailable=result.available;updateClipPlan();}).catch(()=>{});
if(matches.length)renderSessions();if(data)mediaLoaded();
