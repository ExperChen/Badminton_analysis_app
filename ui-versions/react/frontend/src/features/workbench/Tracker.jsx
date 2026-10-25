import React,{useEffect,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,Check,Film,FolderOpen,LoaderCircle,Plus,RefreshCw,Save,Search,Trash2,UploadCloud,UserRound,UsersRound} from 'lucide-react';
import Modal from '../../components/Modal';
import {api,path,go,uploadVideo,errorText} from '../../lib/api';
import {Heading,Empty,Loading,Notice,Progress} from './WorkbenchUI';
import PlayerFrame,{Snapshots} from './PlayerFrame';
import SegmentsEditor from './SegmentsEditor';
import {cleanDraft,clock,emptyPlayer,freshProject,hydrateProject,projectPayload,selectedVideo,setSharedFrame,slotsFor,validateProject,videoId} from './model';

const steps=['选择视频','比赛类型','球员标注','比赛片段','确认项目'];
const post=(url,body,method='POST')=>api(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export default function Tracker(){
  const route=useRef(window.YuxuRoute()),initial=useRef(new URLSearchParams(location.search)),mounted=useRef(true),generation=useRef(0),snapshotVersions=useRef({}),busyRef=useRef(false),file=useRef(null),draftKey=useRef(''),draftEnabled=useRef(true),current=useRef(null);
  const editing=route.current.endsWith('/new')||route.current.startsWith('/tracker/projects/');
  const [project,setProject]=useState(null),[projects,setProjects]=useState([]),[videos,setVideos]=useState([]),[options,setOptions]=useState([]),[disk,setDisk]=useState({directory:'',videos:[]}),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[search,setSearch]=useState(''),[step,setStep]=useState(0),[active,setActive]=useState('A1'),[progress,setProgress]=useState(null),[dialog,setDialog]=useState(null),[admin,setAdmin]=useState(false);
  current.current=project;
  const change=updater=>setProject(previous=>{const next=typeof updater==='function'?updater(previous):updater;current.current=next;return next;});
  useEffect(()=>{
    mounted.current=true;const controller=new AbortController();
    (async()=>{
      try{
        const user=await window.YuxuAuthReady;if(!mounted.current)return;
        const routeMatch=/^\/tracker\/projects\/([^/]+)$/.exec(route.current),requestedId=routeMatch?decodeURIComponent(routeMatch[1]):initial.current.get('project');
        setAdmin(user.role==='admin');draftKey.current='yuxu-tracker-react-v1:'+user.id+':'+(requestedId||'new');
        const [items,saved,names,candidates]=await Promise.all([api('/api/matches',{signal:controller.signal}),api('/api/tracker/projects',{signal:controller.signal}),api('/api/tracker/player-options',{signal:controller.signal}),api('/api/sessions/local-videos',{signal:controller.signal})]);
        if(!mounted.current)return;setVideos(items);setProjects(saved);setOptions(names);setDisk(candidates);
        if(editing){
          const match=/^\/tracker\/projects\/([^/]+)$/.exec(route.current),id=match?decodeURIComponent(match[1]):initial.current.get('project');
          let value;
          if(id)value=hydrateProject(await api('/api/tracker/projects/'+encodeURIComponent(id),{signal:controller.signal}));
          else{
            try{const draft=JSON.parse(localStorage.getItem(draftKey.current)||localStorage.getItem('yuxu-tracker-draft-v3:'+user.id)||'null');if(draft&&!draft.id&&typeof draft.players==='object'&&Array.isArray(draft.segments)){value=hydrateProject(draft);setNotice('已恢复上次未完成的项目。');}}catch{}
            value??=freshProject(names);
          }
          const selected=selectedVideo(value,items);if(selected)value.session_id=videoId(selected);
          if(mounted.current)change(value);
        }
      }catch(e){if(e.name!=='AbortError'&&mounted.current)setError(e.message);}finally{if(mounted.current)setLoading(false);}
    })();
    return()=>{mounted.current=false;generation.current++;controller.abort();};
  },[]);
  useEffect(()=>{if(!editing||!project||!draftKey.current||!draftEnabled.current)return;try{localStorage.setItem(draftKey.current,JSON.stringify(cleanDraft(project)));}catch{setNotice('浏览器无法保存草稿，请在离开前保存项目。');}},[project,editing]);
  const selected=project?selectedVideo(project,videos):null,duration=selected?.video?.duration||0;
  const snapshotsPending=project&&slotsFor(project.game_type).some(s=>project.players[s]?.saving);
  const ask=(title,text,action)=>setDialog({title,text,action});
  const resetFrame=(time,rotation)=>{generation.current++;change(p=>setSharedFrame(p,time,rotation));setError('');};
  function requestFrame(time,rotation){
    const p=current.current;if(time===p.frame.time&&rotation===p.frame.rotation)return;
    if(Object.values(p.players).some(player=>player.box||player.snapshot))ask('重新选择画面？','所有球员的选框和截图会清除，名称与惯用手保留。',()=>resetFrame(time,rotation));
    else resetFrame(time,rotation);
  }
  function updatePlayer(slot,values){
    change(p=>{
      const players={...p.players};
      if(values.is_you)for(const key of Object.keys(players))players[key]={...players[key],is_you:false};
      const remembered=values.name!=null?options.find(item=>item.name===values.name):null;
      players[slot]={...players[slot],...(remembered?.handedness?{handedness:remembered.handedness}:{}),...values};return {...p,players};
    });
  }
  async function capture(slot,box){
    const p=current.current;if(!p||busyRef.current)return;
    const epoch=generation.current,version=(snapshotVersions.current[slot]||0)+1,frame={...p.frame};snapshotVersions.current[slot]=version;
    updatePlayer(slot,{box,...frame,snapshot:null,saving:true,error:''});
    try{
      const snap=await post('/api/tracker/snapshots',{session_id:p.session_id,...frame,box});
      if(!mounted.current||epoch!==generation.current||snapshotVersions.current[slot]!==version)return;
      updatePlayer(slot,{snapshot:snap,saving:false,error:''});
    }catch(e){if(mounted.current&&epoch===generation.current&&snapshotVersions.current[slot]===version)updatePlayer(slot,{saving:false,error:errorText(e.message)});}
  }
  function selectVideo(item){
    const perform=()=>{generation.current++;change(p=>videoId(item)===p.session_id?p:{...freshProject(options),id:p.id,title:item.title,match_date:item.date||'',session_id:videoId(item)});setNotice('已选择原视频，保存前请核对实际比赛日期。');setError('');};
    if(project.session_id!==videoId(item)&&(Object.values(project.players).some(p=>p.snapshot)||project.segments.length))ask('更换原视频？','这会清除当前球员选框和比赛片段，不会删除原视频。',perform);else perform();
  }
  async function refresh(){setError('');try{const [items,saved,candidates]=await Promise.all([api('/api/matches'),api('/api/tracker/projects'),api('/api/sessions/local-videos')]);if(mounted.current){setVideos(items);setProjects(saved);setDisk(candidates);}}catch(e){setError(e.message);}}
  async function addVideo(input,relativePath){
    if(busyRef.current)return;busyRef.current=true;setBusy(true);setError('');
    try{
      const result=relativePath?await post('/api/sessions/link',{relative_path:relativePath}):await uploadVideo(input,{onProgress:event=>{if(mounted.current)setProgress({label:event.phase==='verifying'?'正在核验原视频':event.phase==='checking'?'正在恢复上传进度':'正在上传 '+input.name,percent:Math.round(event.loaded/event.total*100)});}});
      const [items,candidates]=await Promise.all([api('/api/matches'),api('/api/sessions/local-videos')]);
      if(!mounted.current)return;setVideos(items);setDisk(candidates);
      const item=items.find(v=>videoId(v)===result.match_id||v.match_id===result.match_id);if(!item)throw new Error('视频已上传，但暂未出现在当前账号视频库，请刷新后核对。');
      generation.current++;change(p=>({...freshProject(options),id:p.id,title:item.title,session_id:videoId(item),match_date:item.date||''}));setNotice(relativePath?'已直接关联原片，没有创建本地副本。':'原视频已保存并选中，请核对实际比赛日期。');
    }catch(e){if(mounted.current)setError(e.message);}finally{busyRef.current=false;if(mounted.current){setBusy(false);setProgress(null);}}
  }
  function requestVideo(input,relativePath){
    if(project.session_id&&(Object.values(project.players).some(p=>p.snapshot)||project.segments.length))ask('更换原视频？','当前球员选框和片段设置会清除，已有原片不会删除。',()=>addVideo(input,relativePath));
    else addVideo(input,relativePath);
  }
  function jump(target){
    if(busy)return;const issues=target>0?validateProject(project,videos,0):[];
    if(issues.length){setError(issues.join('\n'));return;}setError('');setStep(target);
  }
  async function save(){
    if(busyRef.current)return;const issues=validateProject(current.current,videos);if(issues.length){setError(issues.join('\n'));return;}
    busyRef.current=true;setBusy(true);setError('');
    try{
      const p=current.current,saved=await post('/api/tracker/projects'+(p.id?'/'+encodeURIComponent(p.id):''),projectPayload(p,videos),p.id?'PUT':'POST');
      draftEnabled.current=false;change(value=>({...value,id:saved.id}));
      try{localStorage.removeItem(draftKey.current);}catch{}
      go('/tracker');
    }catch(e){if(mounted.current)setError(e.message);}finally{busyRef.current=false;if(mounted.current)setBusy(false);}
  }
  function next(){const issues=validateProject(project,videos,step);if(issues.length){setError(issues.join('\n'));return;}if(step===4)save();else jump(step+1);}
  function changeGame(game){change(p=>{const players={...p.players};for(const s of slotsFor(game))players[s]??=emptyPlayer(s);if(p.playing&&slotsFor(game).filter(s=>players[s].is_you).length!==1){for(const s of Object.keys(players))players[s]={...players[s],is_you:s==='A1'};}return {...p,game_type:game,players};});if(!slotsFor(game).includes(active))setActive('A1');}
  function playing(value){change(p=>{const players={...p.players};for(const s of Object.keys(players))players[s]={...players[s],is_you:value&&s==='A1'};return {...p,playing:value,players};});}
  async function remove(p){if(busyRef.current)return;busyRef.current=true;setBusy(true);try{await api('/api/tracker/projects/'+encodeURIComponent(p.id),{method:'DELETE'});setProjects(items=>items.filter(item=>item.id!==p.id));setNotice('项目已归档，原视频和已有数据保留。');}catch(e){setError(e.message);}finally{busyRef.current=false;setBusy(false);}}
  const usable=[...new Map(videos.filter(v=>v.has_video&&v.video).map(v=>[videoId(v),v])).values()];
  const shown=projects.filter(p=>(p.title+' '+(p.match_date||'')).toLowerCase().includes(search.toLowerCase()));
  return <main className="workbench" data-testid="react-tracker">
    <Heading eyebrow="YOUR TRAINING, THOUGHTFULLY ORGANIZED" title={editing?(project?.id?'编辑训练项目':'新建训练项目'):'训练项目'} description={editing?'从原片、球员到比赛片段，把值得复盘的细节整理好。':'为每场训练留下清晰的记录，也为下一次进步做好准备。'}>{editing?<a className="analysis-small-button" href={path('/tracker')}><ArrowLeft size={14}/>返回项目</a>:<a className="purple-button" href={path('/tracker/new')}><Plus size={16}/>新建项目</a>}</Heading>
    <Notice error>{error}</Notice><Notice>{notice}</Notice>
    {loading?<Loading/>:!editing?<>
      <div className="workbench-toolbar"><label className="workbench-search"><Search size={15}/><input aria-label="搜索训练项目" placeholder="搜索项目名称或日期" value={search} onChange={e=>setSearch(e.target.value)}/></label><div className="workbench-actions"><span className="workbench-counter">{shown.length} 个项目</span><button className="analysis-icon" aria-label="刷新项目" onClick={refresh} disabled={busy}><RefreshCw size={15}/></button></div></div>
      {shown.length?<div className="workbench-projects">{shown.map(p=><article className="workbench-card workbench-project" key={p.id}><div className="workbench-project-top"><span className="workbench-project-icon"><FolderOpen size={21}/></span><span className={'workbench-pill '+(p.analysis_match_id?'success':'subtle')}>{p.analysis_match_id?<Check size={12}/>:null}{p.analysis_match_id?'已有复盘数据':'设置已保存'}</span></div><h2>{p.title}</h2><p className="workbench-project-meta">{p.match_date||'未填写日期'} · {p.game_type==='singles'?'单打':'双打'} · {p.players.length} 位球员</p><div className="workbench-project-bottom"><a className="analysis-small-button" data-edit={p.id} href={path('/tracker/projects/'+encodeURIComponent(p.id))}>编辑项目<ArrowRight size={13}/></a>{p.analysis_match_id&&<a className="purple-button" href={path('/insights/match-breakdown?match='+encodeURIComponent(p.analysis_match_id))}>打开复盘<ArrowRight size={13}/></a>}<button className="icon-button danger" aria-label={'删除项目 '+p.title} disabled={busy} onClick={()=>ask('归档这个项目？','项目会从列表移除，原视频和已有逐拍数据保留。',()=>remove(p))}><Trash2 size={16}/></button></div></article>)}</div>:<section className="workbench-card"><Empty title={search?'没有找到匹配的项目':'从你的第一场训练开始'}>选择原视频，标记球员，保存比赛设置。已有逐拍数据可继续在比赛复盘中查看。</Empty></section>}
      <p className="workbench-list-footer">项目仅保存在本站，不会触发外部上传或自动分析。</p>
    </>:project?<>
      <nav className="workbench-steps" aria-label="项目创建步骤">{steps.map((label,i)=><button className="workbench-step" key={label} aria-current={step===i?'step':undefined} disabled={busy} onClick={()=>jump(i)}><span>{i+1}</span>{label}</button>)}</nav>
      <section className="workbench-card">
        {step===0&&<><div className="workbench-form-grid"><label className="workbench-field">项目名称<input maxLength={240} value={project.title} onChange={e=>change(p=>({...p,title:e.target.value}))} disabled={busy}/></label><label className="workbench-field">比赛日期<input type="date" min="0001-01-01" max="9999-12-31" value={project.match_date||''} disabled={busy} onChange={e=>{const value=e.target.value.replace(/^(\d{4})\d+-(\d{2})-(\d{2})$/,'$1-$2-$3');change(p=>({...p,match_date:value}));}}/></label></div><input hidden type="file" ref={file} accept=".mp4,.mov,.mkv,.webm,.avi,.m4v" onChange={e=>{const f=e.target.files[0];e.target.value='';if(f)requestVideo(f);}}/><button className="workbench-drop" disabled={busy} onClick={()=>file.current.click()}><UploadCloud size={24}/><div><strong>上传一段原视频</strong><p>支持 MP4、MOV、MKV、WebM 等格式，断网后可续传。</p></div></button><Progress progress={progress}/><div className="workbench-toolbar"><h2>或选择视频空间中的原片</h2><label className="workbench-search"><Search size={14}/><input aria-label="搜索原视频" placeholder="搜索视频" value={search} onChange={e=>setSearch(e.target.value)}/></label></div><div className="workbench-selection">{usable.filter(v=>v.title.toLowerCase().includes(search.toLowerCase())).map(v=><button className="workbench-video-option" key={videoId(v)} aria-pressed={project.session_id===videoId(v)} disabled={busy} onClick={()=>selectVideo(v)}><Film size={19}/><div><strong>{v.title}</strong><small>{clock(v.video.duration)} · {v.tagged?'已有逐拍数据':'完整原片'}</small></div>{project.session_id===videoId(v)&&<Check size={15}/>}</button>)}</div>{!usable.length&&<Empty title="还没有原视频">先上传视频，再继续设置。</Empty>}{admin&&<details><summary>关联本机尚未登记的原片</summary><p className="workbench-tip">{disk.directory} · 直接复用文件，不复制原片。</p><div className="workbench-selection">{disk.videos.map(v=><button className="workbench-video-option" key={v.relative_path} disabled={busy} onClick={()=>requestVideo(null,v.relative_path)}><Film size={16}/><div><strong>{v.relative_path}</strong><small>{(v.size_bytes/1048576).toFixed(1)} MiB · 直接关联</small></div></button>)}</div>{!disk.videos.length&&<p className="workbench-tip">目录中没有尚未登记的原片。</p>}</details>}{admin&&selected&&<button className="analysis-small-button" disabled={busy} onClick={()=>ask('核对并释放重复副本？','仅在哈希与文件状态核对一致后释放冗余副本，保留命名原片及关联。',async()=>{try{const result=await post('/api/sessions/'+encodeURIComponent(project.session_id)+'/reclaim-duplicate',{});setNotice(result.message||'原片核对已完成。');await refresh();}catch(e){setError(e.message);}})}>核对并安全释放重复副本</button>}</>}
        {step===1&&<><h2>这一场，怎样记录？</h2><p className="workbench-subtitle">选择比赛类型，以及你在这场比赛中的角色。</p><div className="workbench-choice-grid">{[['singles','单打',UserRound,'两位球员，各自成就每一拍。'],['doubles','双打',UsersRound,'四位球员，记录配合与默契。']].map(([value,label,Icon,text])=><button key={value} className="workbench-choice" aria-pressed={project.game_type===value} disabled={busy} onClick={()=>changeGame(value)}><Icon size={24}/><strong>{label}</strong><small>{text}</small></button>)}</div><div className="workbench-row"><button className="analysis-small-button" aria-pressed={project.playing} onClick={()=>playing(true)}>我参与比赛</button><button className="analysis-small-button" aria-pressed={!project.playing} onClick={()=>playing(false)}>我只记录比赛</button><span className="workbench-counter">{project.playing?'下一步选择“这是我”，设置本人位置。':'不选择本人位置，按球员设置记录比赛。'}</span></div></>}
        {step===2&&<PlayerFrame project={project} duration={duration} active={active} onActive={setActive} onPlayer={updatePlayer} onFrame={requestFrame} onCapture={capture} onClear={slot=>{snapshotVersions.current[slot]=(snapshotVersions.current[slot]||0)+1;updatePlayer(slot,{box:null,snapshot:null,saving:false,error:''});}} disabled={busy}/>}
        {step===3&&<SegmentsEditor project={project} duration={duration} onChange={segments=>change(p=>({...p,segments}))} onError={setError} disabled={busy}/>}
        {step===4&&<><div className="workbench-card-head"><div><h2>把细节核对好，就可以保存了</h2><p>保存球员截图与比赛设置，不会自动生成逐拍数据。</p></div><span className="workbench-pill">最后一步</span></div><dl className="workbench-summary">{[['项目名称',project.title],['比赛日期',project.match_date||'未填写'],['原视频',selected?.title||'原片不可用'],['比赛类型',project.game_type==='singles'?'单打':'双打'],['本人位置',project.playing?slotsFor(project.game_type).find(s=>project.players[s].is_you)||'未选择':'不参赛'],['片段范围',project.segments.length?project.segments.length+' 个片段':'完整视频 · 近端先发球']].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><div style={{marginTop:22}}><Snapshots project={project}/></div>{project.segments.map((s,i)=><p className="workbench-tip" key={i}>片段 {i+1} · {clock(s.start)}–{clock(s.end)} · {s.first_server==='near'?'近端':'远端'}先发球 · 比分 {s.a_start??'—'}:{s.b_start??'—'} → {s.a_end??'—'}:{s.b_end??'—'}</p>)}{validateProject(project,videos).length>0&&<ul className="workbench-error-list">{validateProject(project,videos).map(issue=><li key={issue}>{issue}</li>)}</ul>}</>}
      </section><div className="workbench-footer"><button className="analysis-small-button" disabled={busy||step===0} onClick={()=>jump(step-1)}><ArrowLeft size={14}/>上一步</button><div className="workbench-actions">{!project.id&&<button className="analysis-small-button" disabled={busy} onClick={()=>ask('清空未保存的草稿？','只清除表单草稿，已上传的原视频会保留。',()=>{generation.current++;change(freshProject(options));setStep(0);setActive('A1');setNotice('草稿已清空。');})}>清空草稿</button>}<span className="workbench-counter">{snapshotsPending?'截图保存中…':`第 ${step+1} / 5 步`}</span><button className="purple-button" disabled={busy||(step===4&&validateProject(project,videos).length>0)} onClick={next}>{busy?<LoaderCircle size={15} className="animate-spin"/>:step===4?<Save size={15}/>:null}{step===4?(busy?'正在保存…':'保存项目'):'下一步'}{step!==4&&<ArrowRight size={14}/>}</button></div></div><datalist id="workbench-player-names">{options.map(p=><option key={p.name} value={p.name}/>)}</datalist></>:null}
    {dialog&&<Modal title={dialog.title} onClose={()=>setDialog(null)} actions={<><button className="analysis-small-button" onClick={()=>setDialog(null)}>取消</button><button className="purple-button" onClick={()=>{const action=dialog.action;setDialog(null);action();}}>确认</button></>}><p className="workbench-subtitle">{dialog.text}</p></Modal>}
  </main>;
}
