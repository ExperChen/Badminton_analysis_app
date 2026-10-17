import React,{useMemo,useState} from 'react';
import Chart,{axis,palette,tooltip} from '../../components/Chart';
import {term} from '../../lib/api';
import {Card} from './Statistics';

const heightKeys=['above_tape','above_net_midline','below_net_midline','below_net'];
const errorKeys=['into_the_net','out_the_back','out_on_sides','unknown'];
const facets=[['striking_side','side','击球侧'],['direction','direction','击球线路'],['shot_variant','variant','击球变体']];
function Filters({data,chosen,onChange}) {
  const active=Object.values(chosen).some(values=>values.length);
  return <div className="annotation-filters">{facets.map(([field,recordKey,label])=><fieldset key={field}><legend>{label}</legend><div>{data.facets[field].map(value=><label key={value} className={chosen[recordKey]?.includes(value)?'selected':''}><input type="checkbox" checked={Boolean(chosen[recordKey]?.includes(value))} onChange={event=>onChange({...chosen,[recordKey]:event.target.checked?[...(chosen[recordKey]||[]),value]:(chosen[recordKey]||[]).filter(x=>x!==value)})}/>{term(value,field)}</label>)}</div></fieldset>)}{active&&<button className="analysis-text-button" onClick={()=>onChange({})}>清空筛选</button>}</div>;
}
function filterRecords(records,chosen) {
  return records.filter(r=>Object.entries(chosen).every(([key,values])=>!values.length||values.includes(r[key])));
}
function StackedChart({id,data,records,keys,label,onDrill}) {
  const {option,groups}=useMemo(()=>{
    const players=[...new Set(data.players.map(p=>p.name))],families=[...new Set(records.map(r=>r.family))];
    const groups=families.flatMap(family=>players.map(player=>({family,player})));
    const series=keys.map((key,i)=>({name:term(key),type:'bar',stack:'tags',barMaxWidth:32,itemStyle:{color:palette[i]},data:groups.map(g=>records.filter(r=>r.player===g.player&&r.family===g.family&&r.category===key).length)}));
    return {groups,option:{tooltip:{...tooltip,formatter:params=>{const g=groups[params[0].dataIndex];return `${g.player} · ${term(g.family)}\n`+params.filter(p=>p.value).map(p=>`${p.seriesName}：${p.value}`).join('\n');}},legend:{top:3,textStyle:{color:'#907e9d',fontSize:11},itemWidth:10,itemHeight:10},grid:{left:40,right:18,top:58,bottom:groups.length>10?82:62},xAxis:{type:'category',data:groups.map(g=>`${term(g.family)}\n${g.player}`),...axis,axisLabel:{...axis.axisLabel,fontSize:10,interval:0,rotate:groups.length>10?30:0}},yAxis:{type:'value',...axis,minInterval:1},dataZoom:groups.length>14?[{type:'inside',start:0,end:65}]:[],series}};
  },[data,records,keys]);
  return <Chart id={id} label={label} height={330} option={option} empty={!records.length} onClick={event=>{const group=groups[event.dataIndex];if(group)onDrill({title:`${group.player} · ${term(group.family)}`,records:records.filter(r=>r.player===group.player&&r.family===group.family)});}}/>;
}
function HeightSummary({data,records}) {
  const players=[...new Set(data.players.map(p=>p.name))];
  return <div className="height-summary-grid">{players.map(player=>{
    const tagged=records.filter(r=>r.player===player),total=tagged.length,counts=heightKeys.map(key=>tagged.filter(r=>r.category===key).length);
    return <article key={player} className="height-summary"><header><h3>{player}</h3><span>{total} 拍已标注</span></header><div className="height-summary-strip">{counts.map((n,i)=>n>0&&<span key={heightKeys[i]} title={`${term(heightKeys[i])}：${n}`} style={{width:`${n/total*100}%`,background:palette[i]}}/>)}</div><ul>{counts.map((n,i)=><li key={heightKeys[i]}><span><i style={{background:palette[i]}}/>{term(heightKeys[i])}</span><strong>{total?`${Math.round(n/total*100)}%`:'—'}<small> · {n} 拍</small></strong></li>)}</ul></article>;
  })}</div>;
}
export default function Annotations({data,type,onDrill}) {
  const [chosen,setChosen]=useState({}),height=type==='height';
  const records=useMemo(()=>filterRecords(height?data.heights:data.errors,chosen),[data,chosen,height]);
  const replies=useMemo(()=>filterRecords(data.replies,chosen),[data,chosen]);
  return <div className="analysis-annotations"><Card id={height?'contact':'errors'} title={height?'触球高度':'失误分析'} note={height?'比较双方的触球高度与击球后对方的回球高度。':'按失误技术和落点查看双方分布。'}><Filters data={data} chosen={chosen} onChange={setChosen}/><p className="annotation-count">当前筛选 {records.length} 条标注 · 点击柱形查看对应击球</p><StackedChart id={height?'height-chart':'error-chart'} data={data} records={records} keys={height?heightKeys:errorKeys} label={height?'各项技术的触球高度分布':'各项技术的失误落点分布'} onDrill={onDrill}/></Card>{height&&<><Card title="整体触球高度" note="只使用已有标注，缺失值不会推测"><HeightSummary data={data} records={records}/></Card><Card title="对方回球时的触球高度" note="仅统计同回合内紧接的对方一拍；抹扑与扑球按网带以上计入"><StackedChart id="reply-chart" data={data} records={replies} keys={heightKeys} label="击球后对方回球高度分布" onDrill={onDrill}/></Card></>}</div>;
}
