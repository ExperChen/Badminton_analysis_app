"use strict";

// Browser code only handles presentation. Statistical calculations live in Python.
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "—").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const fmt = (value, suffix="") => value == null ? "—" : `${value}${suffix}`;
const time = (seconds) => seconds == null ? "—" : `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,"0")}`;
const labels = Object.fromEntries(["above_tape","above_net_midline","below_net_midline","below_net","into_the_net","out_the_back","out_on_sides","unknown","winner","forced_error","unforced_error","in_play"].map(key=>[key,window.AppText.term(key)]));
const zh=value=>window.AppText.term(value);
const colors = ["#ff2344", "#38bdf8", "#e7a648", "#ac85ed", "#61c9a4", "#85858e"];
const heightKeys = ["above_tape", "above_net_midline", "below_net_midline", "below_net"];
const errorKeys = ["into_the_net", "out_the_back", "out_on_sides", "unknown"];
let matches = [], data = null, rallyIndex = 0, mode = "single", requestId = 0;
let activeRequest = null, videoURL = null, videoMatch = null, selectedShot = null;
const charts = new Map();
const patternState = {3:{mode:"all",limit:5},5:{mode:"all",limit:5}};

function message(title, body, html=false) {
  $("dialog-title").textContent = title;
  $("dialog-body")[html ? "innerHTML" : "textContent"] = html ? body : window.AppText.error(body);
  if (!$("dialog").open) $("dialog").showModal();
}
function help() {
  message("比赛分析 · 使用说明", "<p>选择比赛、局数和观察方后，图表将同步更新。</p><p>赢分技术分布表示赢下回合时最后一拍的技术占比，并非使用该技术后的条件胜率。失分技术分布按输分回合最后一拍归类。</p><p>三拍和五拍组合统计每个回合开局的连续击球。回合数、胜率和时长按回合统计，未知值不会自动算作零。</p><p>关联原视频后，点击逐拍记录可定位回放。视频剪辑按回合起止时间保留有效比赛，可设置前后保留时长。</p><p>对比模式可比较两场完整比赛。抹扑和扑球按网带以上计入，其余缺失触球高度不推测。</p>", true);
}
async function getJSON(url, signal) {
  const response = await fetch(url, {signal, cache:"no-store"});
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? window.AppText.error(body.detail) : `请求失败（${response.status}）`);
  }
  return response.json();
}
function chart(id, option, onClick) {
  const node = $(id);
  if (charts.has(id)) { charts.get(id).dispose(); charts.delete(id); }
  node.classList.remove("empty-chart");
  node.replaceChildren();
  if (!window.echarts) throw new Error("图表暂时无法加载，请刷新页面重试。");
  const instance = echarts.init(node, null, {renderer:"svg"});
  charts.set(id, instance);
  const description = (option.series||[]).map(series=>`${series.name||"数据"}：${(series.data||[]).map((item,i)=>`${option.xAxis?.data?.[i]??item?.name??i+1} ${fmt(typeof item==="object"&&item!==null?item.value:item)}`).join("，")}`).join("；");
  instance.setOption({animation:!matchMedia("(prefers-reduced-motion: reduce)").matches,
    color:colors, textStyle:{fontFamily:"Poppins, Microsoft YaHei, sans-serif",color:"#a3a3ac",fontSize:11},
    aria:{enabled:true,label:{description}}, ...option});
  if (onClick) instance.on("click", onClick);
}
function emptyChart(id, text="暂无符合条件的数据") {
  if (charts.has(id)) { charts.get(id).dispose(); charts.delete(id); }
  $(id).replaceChildren(); $(id).classList.add("empty-chart"); $(id).textContent=text;
}
const axis = {axisLine:{lineStyle:{color:"#27272d"}},axisTick:{show:false},axisLabel:{color:"#92929b",fontSize:10},splitLine:{lineStyle:{color:"#1d1d22"}}};
const tooltip = {trigger:"axis",renderMode:"richText",backgroundColor:"#202024",borderColor:"#414149",textStyle:{color:"#eee",fontSize:12},confine:true};

function drawProgression() {
  if (!data.rallies.length) return emptyChart("progression");
  const names = ["A","B"].map(team => data.players.filter(p=>p.id?.startsWith(team)).map(p=>p.name).join(" / ") || team);
  const games = data.game_scores;
  const x = [], a = [], b = [], indexes=[];
  for (const game of games) {
    const rallies = data.rallies.filter(r=>r.game===game.number);
    if (x.length) {x.push("");a.push(null);b.push(null);indexes.push(null);}
    x.push(games.length>1 ? `第${game.number}局 · 0` : "开局");
    a.push(rallies[0].score_before[0]); b.push(rallies[0].score_before[1]); indexes.push(null);
    rallies.forEach(r=>{x.push(games.length>1?`第${r.game}局 第${r.number}回合`:`第${r.number}回合`);a.push(r.score[0]);b.push(r.score[1]);indexes.push(data.rallies.indexOf(r));});
  }
  chart("progression", {tooltip,legend:{top:9,left:15,textStyle:{color:"#ddd",fontSize:10},icon:"roundRect",itemWidth:12,itemHeight:7},grid:{left:42,right:20,top:50,bottom:36},xAxis:{type:"category",data:x,...axis,boundaryGap:false},yAxis:{type:"value",...axis,minInterval:1},series:[{name:names[0],type:"line",data:a,symbolSize:5,showSymbol:false,lineStyle:{width:2},connectNulls:false},{name:names[1],type:"line",data:b,symbolSize:5,showSymbol:false,lineStyle:{width:2},connectNulls:false}]}, p=>{if(indexes[p.dataIndex]!=null) jumpRally(indexes[p.dataIndex]);});
}
function drawBars(id, items) {
  if (!items.length) return emptyChart(id);
  const cats = [...new Set(items.flatMap(x=>Object.keys(x.detail||{})))];
  const total = items.reduce((n,x)=>n+x.value,0);
  const series = cats.length ? cats.map((name,i)=>({name:zh(name),type:"bar",stack:"points",barMaxWidth:30,itemStyle:{color:colors[i]},data:items.map(x=>Number(((x.detail[name]||0)*100/total).toFixed(1)))})) : [{type:"bar",name:"占比",barMaxWidth:30,itemStyle:{color:colors[0],borderRadius:[3,3,0,0]},data:items.map(x=>x.percent)}];
  chart(id,{tooltip:{...tooltip,formatter:params=>{const item=items[params[0].dataIndex];return `${zh(item.name)}   ${item.percent}%\n${item.value} ${id==="usage"?"拍":"分"}${item.detail?"\n"+Object.entries(item.detail).map(([k,v])=>`${zh(k)}: ${v}`).join("\n"):""}`;}},grid:{left:43,right:14,top:26,bottom:48},xAxis:{type:"category",data:items.map(x=>zh(x.name)),...axis,axisLabel:{...axis.axisLabel,rotate:items.length>5?35:0,interval:0}},yAxis:{type:"value",...axis,axisLabel:{...axis.axisLabel,formatter:"{value}%"}},series});
}
function drawOutcomes() {
  if(!data.outcomes.length) return emptyChart("outcomes","所选范围内没有已标注的赢分回合");
  chart("outcomes",{tooltip:{trigger:"item",renderMode:"richText",formatter:"{b}\n{c} 个回合 · {d}%"},legend:{bottom:10,left:"center",textStyle:{color:"#999",fontSize:10}},title:{text:String(data.summary.wins),subtext:"赢分回合",left:"center",top:"33%",textStyle:{color:"#eee",fontSize:30,fontWeight:500},subtextStyle:{color:"#777",fontSize:10}},series:[{type:"pie",radius:["47%","66%"],center:["50%","45%"],label:{show:false},itemStyle:{borderColor:"#0c0c0d",borderWidth:4},data:data.outcomes.map(x=>({name:labels[x.name]||x.name,value:x.value}))}]});
}
function drawLength() {
  $("length-meta").textContent=`平均拍数 ${fmt(data.summary.avg_shots)} · 平均时长 ${fmt(data.summary.avg_duration,"s")}`;
  if(!data.rallies.length) return emptyChart("length-chart");
  chart("length-chart",{tooltip,grid:{left:38,right:18,top:25,bottom:35},xAxis:{type:"category",data:data.rallies.map(r=>`第${r.game}局 第${r.number}回合`),...axis},yAxis:{type:"value",...axis,minInterval:1},series:[{name:"拍数",type:"bar",barMaxWidth:22,data:data.rallies.map(r=>({value:r.length,itemStyle:{color:r.won===true?colors[0]:r.won===false?"#464650":"#777"}}))}]},p=>jumpRally(p.dataIndex));
}
function resetVideo() {
  const video=$("video"); video.pause(); video.removeAttribute("src"); video.load(); video.hidden=true;
  if(videoURL) URL.revokeObjectURL(videoURL);
  videoURL=null;videoMatch=null;$("video-file").value="";$("video-empty").hidden=false;$("video-name").textContent="尚未选择视频";
}
function renderRally() {
  const r=data?.rallies[rallyIndex];
  $("prev-rally").disabled=!r||rallyIndex===0;
  $("next-rally").disabled=!r||rallyIndex>=data.rallies.length-1;
  $("rally-slider").max=Math.max(0,(data?.rallies.length||0)-1);$("rally-slider").value=rallyIndex;$("rally-slider").disabled=!r;
  $("rally-position").textContent=r?`回合 ${rallyIndex+1} / ${data.rallies.length}`:"暂无回合";
  $("rally-title").textContent=r?`第 ${r.game} 局 · 第 ${r.number} 回合`:"暂无回合";
  $("rally-info").textContent=r?`${r.length} 拍 · ${r.score_before.map(x=>fmt(x)).join("–")}`:"";
  $("shot-rows").innerHTML=r?r.shots.map(s=>`<tr data-shot="${esc(s.shot_id)}" class="${s.shot_id===selectedShot?"active":""}"><td><button data-jump-shot="${esc(s.shot_id)}" aria-label="跳到 ${esc(time(s.video_timestamp_seconds))}">${time(s.video_timestamp_seconds)}</button></td><td>${esc(zh(s.shot_variant||s.shot_family))}</td><td class="${s.striker_is_you===1?"you":"opp"}">${s.striker_is_you===1?"观察方":"另一方"}</td><td class="${esc(s.outcome)}">${esc(labels[s.outcome]||s.outcome)}</td></tr>`).join(""):"<tr><td colspan='4'>暂无数据</td></tr>";
  $("rally-dots").innerHTML=(data?.rallies||[]).map((r,i)=>`<button class="${r.won?"won ":""}${i===rallyIndex?"current":""}" data-rally="${i}" aria-label="第 ${r.game} 局，第 ${r.number} 回合" aria-pressed="${i===rallyIndex}"></button>`).join("");
}
function seek(seconds) {
  if(!videoURL||seconds==null) return;
  const video=$("video");
  if(Number.isFinite(video.duration) && seconds>video.duration){message("视频时间超出范围","请选择与本场标注对应、未经裁剪的视频，当前时间戳超出了视频时长。");return;}
  if(video.readyState>=1) video.currentTime=Math.max(0,seconds);
  else video.addEventListener("loadedmetadata",()=>{video.currentTime=Math.min(seconds,video.duration||seconds);},{once:true});
}
function jumpRally(index, navigate=true) {
  if(!data||!Number.isInteger(index)||index<0||index>=data.rallies.length) return;
  rallyIndex=index;selectedShot=null;renderRally();
  seek(data.rallies[index].start??data.rallies[index].shots[0]?.video_timestamp_seconds);
  if(navigate) $("replay").scrollIntoView({block:"start",behavior:"smooth"});
}
function jumpShot(shotId) {
  const index=data?.rallies.findIndex(r=>r.shots.some(s=>s.shot_id===shotId));
  if(index==null||index<0) return;
  rallyIndex=index;selectedShot=shotId;renderRally();
  const shot=data.rallies[index].shots.find(s=>s.shot_id===shotId);seek(shot.video_timestamp_seconds);
  if(!videoURL) $("video-name").textContent=`已选 ${time(shot.video_timestamp_seconds)} · 选择视频后可定位`;
}
function renderPatterns(size) {
  const panel=document.querySelector(`[data-pattern="${size}"]`), state=patternState[size];
  const result=data.patterns[size][state.mode];
  panel.querySelectorAll("[data-pattern-mode]").forEach(b=>{b.classList.toggle("selected",b.dataset.patternMode===state.mode);b.setAttribute("aria-pressed",b.dataset.patternMode===state.mode);});
  panel.querySelector(".pattern-list").innerHTML=result.items.length?result.items.slice(0,state.limit).map(x=>`<li><div class="pattern-sequence">${x.sequence.map(value=>esc(window.AppText.sequence(value))).join(" <em>→</em> ")}</div><small>出现 <b>${x.count} 次</b>，共 ${result.eligible} 个回合</small></li>`).join(""):"<li>暂无达到所需拍数的回合</li>";
  panel.querySelector(".show-more").hidden=result.items.length<=state.limit;
}
function facetFilters(id) {
  const categories={striking_side:"击球侧",direction:"击球线路",shot_variant:"击球变体"};
  $(id).innerHTML=Object.entries(categories).map(([key,name])=>`<div class="facet-group"><span>${name}</span>${data.facets[key].map(value=>`<label><input type="checkbox" data-field="${key}" value="${esc(value)}">${esc(zh(value))}</label>`).join("")}</div>`).join("");
}
function filtered(records,id) {
  const keymap={striking_side:"side",direction:"direction",shot_variant:"variant"};
  const chosen={};$(id).querySelectorAll("input:checked").forEach(input=>(chosen[keymap[input.dataset.field]]??=[]).push(input.value));
  return records.filter(r=>Object.entries(chosen).every(([key,values])=>values.includes(r[key])));
}
function drilldown(records,title) {
  message(title,`<p>${records.length} 条标注 · 点击时间定位回合</p><div class="table-wrap"><table class="shot-table"><thead><tr><th>时间</th><th>球员</th><th>技术</th><th>标注</th></tr></thead><tbody>${records.map(r=>`<tr><td><button data-detail-shot="${esc(r.shot_id)}">${time(r.time)}</button></td><td>${esc(r.player)}</td><td>${esc(zh(r.family))}</td><td>${esc(labels[r.category]||r.category)}</td></tr>`).join("")}</tbody></table></div>`,true);
}
function stacked(id,records,keys) {
  if(!records.length) return emptyChart(id);
  const players=[...new Set(data.players.map(p=>p.name))];
  const families=[...new Set(records.map(r=>r.family))];
  const groups=families.flatMap(family=>players.map(player=>({family,player})));
  const series=keys.map((key,index)=>({name:labels[key],type:"bar",stack:"tags",barMaxWidth:35,itemStyle:{color:colors[index]},data:groups.map(g=>records.filter(r=>r.player===g.player&&r.family===g.family&&r.category===key).length)}));
  chart(id,{tooltip:{...tooltip,formatter:params=>{const g=groups[params[0].dataIndex];return `${g.player} · ${zh(g.family)}\n`+params.filter(p=>p.value).map(p=>`${p.seriesName}: ${p.value}`).join("\n");}},legend:{top:6,textStyle:{color:"#aaa",fontSize:10},itemWidth:10,itemHeight:10},grid:{left:42,right:18,top:52,bottom:groups.length>10?85:60},xAxis:{type:"category",data:groups.map(g=>`${zh(g.family)}\n${g.player}`),...axis,axisLabel:{...axis.axisLabel,interval:0,fontSize:9,rotate:groups.length>10?35:0,formatter:value=>value.length>28?value.slice(0,27)+"…":value}},yAxis:{type:"value",...axis,minInterval:1},dataZoom:groups.length>16?[{type:"inside",start:0,end:60}]:[],series},p=>{const g=groups[p.dataIndex];drilldown(records.filter(r=>r.player===g.player&&r.family===g.family),`${g.player} · ${zh(g.family)}`);});
}
function drawContact() {
  const heights=filtered(data.heights,"contact-filters"), replies=filtered(data.replies,"contact-filters");
  stacked("height-chart",heights,heightKeys);stacked("reply-chart",replies,heightKeys);
  $("height-overall").innerHTML=[...new Set(data.players.map(p=>p.name))].map(player=>{
    const records=heights.filter(r=>r.player===player),total=records.length;
    const counts=heightKeys.map(key=>records.filter(r=>r.category===key).length);
    return `<div class="height-card"><h4>${esc(player)} <span class="pill">${total} 拍</span></h4><div class="height-strip">${counts.map((n,i)=>n?`<span style="width:${n/total*100}%;background:${colors[i]}" title="${labels[heightKeys[i]]}: ${n}"></span>`:"").join("")}</div><ul>${counts.map((n,i)=>`<li><span><i style="background:${colors[i]}"></i>${labels[heightKeys[i]]}</span><span>${total?Math.round(n/total*100)+"%":"—"} · ${n}</span></li>`).join("")}</ul></div>`;
  }).join("");
}
function renderComparison(left,right) {
  const leftMatch=matches.find(m=>m.match_id===$("match").value),rightMatch=matches.find(m=>m.match_id===$("compare-match").value);
  const fields=[["rallies","回合数",""],["own_shots","所选方拍数",""],["wins","赢分回合",""],["losses","输分回合",""],["win_rate","回合胜率","%"],["avg_shots","平均回合拍数",""],["avg_duration","平均回合时长","s"]];
  $("comparison-data").innerHTML=`<table class="compare-table"><thead><tr><th>指标</th><th>${esc(leftMatch.title)}</th><th>${esc(rightMatch.title)}</th></tr></thead><tbody>${fields.map(([key,label,suffix])=>`<tr><td>${label}</td><td>${fmt(left.summary[key],suffix)}</td><td>${fmt(right.summary[key],suffix)}</td></tr>`).join("")}</tbody></table>`;
}
function render() {
  $("content").hidden=false;
  $("summary-text").textContent=window.AppText.sequence(data.summary_text);
  $("metric-win").textContent=`${data.summary.wins} / ${data.summary.wins+data.summary.losses}`;
  $("metric-rate").textContent=fmt(data.summary.win_rate,"%");
  $("metric-shots").textContent=data.summary.shots;$("metric-avg").textContent=fmt(data.summary.avg_shots);
  $("score-tags").textContent=data.game_scores.map(g=>`第${g.number}局  ${g.score.map(x=>fmt(x)).join("–")}`).join("  /  ");
  $("replay-count").textContent=`${data.summary.rallies} 回合 · ${data.summary.shots} 拍`;
  $("notes").innerHTML=data.notes.map(text=>`<li>${esc(window.AppText.sequence(text))}</li>`).join("");
  drawProgression();drawBars("winning",data.winning);drawBars("losing",data.losing);drawBars("usage",data.usage);drawOutcomes();drawLength();
  renderRally();renderPatterns(3);renderPatterns(5);
  facetFilters("contact-filters");facetFilters("error-filters");drawContact();stacked("error-chart",data.errors,errorKeys);
}
async function load(resetGame=false) {
  if(!$("match").value) return;
  const id=++requestId;activeRequest?.abort();activeRequest=new AbortController();
  document.dispatchEvent(new CustomEvent("ggab:loading"));
  const signal=activeRequest.signal;
  $("status").textContent="正在更新比赛分析…";$("error").hidden=true;$("content").hidden=true;
  if(resetGame) $("game").value="";
  const matchId=$("match").value, game=$("game").value, perspective=$("perspective").value;
  const params=new URLSearchParams({perspective});if(game) params.set("game",game);
  try {
    const requests=[getJSON(`/api/matches/${encodeURIComponent(matchId)}/breakdown?${params}`,signal)];
    if(mode==="compare") {
      requests.push(getJSON(`/api/matches/${encodeURIComponent($("compare-match").value)}/breakdown?perspective=${perspective}`,signal));
      requests.push(game?getJSON(`/api/matches/${encodeURIComponent(matchId)}/breakdown?perspective=${perspective}`,signal):requests[0]);
    }
    const results=await Promise.all(requests);if(id!==requestId)return;
    data=results[0];rallyIndex=0;selectedShot=null;
    if(videoMatch && videoMatch!==matchId) resetVideo();
    for(const state of Object.values(patternState)){state.mode="all";state.limit=5;}
    $("game").innerHTML=`<option value="">全部局数</option>`+data.games.map(g=>`<option value="${g}">第 ${g} 局</option>`).join("");$("game").value=game;
    const match=matches.find(m=>m.match_id===matchId);$("match-title").textContent=match?.title||"比赛分析";
    $("comparison").hidden=mode!=="compare";if(mode==="compare")renderComparison(results[2],results[1]);
    render();
    document.dispatchEvent(new CustomEvent("ggab:match-loaded"));
    if(data.perspective_labels)for(const option of $("perspective").options)option.textContent=data.perspective_labels[option.value]||option.textContent;
    $("status").textContent=`${match?.date||""} · ${data.summary.rallies} 回合 · ${data.summary.shots} 拍${data.summary.rallies?"":" · 当前筛选暂无数据"}`;
    const url=new URL(location.href);url.searchParams.set("match",matchId);url.searchParams.set("perspective",perspective);game?url.searchParams.set("game",game):url.searchParams.delete("game");history.replaceState(null,"",url);
  } catch(error) {
    if(error.name==="AbortError"||id!==requestId)return;
    $("error").hidden=false;$("error").textContent=window.AppText.error(error.message)+" 点击“刷新”重试。";$("status").textContent="加载失败";$("content").hidden=true;
  }
}
async function init() {
  try {
    matches=await getJSON("/api/matches");
    $("library-count").textContent=matches.length;$("library-detail").textContent=`${matches.reduce((n,m)=>n+m.shots,0)} 拍 · ${matches.reduce((n,m)=>n+m.rallies,0)} 个回合`;
    if(!matches.length){$("match-title").textContent="还没有比赛";$("match").innerHTML="<option>暂无比赛</option>";$("status").textContent="还没有比赛数据，请先创建项目并完成分析。";return;}
    const options=matches.map(m=>`<option value="${esc(m.match_id)}">${esc(m.title)} · ${esc(m.date)}</option>`).join("");
    document.dispatchEvent(new CustomEvent("ggab:sessions-loaded"));
    $("match").innerHTML=options;$("compare-match").innerHTML=options;$("match").disabled=false;
    const params=new URLSearchParams(location.search);
    const requested=matches.find(m=>m.match_id===params.get("match"))||matches.find(m=>m.local_session_id===params.get("match"));
    if(requested)$("match").value=requested.match_id;
    if(["you","opponent"].includes(params.get("perspective")))$("perspective").value=params.get("perspective");
    const game=params.get("game");if(game&&/^[1-9]\d*$/.test(game)){$("game").add(new Option(`第 ${game} 局`,game));$("game").value=game;}
    $("compare-match").value=matches.find(m=>m.match_id!==$("match").value)?.match_id||$("match").value;
    await load();
    registerTools();
  } catch(error) {$("error").hidden=false;$("error").textContent=window.AppText.error(error.message);$("status").textContent="暂时无法加载";$("match-title").textContent="无法加载比赛";}
}
$("refresh").addEventListener("click",async()=>{try{await refreshSessions();await load();}catch(error){message("刷新失败",error.message);}});
$("match").addEventListener("change",()=>load(true));$("game").addEventListener("change",()=>load());$("perspective").addEventListener("change",()=>load());$("compare-match").addEventListener("change",()=>load());
document.querySelectorAll("[data-mode]").forEach(button=>button.addEventListener("click",()=>{
  mode=button.dataset.mode;document.querySelectorAll("[data-mode]").forEach(b=>{b.classList.toggle("selected",b===button);b.setAttribute("aria-pressed",b===button);});
  $("compare-control").hidden=mode!=="compare";$("session-count").textContent=mode==="single"?"一场比赛":"两场比赛";load();
}));
$("filter-toggle").addEventListener("click",()=>{const hidden=!$("filters").hidden;$("filters").hidden=hidden;$("filter-toggle").setAttribute("aria-expanded",!hidden);$("filter-toggle").innerHTML=`☷ <span>${hidden?"显示筛选":"收起筛选"}</span>`;});
function updateNavA11y() {
  const narrow=matchMedia("(max-width: 900px)").matches;
  const expanded=narrow?document.body.classList.contains("nav-collapsed"):!document.body.classList.contains("nav-collapsed");
  $("nav-toggle").setAttribute("aria-expanded",expanded);$("sidebar").inert=!expanded;
}
$("nav-toggle").addEventListener("click",()=>{document.body.classList.toggle("nav-collapsed");updateNavA11y();});
matchMedia("(max-width: 900px)").addEventListener("change",updateNavA11y);updateNavA11y();
$("help").addEventListener("click",help);$("help-nav").addEventListener("click",help);$("dialog-close").addEventListener("click",()=>$("dialog").close());
document.querySelectorAll("[data-info]").forEach(b=>{b.setAttribute("aria-label","查看统计口径");b.addEventListener("click",()=>message("统计口径",b.dataset.info));});
$("prev-rally").addEventListener("click",()=>jumpRally(rallyIndex-1,false));$("next-rally").addEventListener("click",()=>jumpRally(rallyIndex+1,false));$("rally-slider").addEventListener("input",e=>jumpRally(Number(e.target.value),false));
$("rally-dots").addEventListener("click",e=>{const b=e.target.closest("[data-rally]");if(b)jumpRally(Number(b.dataset.rally),false);});
$("shot-rows").addEventListener("click",e=>{const row=e.target.closest("[data-shot]");if(row)jumpShot(row.dataset.shot);});
$("dialog-body").addEventListener("click",e=>{const b=e.target.closest("[data-detail-shot]");if(b){$("dialog").close();jumpShot(b.dataset.detailShot);$("replay").scrollIntoView({block:"start"});}});
document.querySelectorAll('label[for="video-file"]').forEach(label=>{
  label.setAttribute("role","button");label.tabIndex=0;
  label.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();$("video-file").click();}});
});
$("video-file").addEventListener("change",e=>{
  const file=e.target.files[0];if(!file||!data)return;
  if(videoURL)URL.revokeObjectURL(videoURL);videoURL=URL.createObjectURL(file);videoMatch=$("match").value;
  const video=$("video");video.src=window.GGABPath(videoURL);video.hidden=false;$("video-empty").hidden=true;$("video-name").textContent=file.name;
  video.playbackRate=Number($("speed").value);video.load();
  const chosen=data.rallies.flatMap(r=>r.shots).find(s=>s.shot_id===selectedShot);
  seek(chosen?.video_timestamp_seconds??data.rallies[rallyIndex]?.start??0);
  document.dispatchEvent(new CustomEvent("ggab:video-selected", {detail:{file,matchId:$("match").value}}));
});
$("video").addEventListener("error",()=>{if(videoURL)message("视频无法播放","浏览器无法解码这个视频，请选择浏览器支持的 MP4（H.264）或 WebM 视频。");});
$("speed").addEventListener("change",()=>$("video").playbackRate=Number($("speed").value));
$("video").addEventListener("timeupdate",()=>{
  if(!data||!videoURL)return;const t=$("video").currentTime;
  const index=data.rallies.findIndex(r=>r.start!=null&&r.end!=null&&t>=r.start&&t<=r.end);
  if(index<0)return;
  const shot=data.rallies[index].shots.filter(s=>s.video_timestamp_seconds!=null&&s.video_timestamp_seconds<=t).at(-1);
  if(index!==rallyIndex||shot?.shot_id!==selectedShot){rallyIndex=index;selectedShot=shot?.shot_id||null;renderRally();}
});
document.querySelectorAll("[data-pattern]").forEach(panel=>{
  const size=panel.dataset.pattern;
  panel.querySelectorAll("[data-pattern-mode]").forEach(b=>b.addEventListener("click",()=>{if(!data)return;patternState[size]={mode:b.dataset.patternMode,limit:5};renderPatterns(size);}));
  panel.querySelector(".show-more").addEventListener("click",()=>{if(!data)return;patternState[size].limit+=5;renderPatterns(size);});
});
$("contact-filters").addEventListener("change",drawContact);$("error-filters").addEventListener("change",()=>stacked("error-chart",filtered(data.errors,"error-filters"),errorKeys));
const resizeObserver=new ResizeObserver(()=>charts.forEach(instance=>instance.resize()));resizeObserver.observe($("main"));
window.addEventListener("beforeunload",()=>{if(videoURL)URL.revokeObjectURL(videoURL);resizeObserver.disconnect();});

function registerTools() {
  const context=document.modelContext;if(!context?.registerTool)return;
  try {Promise.resolve(context.registerTool({name:"read_match_breakdown",title:"读取当前比赛分析",description:"Read the current visible match, filters and rally summary without changing them.",inputSchema:{type:"object",properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute(input){if(!input||typeof input!=="object"||Object.keys(input).length)throw new Error("Expected an empty object.");if(!data)throw new Error("No match loaded.");return{match_id:data.match_id,perspective:data.perspective,game:$("game").value||null,summary:data.summary};}})).catch(()=>{});}catch(_){/* Optional browser capability. */}
}
let csvRevision = null, csvRevisionReady = false, csvRefreshing = false;
async function refreshSyncedData() {
  if (document.hidden || csvRefreshing) return;
  csvRefreshing = true;
  try {
    const current = await getJSON('/api/data-revision');
    if (csvRevisionReady && current.revision !== csvRevision) {
      // Preserve an active playback/upload; apply the update on a later poll.
      if (!$('video').paused || mediaBusy || pendingVideo) return;
      await refreshSessions();
      await load();
    }
    csvRevision = current.revision;
    csvRevisionReady = true;
  } catch { /* A temporary disconnection leaves the visible match intact. */ }
  finally { csvRefreshing = false; }
}
init().then(refreshSyncedData);
setInterval(refreshSyncedData,15000);
document.addEventListener('visibilitychange',refreshSyncedData);
