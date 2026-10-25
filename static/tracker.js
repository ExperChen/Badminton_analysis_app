"use strict";
const el=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clock=s=>{s=Math.max(0,Number(s)||0);return `${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toFixed(2).padStart(5,'0')}`;};
let draftKey="yuxu-tracker-draft-v2";
let library=[], projects=[], options=[], diskVideos=[], step=0, slot="A1", busy=false, frameReady=false, frameRequest=0, frameUrl=null, frameAbort=null, frameKey=null;
let state=fresh();
function fresh(){return {title:"",match_date:"",session_id:"",game_type:"singles",playing:true,players:{},frame:{time:0,rotation:0},segments:[],id:null};}
function slots(){return state.game_type==="singles"?["A1","B1"]:["A1","A2","B1","B2"];}
function player(s=slot){if(!state.players[s])state.players[s]={slot:s,name:"",handedness:"right",is_you:false,auto_me:false,time:0,rotation:0,box:null,snapshot:null,revision:0};return state.players[s];}
function sharedFrame(){if(!state.frame)state.frame={time:player('A1').time||0,rotation:player('A1').rotation||0};return state.frame;}
function sameFrame(p){const f=sharedFrame();return Math.abs(p.time-f.time)<.001&&p.rotation===f.rotation;}
function localVideoId(video){return video.local_session_id||video.video?.session_id||video.match_id;}
function selectedVideo(){return library.find(v=>v.has_video&&v.video&&(localVideoId(v)===state.session_id||v.match_id===state.session_id));}
function normalizeSelectedVideo(){const video=selectedVideo();if(video)state.session_id=localVideoId(video);}
function duration(){return selectedVideo()?.video?.duration||0;}
function persist(){try{localStorage.setItem(draftKey,JSON.stringify(state));}catch(_){el("notice").textContent="浏览器无法保存临时草稿，请完成后保存项目。";}}
function fail(e){el("failure").hidden=false;el("failure").textContent=window.AppText.error(typeof e==="string"?e:e.message);}
function clearError(){el("failure").hidden=true;}
async function api(url,body,method){const requestMethod=method||(body?"POST":"GET"),mutation=requestMethod!=="GET";const headers=mutation?{"X-Yuxu-Local":"1"}:{};if(body)headers["Content-Type"]="application/json";const response=await fetch(url,{cache:"no-store",method:requestMethod,headers,body:body?JSON.stringify(body):undefined});const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==="string"?result.detail:"填写内容无效，请检查名称、选框、分段和比分。");return result;}
function isOwnDefaultName(name){return name==="我"||options.some(item=>item.is_you&&item.name===name);}
function renderPlayerOptions(){el('player-options').innerHTML=[...new Set(options.map(p=>p.name))].map(n=>`<option value="${esc(n)}"></option>`).join('');}
function clearYouAssignments(){for(const s of slots()){const p=player(s),wasYou=p.is_you;p.is_you=false;if(wasYou&&(p.auto_me||isOwnDefaultName(p.name))){p.name="";p.auto_me=false;}}}
function preparePlayers(){for(const s of slots())player(s);if(!state.playing){clearYouAssignments();return;}if(!slots().some(s=>player(s).is_you)){const p=player("A1"),me=options.find(item=>item.is_you);p.is_you=true;if(!p.name){p.name=me?.name||"我";p.auto_me=true;}}}
function errors(which){const out=[];if(which===0){if(!state.title.trim())out.push("请输入项目名称。");if(!selectedVideo())out.push("请选择一个已保存的视频。");if(state.match_date&&!/^\d{4}-\d{2}-\d{2}$/.test(state.match_date))out.push("比赛日期无效。");}
if(which===2){for(const s of slots()){const p=player(s);if(!p.name.trim())out.push(`${s}：请输入球员名称。`);if(!p.snapshot||p.saving)out.push(`${s}：请完成框选并等待截图保存成功。`);}if(slots().filter(s=>player(s).is_you).length!==Number(state.playing))out.push("请为本人选择一个球员位置。");}
if(which===3){let previous=0;for(const [i,s]of [...state.segments].sort((a,b)=>a.start-b.start).entries()){if(!Number.isFinite(s.start)||!Number.isFinite(s.end)||s.start<previous||s.start<0||s.end<=s.start||s.end>duration())out.push(`片段 ${i+1}：时间需在视频范围内、结束晚于开始且片段不能重叠。`);previous=s.end;for(const key of ['a_start','b_start','a_end','b_end'])if(s[key]!=null&&(!Number.isInteger(s[key])||s[key]<0||s[key]>999))out.push("比分必须是 0–999 的整数。");for(const side of ['a','b'])if(s[side+'_start']!=null&&s[side+'_end']!=null&&s[side+'_end']<s[side+'_start'])out.push("同一片段的结束比分不能小于开始比分。");}}
return out;}
function showStep(index){clearError();step=index;el("wizard").hidden=false;el("project-library").hidden=true;el("heading").textContent=state.id?"编辑比赛项目":"新建比赛项目";el("steps").innerHTML=['选择视频','比赛类型','球员标注','比赛片段','确认项目'].map((name,i)=>`<button data-step-go="${i}" ${i===step?'aria-current="step"':''}>${i+1} · ${name}</button>`).join('');document.querySelectorAll('.step').forEach(n=>n.hidden=Number(n.dataset.step)!==step);el("back").disabled=step===0;el("next").textContent=step===4?(state.id?"保存修改":"保存项目"):"下一步 ›";el("next").disabled=busy;el("title").value=state.title;el("date").value=state.match_date;document.querySelectorAll('[data-game]').forEach(b=>b.classList.toggle('selected',b.dataset.game===state.game_type));if(step===0)renderVideos();if(step===2){preparePlayers();if(!slots().includes(slot))slot='A1';renderPlayer();}else{frameRequest++;frameAbort?.abort();frameReady=false;frameKey=null;}if(step===3){const v=el('segment-video');const url=window.YuxuPath(`/api/matches/${encodeURIComponent(state.session_id)}/video`);if(v.getAttribute('src')!==url)v.src=window.YuxuPath(url);renderSegments();}else el('segment-video').pause();if(step===4)renderReview();persist();}
function renderVideos(){const q=el('search').value.toLowerCase();const videos=[...new Map(library.filter(v=>v.has_video&&v.video&&v.title.toLowerCase().includes(q)).map(v=>[localVideoId(v),v])).values()];const selected=selectedVideo();el('videos').innerHTML=videos.length?videos.map(v=>`<button class="video-choice ${selected&&localVideoId(v)===localVideoId(selected)?'selected':''}" data-video="${esc(localVideoId(v))}"><span>▷ ${esc(v.title)}</span><small>${clock(v.video.duration)} · ${selected&&localVideoId(v)===localVideoId(selected)?'已选择':'可使用'}</small></button>`).join(''):'<p class="hint">没有匹配的已登记视频。</p>';const unlinked=diskVideos.filter(v=>v.filename.toLowerCase().includes(q)||v.relative_path.toLowerCase().includes(q));el('disk-videos').innerHTML=unlinked.length?unlinked.map(v=>`<button class="video-choice" type="button" data-disk-video="${esc(v.relative_path)}"><span>▷ ${esc(v.relative_path)}</span><small>直接关联 · ${(v.size_bytes/1048576).toFixed(1)} MiB</small></button>`).join(''):'<p class="hint">目录中没有尚未登记的原片。</p>';el('reclaim-video').disabled=busy||!selectedVideo()?.has_video;}
async function refreshDiskVideos(){const data=await api('/api/sessions/local-videos');diskVideos=data.videos;el('local-video-root').textContent=data.directory;renderVideos();}
function setVideo(id,alreadyConfirmed=false){const v=library.find(item=>item.has_video&&item.video&&(localVideoId(item)===id||item.match_id===id));if(!v){fail('本地原片不可用，请核对视频关联。');return;}const current=selectedVideo(),nextId=localVideoId(v),changing=(current?localVideoId(current):state.session_id)!==nextId;if(state.session_id&&changing&&slots().some(s=>player(s).snapshot)&&!alreadyConfirmed&&!confirm('更换视频会清除当前球员选框和比赛片段，继续吗？'))return;if(changing){state.players={};state.frame={time:0,rotation:0};state.segments=[];}state.session_id=nextId;if(changing){state.title=v.title;state.match_date=v.date||'';}preparePlayers();showStep(0);}
function renderPlayerTabs(){el('player-tabs').innerHTML=slots().map(s=>{const p=player(s);return `<button data-player="${s}" class="${slot===s?'selected':''}">${s} — ${esc(p.name||'待填写')} ${p.saving?'· 保存中…':p.snapshot?'· ✓':'· 待框选'}</button>`;}).join('');}
function renderPlayer(){const p=player(),f=sharedFrame();renderPlayerTabs();el('player-name').value=p.name;el('is-you').checked=p.is_you;el('is-you').disabled=!state.playing;document.querySelectorAll('[data-playing]').forEach(b=>b.classList.toggle('selected',b.dataset.playing===String(state.playing)));document.querySelectorAll('[data-hand]').forEach(b=>b.classList.toggle('selected',b.dataset.hand===p.handedness));const max=Math.max(0,duration()-.08);el('frame-time').max=max;el('frame-seconds').max=max;el('frame-time').value=f.time;el('frame-seconds').value=f.time;el('frame-clock').textContent=`${clock(f.time)} / ${clock(duration())}`;renderSnapshot();const key=`${state.session_id}:${f.time}:${f.rotation}`;if(frameKey!==key)loadFrame();else if(frameReady){drawBoxes();el('frame-status').textContent=`${slot}：在当前画面拖拽框选。${p.box&&!sameFrame(p)?'旧截图来自另一帧，重画后将替换。':''}`;}}
function drawBoxes(activeBox){const group=el('player-boxes'),ns='http://www.w3.org/2000/svg';group.replaceChildren();for(const s of slots()){const p=player(s),box=s===slot&&activeBox!==undefined?activeBox:sameFrame(p)?p.box:null;if(!box)continue;const g=document.createElementNS(ns,'g');g.classList.add('player-outline');if(s===slot)g.classList.add('is-active');g.dataset.slot=s;const rect=document.createElementNS(ns,'rect');for(const [key,value]of Object.entries(box))rect.setAttribute(key,value*1000);const label=document.createElementNS(ns,'text');label.setAttribute('x',Math.max(4,Math.min(960,box.x*1000+5)));label.setAttribute('y',Math.max(27,box.y*1000+26));label.textContent=s;g.append(rect,label);group.append(g);}}
function renderSnapshot(){const p=player();el('snapshot-preview').innerHTML=p.snapshot?`<img src="${window.YuxuPath(`/api/tracker/snapshots/${encodeURIComponent(p.snapshot.id)}`)}" alt="${esc(p.name)} 球员截图"><span>✓ 截图已保存 · ${clock(p.time)} · ${p.rotation}°</span>`:'';el('retry-snapshot').hidden=!p.box||!!p.snapshot||!!p.saving;}
async function loadFrame(){const f={...sharedFrame()},version=++frameRequest;frameKey=`${state.session_id}:${f.time}:${f.rotation}`;frameAbort?.abort();frameAbort=new AbortController();frameReady=false;drawBoxes();el('frame-image').style.opacity='.3';el('frame-status').textContent='正在读取共享视频帧…';try{const response=await fetch(`/api/tracker/frame/${encodeURIComponent(state.session_id)}?time=${f.time}&rotation=${f.rotation}`,{signal:frameAbort.signal,cache:'no-store'});if(!response.ok){const e=await response.json();throw new Error(e.detail||'读取视频帧失败');}const blob=await response.blob();if(version!==frameRequest)return;const url=URL.createObjectURL(blob);const img=el('frame-image');img.onload=()=>{if(version!==frameRequest)return;frameReady=true;img.style.opacity='1';drawBoxes();el('frame-status').textContent=`${slot}：在当前画面拖拽框选，其他球员的彩色框会保留。`;};if(frameUrl)URL.revokeObjectURL(frameUrl);frameUrl=url;img.src=window.YuxuPath(url);}catch(e){if(e.name!=='AbortError'&&version===frameRequest){frameKey=null;el('frame-status').textContent=e.message;fail(e);}}}
function invalidate(p){p.revision++;p.snapshot=null;p.box=null;p.saving=false;persist();}
async function saveSnapshot(p){
  const revision=p.revision;
  p.saving=true;renderPlayerTabs();renderSnapshot();
  el('frame-status').textContent='正在裁剪并保存球员截图…';
  try{
    const snap=await api('/api/tracker/snapshots',{session_id:state.session_id,time:p.time,rotation:p.rotation,box:p.box});
    if(p.revision!==revision)return;
    p.snapshot=snap;p.saving=false;persist();renderPlayerTabs();
    if(player()===p){renderSnapshot();el('frame-status').textContent='✓ 截图保存成功，可以重新拖拽绘框。';}
  }catch(e){
    if(p.revision!==revision)return;
    p.saving=false;renderPlayerTabs();
    if(player()===p){renderSnapshot();el('frame-status').textContent='截图保存失败，请点击重试。';fail(e);}
  }finally{
    // Review may have been opened while the snapshot request was still saving.
    if(step===4)renderReview();
  }
}
let drawing=null;
function point(e){const r=el('frame-overlay').getBoundingClientRect();return {x:Math.min(1,Math.max(0,(e.clientX-r.left)/r.width)),y:Math.min(1,Math.max(0,(e.clientY-r.top)/r.height))};}
el('frame-overlay').addEventListener('pointerdown',e=>{if(!frameReady||e.button!==0)return;e.preventDefault();const p=player();drawing={origin:point(e),p};el('frame-overlay').setPointerCapture(e.pointerId);});
el('frame-overlay').addEventListener('pointermove',e=>{if(!drawing)return;const a=drawing.origin,b=point(e);drawing.box={x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),width:Math.abs(a.x-b.x),height:Math.abs(a.y-b.y)};drawBoxes(drawing.box);});
el('frame-overlay').addEventListener('pointerup',()=>{if(!drawing)return;const {p,box}=drawing;drawing=null;if(!box||box.width<.005||box.height<.005){drawBoxes();return;}invalidate(p);const f=sharedFrame();p.time=f.time;p.rotation=f.rotation;p.box=box;drawBoxes();persist();saveSnapshot(p);});
el('frame-overlay').addEventListener('pointercancel',()=>{drawing=null;drawBoxes();});
function replaceSharedFrame(time,rotation){const f=sharedFrame();if(f.time===time&&f.rotation===rotation){renderPlayer();return;}if(slots().some(s=>player(s).box||player(s).snapshot)&&!confirm('重新选帧或旋转会清除所有球员的选框和截图，需要重新框选。继续吗？')){renderPlayer();return;}for(const s of slots()){const p=player(s);invalidate(p);p.time=time;p.rotation=rotation;}state.frame={time,rotation};drawBoxes();persist();renderPlayer();}
function changeTime(value){const t=Number(value);if(!Number.isFinite(t)||t<0||t>Math.max(0,duration()-.08)){fail('选帧时间超出视频范围。');renderPlayer();return;}replaceSharedFrame(t,sharedFrame().rotation);}
el('frame-time').addEventListener('input',e=>{el('frame-seconds').value=e.target.value;el('frame-clock').textContent=`${clock(e.target.value)} / ${clock(duration())}`;});
el('frame-time').addEventListener('change',e=>changeTime(e.target.value));el('frame-seconds').addEventListener('change',e=>changeTime(e.target.value));
el('rotate').onclick=()=>replaceSharedFrame(sharedFrame().time,(sharedFrame().rotation+90)%360);
el('clear-box').onclick=()=>{invalidate(player());drawBoxes();renderSnapshot();renderPlayerTabs();};el('retry-snapshot').onclick=()=>{if(player().box)saveSnapshot(player());};
el('player-name').oninput=e=>{player().name=e.target.value;player().auto_me=false;renderPlayerTabs();persist();};
el('player-name').onchange=e=>{const found=options.find(p=>p.name===e.target.value);if(found?.handedness){player().handedness=found.handedness;document.querySelectorAll('[data-hand]').forEach(b=>b.classList.toggle('selected',b.dataset.hand===player().handedness));persist();}};
el('is-you').onchange=e=>{slots().forEach(s=>player(s).is_you=false);player().is_you=e.target.checked;persist();};
el('player-tabs').onclick=e=>{const b=e.target.closest('[data-player]');if(b){slot=b.dataset.player;renderPlayer();}};
document.querySelectorAll('[data-hand]').forEach(b=>b.onclick=()=>{player().handedness=b.dataset.hand;persist();document.querySelectorAll('[data-hand]').forEach(x=>x.classList.toggle('selected',x===b));});
document.querySelectorAll('[data-playing]').forEach(b=>b.onclick=()=>{state.playing=b.dataset.playing==='true';preparePlayers();renderPlayer();persist();});
document.querySelectorAll('[data-game]').forEach(b=>b.onclick=()=>{state.game_type=b.dataset.game;preparePlayers();if(state.playing&&slots().filter(s=>player(s).is_you).length!==1){slots().forEach(s=>player(s).is_you=s==='A1');}showStep(1);});
function renderSegments(){el('segments').innerHTML=state.segments.map((s,i)=>`<div class="segment-row"><div class="segment-head"><strong>SEGMENT ${i+1}</strong><button data-remove="${i}">移除片段</button></div><div class="segment-inputs">${[['start','开始（秒）'],['end','结束（秒）']].map(([key,label])=>`<div><label>${label}<input type="number" min="0" max="${duration()}" step="0.01" value="${s[key]}" data-index="${i}" data-key="${key}" aria-label="片段 ${i+1} ${label}"></label></div>`).join('')}<div><label>FIRST SERVER</label><select data-index="${i}" data-key="first_server" aria-label="片段 ${i+1} 先发球"><option value="near" ${s.first_server==='near'?'selected':''}>Near · 近端</option><option value="far" ${s.first_server==='far'?'selected':''}>Far · 远端</option></select></div><div><label>时长</label><p>${clock(s.end-s.start)}</p></div></div><details ${['a_start','b_start','a_end','b_end'].some(k=>s[k]!=null)?'open':''}><summary>起止比分（可选）</summary><div class="scores">${[['a_start','A 开始'],['b_start','B 开始'],['a_end','A 结束'],['b_end','B 结束']].map(([key,label])=>`<label>${label}<input type="number" min="0" max="999" step="1" value="${s[key]??''}" data-index="${i}" data-key="${key}" aria-label="片段 ${i+1} ${label}"></label>`).join('')}</div></details></div>`).join('')||'<p class="hint">未添加片段：将使用完整视频。</p>';renderTrack();}
function renderTrack(){el('segment-track').innerHTML=state.segments.map((s,i)=>`<div class="track-segment" data-track-index="${i}" style="left:${s.start/duration()*100}%;width:${(s.end-s.start)/duration()*100}%" title="片段 ${i+1}：${clock(s.start)} – ${clock(s.end)}"><i data-edge="start"></i><span>${i+1}</span><i data-edge="end"></i></div>`).join('');}
el('segments').onchange=e=>{const {index,key}=e.target.dataset;if(index===undefined)return;state.segments[index][key]=key==='first_server'?e.target.value:e.target.value===''?null:Number(e.target.value);persist();renderTrack();if(key==='start'||key==='end'){const s=state.segments[index];e.target.closest('.segment-row').querySelector('.segment-inputs p').textContent=clock(s.end-s.start);}};
el('segments').onclick=e=>{const b=e.target.closest('[data-remove]');if(b){state.segments.splice(Number(b.dataset.remove),1);persist();renderSegments();}};
el('add-segment').onclick=()=>{const sorted=[...state.segments].sort((a,b)=>a.start-b.start);let start=0,end=duration();for(const s of sorted){if(s.start>start+.1){end=s.start;break;}start=Math.max(start,s.end);}if(end-start<.1){fail('视频已被片段覆盖，请先缩短或移除一个片段。');return;}state.segments.push({start,end,first_server:'near',a_start:null,b_start:null,a_end:null,b_end:null});persist();renderSegments();};
let segmentDrag=null;
el('segment-track').onpointerdown=e=>{if(e.button!==0)return;const r=el('segment-track').getBoundingClientRect(),b=e.target.closest('[data-track-index]');if(!b){el('segment-video').currentTime=Math.min(duration()-.05,Math.max(0,(e.clientX-r.left)/r.width*duration()));return;}const index=Number(b.dataset.trackIndex);segmentDrag={index,edge:e.target.dataset.edge,x:e.clientX,start:state.segments[index].start,end:state.segments[index].end,width:r.width};el('segment-track').setPointerCapture(e.pointerId);e.preventDefault();};
el('segment-track').onpointermove=e=>{if(!segmentDrag)return;const d=segmentDrag,s=state.segments[d.index],delta=(e.clientX-d.x)/d.width*duration();if(d.edge==='start')s.start=Math.min(d.end-.1,Math.max(0,d.start+delta));else if(d.edge==='end')s.end=Math.max(d.start+.1,Math.min(duration(),d.end+delta));else{const offset=Math.max(-d.start,Math.min(duration()-d.end,delta));s.start=d.start+offset;s.end=d.end+offset;}s.start=Math.round(s.start*100)/100;s.end=Math.min(duration(),Math.round(s.end*100)/100);renderTrack();};
el('segment-track').onpointerup=()=>{if(segmentDrag){segmentDrag=null;persist();renderSegments();}};el('segment-track').onpointercancel=()=>{segmentDrag=null;renderSegments();};
function renderReview(){const issues=[0,2,3].flatMap(errors);el('review').innerHTML=(issues.length?`<div class="hint" style="color:#ff8fa4">${issues.map(esc).join('<br>')}</div>`:'')+[['项目',state.title],['比赛日期',state.match_date||'未填写'],['视频',selectedVideo()?.title||'视频不可用'],['比赛类型',state.game_type==='singles'?'单打':'双打']].map(([k,v])=>`<div class="review-line"><span>${k}</span><strong>${esc(v)}</strong></div>`).join('')+`<div class="review-players">${slots().map(s=>{const p=player(s);return `<div class="review-player">${p.snapshot?`<img alt="${esc(p.name)} 截图" src="${window.YuxuPath(`/api/tracker/snapshots/${encodeURIComponent(p.snapshot.id)}`)}">`:''}<div>${s} — ${esc(p.name)}<small>${p.is_you?'本人 · ':''}${p.handedness==='right'?'右手':'左手'} · ${clock(p.time)}</small></div></div>`;}).join('')}</div>`+`<div class="review-line"><span>比赛片段</span><div>${state.segments.length?state.segments.map((s,i)=>`${i+1}. ${clock(s.start)}–${clock(s.end)} · ${s.first_server==='near'?'近端':'远端'}先发球 · 比分 ${s.a_start??'—'}:${s.b_start??'—'} → ${s.a_end??'—'}:${s.b_end??'—'}`).join('<br>'):'完整视频 · 近端先发球'}</div></div>`;el('next').disabled=busy||issues.length>0;}
async function saveProject(){const issues=[0,2,3].flatMap(errors);if(issues.length){fail(issues.join('\n'));return;}busy=true;el('next').disabled=true;try{const body={title:state.title,match_date:state.match_date||null,session_id:state.session_id,game_type:state.game_type,playing:state.playing,players:slots().map(s=>{const p=player(s);return {slot:s,name:p.name,handedness:p.handedness,is_you:p.is_you,snapshot_id:p.snapshot.id};}),segments:state.segments};const saved=await api('/api/tracker/projects'+(state.id?'/'+state.id:''),body,state.id?'PUT':'POST');state.id=saved.id;persist();el('notice').textContent='✓ 项目和球员截图已保存，可以随时重新打开编辑。';[projects,options]=await Promise.all([api('/api/tracker/projects'),api('/api/tracker/player-options')]);renderPlayerOptions();showLibrary();history.replaceState(null,'','/tracker');}catch(e){fail(e);}finally{busy=false;el('next').disabled=false;}}
function localProjectUrl(id){return window.YuxuPath('/tracker/projects/'+encodeURIComponent(id));}
function showLibrary(){
  el('wizard').hidden=true;el('project-library').hidden=false;el('heading').textContent='比赛项目';
  el('segment-video').pause();frameAbort?.abort();
  el('project-list').innerHTML=projects.length?projects.map(p=>`<article class="project-item"><div class="project-copy"><div class="project-title-row"><strong>${esc(p.title)}</strong><span class="project-status">${p.analysis_match_id?'已有复盘数据':'设置已保存'}</span></div><small>${esc(p.match_date||'未填写日期')} · ${p.game_type==='singles'?'单打':'双打'} · ${p.players.length} 名球员</small></div><div class="toolbar project-actions"><a class="button" href="${localProjectUrl(p.id)}" data-edit="${esc(p.id)}">编辑项目</a>${p.analysis_match_id?`<a class="primary project-open" href="${window.YuxuPath('/insights/match-breakdown?match='+encodeURIComponent(p.analysis_match_id))}">打开复盘 →</a>`:''}<button class="delete-project" data-delete-project="${esc(p.id)}" aria-label="删除项目 ${esc(p.title)}">🗑️</button></div></article>`).join(''):'<p class="hint">还没有项目。点击“新建项目”开始。</p>';
}
async function editProject(id){
  try{
    const p=await api('/api/tracker/projects/'+encodeURIComponent(id));state={...p,match_date:p.match_date||'',players:{}};
    normalizeSelectedVideo();
    for(const item of p.players){const snap=p.snapshots[item.slot];state.players[item.slot]={...item,time:snap.time,rotation:snap.rotation,box:snap.box,snapshot:snap,revision:0};}
    slot='A1';showStep(0);history.replaceState(null,'',window.YuxuPath('/tracker/new?project='+encodeURIComponent(id)));
  }catch(e){fail(e);}
}
el('new-project').onclick=()=>{state=fresh();slot='A1';preparePlayers();showStep(0);history.replaceState(null,'',window.YuxuPath('/tracker/new'));el('notice').textContent='';};
el('project-list').onclick=async e=>{
  const edit=e.target.closest('[data-edit]'),del=e.target.closest('[data-delete-project]');
  if(edit){e.preventDefault();await editProject(edit.dataset.edit);return;}
  if(!del)return;
  const p=projects.find(item=>item.id===del.dataset.deleteProject);
  if(!p||!confirm(`删除项目“${p.title}”？原视频保留，项目设置可从备份恢复。`))return;
  del.disabled=true;
  try{await api('/api/tracker/projects/'+encodeURIComponent(p.id),null,'DELETE');projects=projects.filter(item=>item.id!==p.id);showLibrary();}
  catch(error){del.disabled=false;fail(error);}
};
el('videos').onclick=e=>{const b=e.target.closest('[data-video]');if(b)setVideo(b.dataset.video);};el('search').oninput=renderVideos;
el('disk-videos').onclick=async e=>{const b=e.target.closest('[data-disk-video]');if(!b||busy)return;const hasSnapshots=state.session_id&&slots().some(s=>player(s).snapshot);if(hasSnapshots&&!confirm('更换视频会清除当前球员选框和比赛片段，继续吗？'))return;busy=true;b.disabled=true;el('next').disabled=true;clearError();try{const result=await api('/api/sessions/link',{relative_path:b.dataset.diskVideo});library=await api('/api/matches');await refreshDiskVideos();setVideo(result.match_id,Boolean(hasSnapshots));el('notice').textContent='✓ 已直接关联原片，未复制或新建本地视频文件。';}catch(error){fail(error);}finally{busy=false;b.disabled=false;el('next').disabled=false;}};
el('title').oninput=e=>{state.title=e.target.value;persist();};
function updateMatchDate(input){const extended=/^(\d{4})\d+-(\d{2})-(\d{2})$/.exec(input.value);if(extended)input.value=`${extended[1]}-${extended[2]}-${extended[3]}`;const valid=!input.value||(/^\d{4}-\d{2}-\d{2}$/.test(input.value)&&input.validity.valid);input.setCustomValidity(valid?'':'年份必须为 4 位数。');if(valid){state.match_date=input.value;persist();}}
el('date').oninput=e=>updateMatchDate(e.target);el('date').onchange=e=>updateMatchDate(e.target);
el('next').onclick=()=>{if(busy)return;const issues=errors(step);if(issues.length){fail(issues.join('\n'));return;}if(step===4)saveProject();else{showStep(step+1);el('steps').scrollIntoView({block:'start',behavior:'smooth'});}};
el('back').onclick=()=>{if(!busy)showStep(Math.max(0,step-1));};
el('steps').onclick=e=>{const b=e.target.closest('[data-step-go]');if(!b||busy)return;const target=Number(b.dataset.stepGo);if(target>0&&errors(0).length){fail(errors(0).join('\n'));return;}showStep(target);};
el('library-mode').onclick=()=>{el('upload-area').hidden=true;};el('upload-mode').onclick=()=>{el('upload-area').hidden=false;};
el('upload').onchange=async()=>{
  const file=el('upload').files[0];if(!file||busy)return;
  const hasSnapshots=state.session_id&&slots().some(s=>player(s).snapshot);
  if(hasSnapshots&&!confirm('更换视频会清除当前球员选框和比赛片段，继续吗？')){el('upload').value='';return;}
  busy=true;el('next').disabled=true;el('new-project').disabled=true;el('upload').disabled=true;el('upload-progress').hidden=false;
  try{
    const r=await window.YuxuUploadVideo(file,{onProgress:event=>{
      const percent=Math.round(event.loaded/event.total*100);
      el('upload-progress').value=event.phase==='complete'?100:Math.min(99,percent);
      const stage=event.phase==='checking'?'核对已上传片段':event.phase==='verifying'?'上传完成，正在验证视频':'正在上传';
      el('notice').textContent=stage+' '+percent+'%';
    }});
    library=await api('/api/matches');setVideo(r.match_id,Boolean(hasSnapshots));
    el('notice').textContent=r.storage==='google_drive'?'✓ 视频已保存到管理员 Drive 并关联当前账号。':r.reused_local_file?'✓ 已关联现有原片，未新建本地视频文件。':'✓ 原片已保存并选中。';
  }catch(e){fail(e);}finally{busy=false;el('next').disabled=false;el('new-project').disabled=false;el('upload').disabled=false;el('upload').value='';el('upload-progress').hidden=true;}
};
el('nav-toggle').onclick=()=>document.body.classList.toggle('nav-collapsed');
window.addEventListener('popstate',()=>location.reload());
async function init(){
  try{
    const account=await window.YuxuAuthReady;draftKey='yuxu-tracker-draft-v3:'+account.id;
    [library,projects,options]=await Promise.all([api('/api/matches'),api('/api/tracker/projects'),api('/api/tracker/player-options')]);
    await refreshDiskVideos();renderPlayerOptions();
    const routeProject=/^\/tracker\/projects\/([^/]+)$/.exec(window.YuxuRoute());
    const id=routeProject?decodeURIComponent(routeProject[1]):new URLSearchParams(location.search).get('project');
    if(id){await editProject(id);return;}
    if(window.YuxuRoute().endsWith('/new')){
      try{const draft=JSON.parse(localStorage.getItem(draftKey));if(draft&&typeof draft.players==='object'&&Array.isArray(draft.segments)){state=draft;Object.values(state.players).forEach(p=>p.saving=false);el('notice').textContent='已恢复上次填写的项目。';}}catch(_){}
      normalizeSelectedVideo();preparePlayers();showStep(0);
    }else showLibrary();
  }catch(e){fail(e);}
}
init();
