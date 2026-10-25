export const slotsFor = game => game === 'doubles' ? ['A1','A2','B1','B2'] : ['A1','B1'];
export const videoId = item => item.local_session_id || item.video?.session_id || item.match_id;
export const clock = value => {
  const seconds = Math.max(0,Number(value)||0);
  return `${Math.floor(seconds/60)}:${(seconds%60).toFixed(2).padStart(5,'0')}`;
};
export const emptyPlayer = slot => ({slot,name:'',handedness:'right',is_you:false,box:null,snapshot:null,time:0,rotation:0});
export function freshProject(options=[]) {
  const me=options.find(p=>p.is_you);
  return {id:null,title:'',match_date:'',session_id:'',game_type:'singles',playing:true,segments:[],frame:{time:0,rotation:0},
    players:{A1:{...emptyPlayer('A1'),name:me?.name||'我',handedness:me?.handedness||'right',is_you:true},B1:emptyPlayer('B1')}};
}
export function hydrateProject(saved) {
  const result={...freshProject(),...saved,players:{}};
  const players=Array.isArray(saved.players)?saved.players:Object.values(saved.players||{});
  for(const player of players){
    const snap=saved.snapshots?.[player.slot]||player.snapshot;
    result.players[player.slot]={...emptyPlayer(player.slot),...player,...(snap?{time:snap.time,rotation:snap.rotation,box:snap.box,snapshot:snap}:{}),saving:false,error:''};
  }
  const first=result.players.A1;
  result.frame=saved.frame||{time:first?.time||0,rotation:first?.rotation||0};
  for(const slot of slotsFor(result.game_type))result.players[slot]??=emptyPlayer(slot);
  return result;
}
export function setSharedFrame(project,time,rotation){
  return {...project,frame:{time,rotation},players:Object.fromEntries(Object.entries(project.players).map(([key,p])=>[key,{...p,time,rotation,box:null,snapshot:null,saving:false,error:''}]))};
}
export function selectedVideo(project,items){
  return items.find(v=>v.has_video&&v.video&&(videoId(v)===project.session_id||v.match_id===project.session_id));
}
export function validateProject(project,items,step){
  const problems=[],video=selectedVideo(project,items),duration=video?.video?.duration||0;
  if(step==null||step===0){
    if(!project.title.trim())problems.push('请输入项目名称。');
    if(!video)problems.push('请选择可用的原视频。');
    if(project.match_date){
      const date=project.match_date;
      if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||date.startsWith('0000')||Number.isNaN(Date.parse(date+'T00:00:00Z'))||new Date(date+'T00:00:00Z').toISOString().slice(0,10)!==date)problems.push('请输入有效的比赛日期，年份为四位数。');
    }
  }
  if(step==null||step===2){
    const players=slotsFor(project.game_type).map(s=>project.players[s]||emptyPlayer(s));
    for(const p of players){if(!p.name.trim())problems.push(`${p.slot}：请填写球员名称。`);if(!p.snapshot||p.saving)problems.push(`${p.slot}：请完成框选并等待截图保存成功。`);}
    if(players.filter(p=>p.is_you).length!==Number(project.playing))problems.push('请正确选择本人位置；参赛时只能选择一个。');
  }
  if(step==null||step===3){
    let previous=0;
    for(const [i,s] of [...project.segments].sort((a,b)=>a.start-b.start).entries()){
      if(!Number.isFinite(s.start)||!Number.isFinite(s.end)||s.start<previous||s.start<0||s.end<=s.start||s.end>duration)problems.push(`片段 ${i+1}：时间必须在原片范围内，且不重叠。`);
      previous=s.end;
      for(const key of ['a_start','b_start','a_end','b_end'])if(s[key]!=null&&(!Number.isInteger(s[key])||s[key]<0||s[key]>999))problems.push('比分必须是 0–999 的整数，未知时留空。');
      for(const side of ['a','b'])if(s[side+'_start']!=null&&s[side+'_end']!=null&&s[side+'_end']<s[side+'_start'])problems.push('结束比分不能小于开始比分；新一局请另建片段。');
    }
  }
  return [...new Set(problems)];
}
export function projectPayload(project,items){
  return {title:project.title.trim(),match_date:project.match_date||null,session_id:videoId(selectedVideo(project,items)),game_type:project.game_type,playing:project.playing,
    players:slotsFor(project.game_type).map(slot=>{const p=project.players[slot];return {slot,name:p.name.trim(),handedness:p.handedness,is_you:p.is_you,snapshot_id:p.snapshot.id};}),segments:project.segments};
}
export function nextSegment(segments,duration){
  let start=0;
  for(const s of [...segments].sort((a,b)=>a.start-b.start)){
    if(s.start-start>=.1)return {start,end:s.start,first_server:'near'};
    start=Math.max(start,s.end);
  }
  return duration-start>=.1?{start,end:duration,first_server:'near'}:null;
}
export function cleanDraft(project){return {...project,players:Object.fromEntries(Object.entries(project.players).map(([s,p])=>[s,{...p,saving:false,error:''}]))};}
export function clipSettings(game,before,after){
  if(before===''||after==='')return null;
  const values={game:game?Number(game):null,before:Number(before),after:Number(after)};
  return [values.before,values.after].every(n=>Number.isFinite(n)&&n>=0&&n<=5)?values:null;
}
