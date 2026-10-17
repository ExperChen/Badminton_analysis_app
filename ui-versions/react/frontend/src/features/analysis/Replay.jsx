import React,{forwardRef,useEffect,useImperativeHandle,useRef,useState} from 'react';
import {ChevronLeft,ChevronRight,Film,LoaderCircle,UploadCloud} from 'lucide-react';
import {useReducedMotion} from 'motion/react';
import {api,duration,errorText,format,path,term,uploadVideo} from '../../lib/api';

function SmoothPlayback({matchId,active,onSwitch,disabled}) {
  const [state,setState]=useState({state:'idle'}),[error,setError]=useState(''),[starting,setStarting]=useState(false),[revision,setRevision]=useState(0);
  useEffect(()=>{
    let disposed=false,timer,controller=new AbortController();
    async function refresh(){try{const result=await api(`/api/matches/${encodeURIComponent(matchId)}/playback-preview`,{signal:controller.signal});if(disposed)return;setState(result);if(result.state==='processing')timer=setTimeout(refresh,1500);}catch(e){if(!disposed&&e.name!=='AbortError')setError(errorText(e.message));}}
    refresh();return()=>{disposed=true;clearTimeout(timer);controller.abort();};
  },[matchId,revision]);
  async function activate(){
    if(active||state.state==='ready'){onSwitch(!active);return;}
    setStarting(true);setError('');try{setState(await api(`/api/matches/${encodeURIComponent(matchId)}/playback-preview`,{method:'POST'}));setRevision(n=>n+1);}catch(e){setError(errorText(e.message));}finally{setStarting(false);}
  }
  return <div className="smooth-playback"><button type="button" onClick={activate} disabled={disabled||starting||state.state==='processing'}>{state.state==='processing'&&<LoaderCircle size={13} className="animate-spin"/>}{active?'使用原片播放':state.state==='ready'?'切换到流畅播放':'生成流畅播放版本'}</button><span role="status">{error|| (state.state==='processing'?`正在生成 ${state.progress||0}%`:state.state==='ready'?'流畅版本已就绪':state.state==='failed'?'生成未完成，可重试':'原片始终保留，首次生成需要一些时间')}</span></div>;
}
const Replay=forwardRef(function Replay({data,onRefresh},ref){
  const player=useRef(null),section=useRef(null),fileInput=useRef(null),pendingSeek=useRef(null),rallySet=useRef(null),reduced=useReducedMotion();
  const [index,setIndex]=useState(0),[shotId,setShotId]=useState(null),[speed,setSpeed]=useState('1'),[pending,setPending]=useState(null),[smooth,setSmooth]=useState(false),[busy,setBusy]=useState(false),[upload,setUpload]=useState(null),[error,setError]=useState('');
  const rally=data.rallies[index];
  const source=pending?.url||(data.video?path(`/api/matches/${encodeURIComponent(data.match_id)}/${smooth?'playback-preview/video':'video'}?v=${encodeURIComponent(data.video.updated_at)}`):null);
  useEffect(()=>{
    const signature=data.rallies.map(r=>r.id).join(':');
    if(rallySet.current===signature)return;
    rallySet.current=signature;setIndex(0);setShotId(null);setError('');
    const first=data.rallies[0];pendingSeek.current=first?.start??first?.shots[0]?.video_timestamp_seconds??null;
    if(player.current?.readyState>=1&&pendingSeek.current!=null){seek(pendingSeek.current);pendingSeek.current=null;}
  },[data]);
  useEffect(()=>{setPending(null);setSmooth(false);},[data.match_id,data.video?.updated_at]);
  useEffect(()=>()=>{if(pending?.url)URL.revokeObjectURL(pending.url);},[pending]);
  useEffect(()=>{if(player.current)player.current.playbackRate=Number(speed);},[speed,source]);
  function seek(seconds){
    if(seconds==null||!source)return;
    const video=player.current;
    if(Number.isFinite(video.duration)&&seconds>video.duration){setError('时间戳超过视频时长，请关联与标注对应的完整原片。');return;}
    if(video.readyState>=1)video.currentTime=Math.max(0,seconds);else pendingSeek.current=seconds;
  }
  function jumpRally(next,navigate=false){
    const target=data.rallies[next];if(!target)return;setIndex(next);setShotId(null);seek(target.start??target.shots[0]?.video_timestamp_seconds);
    if(navigate)section.current.scrollIntoView({block:'start',behavior:reduced?'auto':'smooth'});
  }
  function jumpShot(id,navigate=false){
    const next=data.rallies.findIndex(r=>r.shots.some(s=>s.shot_id===id));if(next<0)return;
    const shot=data.rallies[next].shots.find(s=>s.shot_id===id);setIndex(next);setShotId(id);seek(shot.video_timestamp_seconds);
    if(navigate)section.current.scrollIntoView({block:'start',behavior:reduced?'auto':'smooth'});
  }
  useImperativeHandle(ref,()=>({jumpRally:i=>jumpRally(i,true),jumpShot:id=>jumpShot(id,true)}));
  function sync(){
    const time=player.current.currentTime,next=data.rallies.findIndex(r=>r.start!=null&&r.end!=null&&time>=r.start&&time<=r.end);
    if(next<0)return;
    setIndex(next);const shots=data.rallies[next].shots.filter(s=>s.video_timestamp_seconds!=null&&s.video_timestamp_seconds<=time);
    setShotId(shots.at(-1)?.shot_id||null);
  }
  function selectFile(event){const file=event.target.files[0];event.target.value='';if(!file)return;setPending({file,url:URL.createObjectURL(file)});setSmooth(false);setError('');}
  async function save(){
    if(!pending||busy)return;setBusy(true);setError('');
    try{await uploadVideo(pending.file,{sessionId:data.match_id,onProgress:event=>setUpload({percent:Math.round(event.loaded/event.total*100),phase:event.phase})});setPending(null);await onRefresh();}
    catch(e){setError(errorText(e.message));}finally{setBusy(false);setUpload(null);}
  }
  function switchSource(next){pendingSeek.current=player.current?.currentTime||0;setSmooth(next);}
  return <section id="replay" ref={section} className="react-replay" aria-label="比赛回放"><header className="analysis-card-header"><div><p className="yu-kicker">REVISIT THE MOMENT</p><h2>比赛回放</h2></div><span className="analysis-note">{data.summary.rallies} 回合 · {data.summary.shots} 拍</span></header><div className="react-replay-grid"><div className="react-footage"><div className="react-video-wrap">{source?<video ref={player} id="video" key={`${data.match_id}:${source}`} src={source} controls playsInline preload="metadata" onTimeUpdate={sync} onLoadedMetadata={()=>{player.current.playbackRate=Number(speed);if(pendingSeek.current!=null){player.current.currentTime=Math.min(pendingSeek.current,player.current.duration||pendingSeek.current);pendingSeek.current=null;}}} onError={()=>setError('视频暂时无法播放，请检查网络或重新关联视频。')}/>:<div className="react-video-empty"><Film size={35} strokeWidth={1}/><h3>为这场比赛关联视频</h3><p>选择完整原片，就能按每一拍的时间回放。</p><button onClick={()=>fileInput.current.click()}>选择本地视频</button></div>}</div><div className="react-player-tools"><label htmlFor="speed">播放速度<select id="speed" value={speed} onChange={e=>setSpeed(e.target.value)}>{['0.25','0.5','0.75','1','1.25','1.5','2'].map(s=><option key={s} value={s}>{s}×</option>)}</select></label><span title={pending?.file.name||data.video?.filename}>{pending?.file.name||data.video?.filename||'尚未关联原片'}</span><button className="analysis-text-button" onClick={()=>fileInput.current.click()} disabled={busy}>更换视频</button><input ref={fileInput} type="file" accept="video/*,.mkv,.avi" hidden onChange={selectFile}/></div>{pending&&<div className="pending-source"><p>本地视频可直接预览；保存后会关联到这场比赛。</p><button className="purple-button" onClick={save} disabled={busy}><UploadCloud size={14}/>{busy?'保存中…':'保存并关联视频'}</button>{upload&&<progress max="100" value={upload.percent}/>}</div>}<div className="react-rally-controls"><button id="prev-rally" onClick={()=>jumpRally(index-1)} disabled={!rally||index===0}><ChevronLeft size={15}/>上一回合</button><strong aria-live="polite">{rally?`回合 ${index+1} / ${data.rallies.length}`:'暂无回合'}</strong><button id="next-rally" onClick={()=>jumpRally(index+1)} disabled={!rally||index>=data.rallies.length-1}>下一回合<ChevronRight size={15}/></button></div><input id="rally-slider" type="range" aria-label="回合时间线" min="0" max={Math.max(0,data.rallies.length-1)} value={index} disabled={!rally} onChange={e=>jumpRally(Number(e.target.value))}/><div id="rally-dots" className="react-rally-dots">{data.rallies.map((r,i)=><button key={r.id} className={`${r.won===true?'won':r.won===false?'lost':'unknown'} ${i===index?'current':''}`} onClick={()=>jumpRally(i)} aria-pressed={i===index} aria-label={`第 ${r.game} 局，第 ${r.number} 回合，${r.won===true?'赢分':r.won===false?'输分':'未标注'}`}>{i+1}</button>)}</div>{data.video&&<SmoothPlayback key={data.match_id} matchId={data.match_id} active={smooth} onSwitch={switchSource} disabled={Boolean(pending)||busy}/>}</div><aside className="react-shot-panel" aria-label="逐拍记录"><header><h3 id="rally-title">{rally?`第 ${rally.game} 局 · 第 ${rally.number} 回合`:'逐拍记录'}</h3><span id="rally-info">{rally?`${rally.length} 拍 · ${duration(rally.duration)}`:''}</span></header>{rally&&<div className="react-rally-score"><span>回合开始</span><strong>{rally.score_before.map(x=>format(x)).join(' : ')}</strong><span className={rally.won?'won':'lost'}>{rally.won===true?'观察方赢分':rally.won===false?'观察方输分':'胜负未标注'}</span></div>}<div className="react-shot-scroll"><table className="analysis-table shot-records"><thead><tr><th>时间</th><th>技术 / 球员</th><th>结果</th></tr></thead><tbody id="shot-rows">{rally?.shots.map(shot=><tr key={shot.shot_id} data-shot={shot.shot_id} className={shotId===shot.shot_id?'active':''} onClick={()=>jumpShot(shot.shot_id)}><td><button disabled={shot.video_timestamp_seconds==null} aria-label={`跳到 ${duration(shot.video_timestamp_seconds)}`}>{duration(shot.video_timestamp_seconds)}</button></td><td><strong>{term(shot.shot_variant||shot.shot_family)}</strong><small className={shot.striker_is_you=== (data.perspective==='opponent'?0:1)?'own':''}>{shot.striker_name||shot.striker}</small></td><td><span className={`shot-result ${shot.outcome}`}>{term(shot.outcome)}</span></td></tr>)}</tbody></table>{!rally&&<p className="analysis-empty">还没有逐拍标注，视频仍可播放。</p>}</div><p className="shot-caption">点击时间定位 · 随播放同步到当前击球</p></aside></div>{error&&<p role="alert" className="analysis-alert">{error}</p>}</section>;
});
export default Replay;
