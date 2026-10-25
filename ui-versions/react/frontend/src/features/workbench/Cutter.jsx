import React,{useEffect,useRef,useState} from 'react';
import {ArrowRight,Check,Download,Film,LoaderCircle,RefreshCw,Scissors,UploadCloud} from 'lucide-react';
import Modal from '../../components/Modal';
import {api,path,uploadVideo,errorText} from '../../lib/api';
import {Heading,Empty,Loading,Notice,Progress} from './WorkbenchUI';
import {clock,clipSettings} from './model';

export default function Cutter(){
  const initial=useRef(new URLSearchParams(location.search)),video=useRef(null),file=useRef(null),mounted=useRef(true),lock=useRef(false),loadVersion=useRef(0);
  const [matches,setMatches]=useState([]),[matchId,setMatchId]=useState(''),[current,setCurrent]=useState(null),[game,setGame]=useState(initial.current.get('game')||''),[before,setBefore]=useState('0'),[after,setAfter]=useState('0'),[tools,setTools]=useState(null),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[plan,setPlan]=useState(null),[planKey,setPlanKey]=useState(''),[planLoading,setPlanLoading]=useState(false),[planError,setPlanError]=useState(''),[jobs,setJobs]=useState([]),[jobRevision,setJobRevision]=useState(0),[jobsError,setJobsError]=useState(''),[error,setError]=useState(''),[progress,setProgress]=useState(null),[replacement,setReplacement]=useState(null),[revision,setRevision]=useState(0),[videoError,setVideoError]=useState('');
  const settings=clipSettings(game,before,after),signature=JSON.stringify([matchId,settings,revision]);
  const activeJob=jobs.some(job=>['queued','running'].includes(job.status));
  useEffect(()=>{
    mounted.current=true;const controller=new AbortController();
    Promise.all([api('/api/matches',{signal:controller.signal}),api('/api/video-tools',{signal:controller.signal})]).then(([items,available])=>{
      if(!mounted.current)return;setMatches(items);setTools(available);
      const requested=items.find(item=>item.match_id===initial.current.get('match')||item.local_session_id===initial.current.get('match'));
      setMatchId((requested||items.find(item=>item.tagged&&item.has_video)||items[0])?.match_id||'');if(!items.length)setLoading(false);
    }).catch(e=>{if(mounted.current&&e.name!=='AbortError'){setError(e.message);setLoading(false);}});
    return()=>{mounted.current=false;controller.abort();};
  },[]);
  useEffect(()=>{
    if(!matchId)return;const controller=new AbortController(),version=++loadVersion.current;
    setLoading(true);setError('');setVideoError('');setCurrent(null);setPlan(null);setJobs([]);
    api('/api/matches/'+encodeURIComponent(matchId)+'/breakdown',{signal:controller.signal}).then(result=>{if(controller.signal.aborted||version!==loadVersion.current)return;setCurrent(result);if(game&&!result.games.includes(Number(game)))setGame('');}).catch(e=>{if(!controller.signal.aborted&&version===loadVersion.current&&e.name!=='AbortError')setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();
  },[matchId,revision]);
  useEffect(()=>{
    if(!matchId)return;let live=true,timer;const controller=new AbortController();
    async function poll(){
      try{
        const result=await api('/api/matches/'+encodeURIComponent(matchId)+'/clips',{signal:controller.signal});if(!live)return;
        setJobs(result);setJobsError('');if(result.some(job=>['queued','running'].includes(job.status)))timer=setTimeout(poll,2000);
      }catch(e){if(live&&e.name!=='AbortError')setJobsError(e.message);}
    }
    poll();return()=>{live=false;clearTimeout(timer);controller.abort();};
  },[matchId,jobRevision]);
  useEffect(()=>{
    setPlan(null);setPlanError('');setPlanKey('');
    if(!current||current.match_id!==matchId||!current.video){setPlanLoading(false);return;}
    if(!settings){setPlanError('回合前后保留时间必须是 0–5 秒。');setPlanLoading(false);return;}
    const controller=new AbortController();setPlanLoading(true);
    const timer=setTimeout(()=>{
      const params=new URLSearchParams({before:settings.before,after:settings.after});if(settings.game)params.set('game',settings.game);
      api('/api/matches/'+encodeURIComponent(matchId)+'/clip-plan?'+params,{signal:controller.signal}).then(result=>{if(!controller.signal.aborted){setPlan(result);setPlanKey(signature);}}).catch(e=>{if(!controller.signal.aborted&&e.name!=='AbortError')setPlanError(e.message);}).finally(()=>{if(!controller.signal.aborted)setPlanLoading(false);});
    },250);
    return()=>{clearTimeout(timer);controller.abort();};
  },[current,matchId,signature]);
  useEffect(()=>{if(!matchId)return;const url=new URL(location.href);url.searchParams.set('match',matchId);if(game)url.searchParams.set('game',game);else url.searchParams.delete('game');history.replaceState(null,'',url);},[matchId,game]);
  async function generate(){
    if(lock.current||!settings||!plan||planKey!==signature||!tools?.available||activeJob)return;
    lock.current=true;setBusy(true);setError('');const id=matchId;
    try{
      const job=await api('/api/matches/'+encodeURIComponent(id)+'/clips',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)});
      if(mounted.current){setJobs(existing=>[job,...existing.filter(item=>item.id!==job.id)]);setJobRevision(n=>n+1);}
    }catch(e){if(mounted.current)setError(e.message);}finally{lock.current=false;if(mounted.current)setBusy(false);}
  }
  async function replaceVideo(input){
    if(lock.current)return;lock.current=true;setBusy(true);setError('');setReplacement(null);
    try{
      await uploadVideo(input,{sessionId:matchId,onProgress:event=>{if(mounted.current)setProgress({label:event.phase==='verifying'?'正在核验原视频':event.phase==='checking'?'正在恢复上传进度':'正在上传 '+input.name,percent:Math.round(event.loaded/event.total*100)});}});
      const items=await api('/api/matches');if(mounted.current){setMatches(items);setRevision(n=>n+1);}
    }catch(e){if(mounted.current)setError(e.message);}finally{lock.current=false;if(mounted.current){setBusy(false);setProgress(null);}}
  }
  const ready=!loading&&!busy&&tools?.available&&plan&&planKey===signature&&!planLoading&&!activeJob;
  return <main className="workbench" data-testid="react-cutter">
    <Heading eyebrow="KEEP THE RALLIES, KEEP THE MOMENTS" title="回合剪辑" description="留下每一次精彩交锋，让等待与间隔留在原片里。"><a className="analysis-small-button" href={path('/insights/match-breakdown'+(matchId?'?match='+encodeURIComponent(matchId):''))}>打开比赛复盘<ArrowRight size={14}/></a></Heading>
    <Notice error>{error}</Notice>
    <section className="workbench-card"><div className="workbench-card-head"><div><h2>先选一场，再留一点余量</h2><p>沿用已有回合标注，生成独立精剪文件；原片永远保留。</p></div><span className={'workbench-pill '+(tools?.available?'success':'subtle')}>{tools==null?'检查剪辑工具…':tools.available?'CPU 剪辑工具就绪':'剪辑工具不可用'}</span></div>
      <div className="cutter-settings"><label className="workbench-field">比赛<select value={matchId} disabled={busy||!matches.length} onChange={e=>{setGame('');setMatchId(e.target.value);}}>{!matches.length&&<option value="">还没有可用比赛</option>}{matches.map(item=><option key={item.match_id} value={item.match_id}>{item.title}{item.has_video?'':' · 原片不可用'}</option>)}</select></label><label className="workbench-field">局数<select value={game} disabled={busy||loading} onChange={e=>setGame(e.target.value)}><option value="">全部局数</option>{current?.games.map(value=><option key={value} value={value}>第 {value} 局</option>)}</select></label><label className="workbench-field">回合前保留（秒）<input type="number" min="0" max="5" step=".1" value={before} disabled={busy} onChange={e=>setBefore(e.target.value)}/></label><label className="workbench-field">回合后保留（秒）<input type="number" min="0" max="5" step=".1" value={after} disabled={busy} onChange={e=>setAfter(e.target.value)}/></label></div>
      <div className="workbench-footer"><div className="workbench-actions"><input type="file" ref={file} hidden accept=".mp4,.mov,.mkv,.webm,.avi,.m4v" onChange={e=>{const value=e.target.files[0];e.target.value='';if(value)setReplacement(value);}}/><button className="analysis-small-button" disabled={busy||loading||!matchId||activeJob} onClick={()=>file.current.click()}><UploadCloud size={14}/>更换原视频</button><button className="analysis-small-button" aria-label="刷新剪辑状态" disabled={busy} onClick={()=>{setRevision(n=>n+1);setJobRevision(n=>n+1);}}><RefreshCw size={14}/>刷新</button></div><button className="purple-button" disabled={!ready} onClick={generate}>{busy?<LoaderCircle size={16} className="animate-spin"/>:<Scissors size={16}/>}生成精剪视频</button></div><Progress progress={progress}/>{!tools?.available&&tools&&<p className="workbench-tip">请管理员检查 FFmpeg / ffprobe 是否可用。</p>}
    </section>
    <div className="workbench-grid" style={{marginTop:20}}><section className="workbench-card"><div className="workbench-card-head"><h2>原片预览</h2><span className="workbench-counter">{current?.video?clock(current.video.duration):'尚未选择'}</span></div>{loading?<Loading/>:current?.video?<><video ref={video} className="workbench-video" controls playsInline preload="metadata" key={matchId+':'+revision} src={path('/api/matches/'+encodeURIComponent(matchId)+'/video')} onError={()=>setVideoError('原片暂时无法播放，请检查网络或联系管理员核对视频存储授权。')}/><Notice error>{videoError}</Notice></>:<div className="cutter-video-empty"><Empty title="还没有可用的原片">选择比赛后可关联视频。原视频不会被剪辑覆盖。</Empty></div>}<p className="workbench-tip">已有标注与原片需要对应同一场比赛。更换原片前请核对文件、时长与回合时间。</p></section>
    <section className="workbench-card"><div className="workbench-card-head"><h2>这一份精剪，将会留下</h2><span className="workbench-pill subtle">{game?'第 '+game+' 局':'全部局数'}</span></div>{planLoading?<Loading text="正在核对回合与时间轴…"/>:<><div className="workbench-metrics">{[['回合数',plan?.rally_count],['合并片段',plan?.segment_count],['保留时长',plan?clock(plan.kept_duration):null],['移除时长',plan?clock(plan.removed_duration):null]].map(([label,value])=><article key={label}><span>{label}</span><strong>{value??'—'}</strong></article>)}</div><p className="workbench-subtitle" role="status">{planError||(!current?.video?'选择原视频后可计算剪辑方案。':plan?plan.rally_count+' 个回合合并为 '+plan.segment_count+' 个片段。':'当前比赛暂无可剪辑的回合时间标注。')}</p>{plan?.segments.length>0&&<div className="cutter-plan-parts" aria-label="精剪片段">{plan.segments.map((part,i)=><button key={i} onClick={()=>{if(video.current)video.current.currentTime=part.start;}}>片段 {i+1} · {clock(part.start)}–{clock(part.end)}</button>)}</div>}</>}<p className="workbench-tip">相邻回合会合并为连续片段。比分在回合结束前 1.25 秒出现，未知比分不会推测。</p></section></div>
    <section className="workbench-card" style={{marginTop:20}}><div className="workbench-card-head"><div><h2>剪辑记录</h2><p>最近五次 · 文件保存在本站，可随时回来下载。</p></div>{activeJob&&<span className="workbench-pill"><LoaderCircle size={12} className="animate-spin"/>任务进行中</span>}</div><Notice error>{jobsError}</Notice><div className="cutter-jobs">{jobs.slice(0,5).map(job=><article className="cutter-job" key={job.id}><div className="cutter-job-content"><strong>{job.status==='complete'?'精剪完成':job.status==='failed'?'剪辑未完成':job.status==='queued'?'等待剪辑':'正在剪辑'}{['queued','running'].includes(job.status)?' · '+job.progress+'%':''}</strong><small>{job.game?'第 '+job.game+' 局':'全部局数'} · {job.plan?.rally_count??'—'} 个回合 · {new Date(job.created_at).toLocaleString()}{job.status==='complete'?' · '+clock(job.output_duration):''}</small>{['queued','running'].includes(job.status)&&<progress value={job.progress} max="100"/>}{job.status==='failed'&&<Notice error>{errorText(job.error)}</Notice>}</div>{job.status==='complete'&&<div className="workbench-actions"><span className="workbench-pill success"><Check size={12}/>已完成</span><a className="purple-button" href={path(job.download_url)}><Download size={14}/>下载视频</a><a className="analysis-small-button" href={path(job.timeline_url)}>时间轴</a></div>}</article>)}</div>{!jobs.length&&!jobsError&&<Empty title="精彩片段，等待被整理">核对方案后生成精剪视频，处理状态会在这里自动更新。</Empty>}</section>
    {replacement&&<Modal title="更换这场比赛的原视频？" onClose={()=>setReplacement(null)} actions={<><button className="analysis-small-button" onClick={()=>setReplacement(null)}>取消</button><button className="purple-button" onClick={()=>replaceVideo(replacement)}>上传并关联</button></>}><p className="workbench-subtitle">将 {replacement.name} 关联到当前比赛。请确认它与已有标注使用相同时间轴，其他原视频不会被剪辑覆盖。</p></Modal>}
  </main>;
}
