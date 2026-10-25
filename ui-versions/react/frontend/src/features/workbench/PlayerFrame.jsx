import React,{useEffect,useRef,useState} from 'react';
import {Check,LoaderCircle,RotateCw,RefreshCw,Trash2} from 'lucide-react';
import {path,errorText} from '../../lib/api';
import {clock,slotsFor} from './model';
export const colors={A1:'#bd82c2',A2:'#b8a25b',B1:'#65a6d5',B2:'#75ad92'};
export function Snapshots({project}){
  return <div className="tracker-snapshots">{slotsFor(project.game_type).map(slot=>{const p=project.players[slot];return <article className="tracker-snapshot" key={slot} style={{'--player-color':colors[slot]}}>{p.snapshot?<img src={path('/api/tracker/snapshots/'+encodeURIComponent(p.snapshot.id))} alt={slot+' '+p.name+' 截图'}/>:<div className="tracker-snapshot-empty">{p.saving?'正在保存…':'等待球员框选'}</div>}<div><strong>{slot} · {p.name||'未填写'}</strong><small>{p.saving?'保存中':p.snapshot?'✓ 截图已保存 · '+clock(p.time):p.error||'未保存截图'}</small></div></article>;})}</div>;
}
export default function PlayerFrame({project,duration,active,onActive,onPlayer,onFrame,onCapture,onClear,disabled}){
  const [source,setSource]=useState(''),[loading,setLoading]=useState(true),[ready,setReady]=useState(false),[error,setError]=useState(''),[retry,setRetry]=useState(0),[seconds,setSeconds]=useState(project.frame.time),[drawing,setDrawing]=useState(null);
  const image=useRef(null),draw=useRef(null),frame=project.frame,slots=slotsFor(project.game_type),p=project.players[active],max=Math.max(0,duration-.08);
  useEffect(()=>setSeconds(frame.time),[frame.time]);
  useEffect(()=>{
    const controller=new AbortController();let objectUrl;
    setLoading(true);setReady(false);setError('');draw.current=null;setDrawing(null);
    fetch(path('/api/tracker/frame/'+encodeURIComponent(project.session_id)+'?'+new URLSearchParams({time:frame.time,rotation:frame.rotation})),{signal:controller.signal,cache:'no-store'}).then(async r=>{
      if(!r.ok){const data=await r.json().catch(()=>({}));throw new Error(data.detail||'无法加载当前视频帧');}
      const blob=await r.blob();if(controller.signal.aborted)return;
      objectUrl=URL.createObjectURL(blob);setSource(objectUrl);
    }).catch(e=>{if(e.name!=='AbortError'){setError(errorText(e.message));setLoading(false);}});
    return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
  },[project.session_id,frame.time,frame.rotation,retry]);
  const point=e=>{const r=image.current.getBoundingClientRect();return {x:Math.min(1,Math.max(0,(e.clientX-r.left)/r.width)),y:Math.min(1,Math.max(0,(e.clientY-r.top)/r.height))};};
  const box=(a,b)=>({x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),width:Math.abs(a.x-b.x),height:Math.abs(a.y-b.y)});
  function commit(){const n=Number(seconds);if(!Number.isFinite(n)||n<0||n>max){setError('选帧时间超出原视频范围。');return;}setError('');onFrame(n,frame.rotation);}
  return <>
    <div className="workbench-card-head"><div><h2>在同一帧里，认识每位球员</h2><p>切换球员标签后拖拽选框，所有人的框会同时保留在画面中。</p></div><span className="workbench-pill">{slots.filter(s=>project.players[s].snapshot).length} / {slots.length} 已保存</span></div>
    <div className="tracker-player-tabs" role="group" aria-label="选择要标注的球员">{slots.map(s=>{const player=project.players[s];return <button className="tracker-player-tab" key={s} style={{'--player-color':colors[s]}} aria-pressed={active===s} onClick={()=>onActive(s)} disabled={disabled}><i/>{s} · {player.name||'未命名'}{player.saving?<LoaderCircle size={12} className="animate-spin"/>:player.snapshot?<Check size={12}/>:null}</button>;})}</div>
    <div className="tracker-player-form"><label className="workbench-field">球员名称<input list="workbench-player-names" maxLength={80} value={p.name} disabled={disabled} onChange={e=>onPlayer(active,{name:e.target.value})}/></label><label className="workbench-field">惯用手<select value={p.handedness} disabled={disabled} onChange={e=>onPlayer(active,{handedness:e.target.value})}><option value="right">右手</option><option value="left">左手</option></select></label><label className="workbench-inline-check"><input type="checkbox" checked={p.is_you} disabled={disabled||!project.playing} onChange={e=>onPlayer(active,{is_you:e.target.checked})}/>这是我</label></div>
    <div className="tracker-frame-controls"><input aria-label="所有球员共用选帧" type="range" min="0" max={max} step=".04" value={seconds} disabled={disabled} onChange={e=>setSeconds(e.target.value)} onPointerUp={commit} onKeyUp={e=>{if(e.key.startsWith('Arrow'))commit();}}/><input aria-label="选帧秒数" type="number" min="0" max={max} step=".04" value={seconds} disabled={disabled} onChange={e=>setSeconds(e.target.value)} onBlur={commit} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();commit();}}}/><span className="workbench-counter">{clock(frame.time)} / {clock(duration)}</span><button className="analysis-small-button" disabled={disabled} onClick={()=>onFrame(frame.time,(frame.rotation+90)%360)}><RotateCw size={14}/>旋转 90°</button></div>
    <div className="tracker-frame-shell" aria-busy={loading}><div className="tracker-frame-stage">{source&&<img id="frame-image" ref={image} src={source} alt="用于同时框选所有球员的视频帧" draggable={false} onLoad={()=>{setReady(true);setLoading(false);}} onError={()=>{setReady(false);setLoading(false);setError('视频帧无法显示，请重新加载。');}}/>}<svg id="frame-overlay" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-label="当前画面的球员选框"
      onPointerDown={e=>{if(!ready||loading||disabled||e.button!==0)return;e.preventDefault();draw.current={slot:active,origin:point(e)};e.currentTarget.setPointerCapture(e.pointerId);}}
      onPointerMove={e=>{if(draw.current)setDrawing({slot:draw.current.slot,box:box(draw.current.origin,point(e))});}}
      onPointerUp={e=>{if(!draw.current)return;const current=draw.current;draw.current=null;setDrawing(null);const result=box(current.origin,point(e));if(result.width>=.005&&result.height>=.005)onCapture(current.slot,result);}}
      onPointerCancel={()=>{draw.current=null;setDrawing(null);}}><g id="player-boxes">{slots.map(s=>{const player=project.players[s],same=Math.abs(player.time-frame.time)<.001&&player.rotation===frame.rotation,b=drawing?.slot===s?drawing.box:same?player.box:null;return b?<g key={s} data-player-box={s}><rect x={b.x*1000} y={b.y*1000} width={b.width*1000} height={b.height*1000} stroke={colors[s]} fill={colors[s]+'2b'}/><text x={b.x*1000+8} y={b.y*1000+25} fill="#fff" stroke="#40324e" strokeWidth=".8" paintOrder="stroke" fontSize="22">{s}</text></g>:null;})}</g></svg></div>{loading&&<div className="tracker-frame-veil"><LoaderCircle size={18} className="animate-spin"/>正在读取原视频帧…</div>}</div>
    <div className="workbench-toolbar"><p className="tracker-frame-status" role="status">{error||p.error||(p.saving?'正在保存 '+active+' 球员截图…':p.snapshot?'✓ '+active+' 截图已保存':'拖拽框选 '+active+'，松开后自动保存截图。')}</p><div className="workbench-actions"><button className="analysis-small-button" onClick={()=>setRetry(n=>n+1)} disabled={disabled||loading}><RefreshCw size={13}/>重新加载画面</button><button className="analysis-small-button" disabled={disabled||!p.box} onClick={()=>onClear(active)}><Trash2 size={13}/>清除选框</button>{p.error&&p.box&&<button className="analysis-small-button" onClick={()=>onCapture(active,p.box)} disabled={disabled||p.saving||!ready||loading}>重试保存截图</button>}</div></div>
    <Snapshots project={project}/><p className="workbench-tip">选框仅对应实际画面，不包含播放器黑边。重新选帧或旋转会清除所有选框和截图；球员名称与惯用手会保留。</p>
  </>;
}
