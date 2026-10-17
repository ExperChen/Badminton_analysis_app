import React,{useMemo,useState} from 'react';
import {ArrowDown,ArrowRight,Info} from 'lucide-react';
import Chart,{axis,palette,tooltip} from '../../components/Chart';
import {count,format,sequence,term} from '../../lib/api';

export function Card({title,note,children,id,action}) {
  return <section id={id} className="analysis-card"><header className="analysis-card-header"><div><h2>{title}</h2>{note&&<p>{note}</p>}</div>{action}</header>{children}</section>;
}
export function Progression({data,onRally}) {
  const {option,indexes}=useMemo(()=>{
    const x=[],a=[],b=[],indexes=[];
    for(const game of data.game_scores){
      const rallies=data.rallies.filter(r=>r.game===game.number);if(!rallies.length)continue;
      if(x.length){x.push('');a.push(null);b.push(null);indexes.push(null);}
      x.push(data.game_scores.length>1?`第${game.number}局 · 开局`:'开局');a.push(rallies[0].score_before[0]);b.push(rallies[0].score_before[1]);indexes.push(null);
      for(const rally of rallies){x.push(`第${rally.game}局 · 回合${rally.number}`);a.push(rally.score[0]);b.push(rally.score[1]);indexes.push(data.rallies.indexOf(rally));}
    }
    const names=['A','B'].map(team=>data.players.filter(p=>p.id?.startsWith(team)).map(p=>p.name).join(' / ')||team);
    return {indexes,option:{tooltip,legend:{top:3,left:14,textStyle:{color:'#907e9d',fontSize:11},icon:'roundRect',itemWidth:12,itemHeight:6},grid:{left:42,right:18,top:43,bottom:33},xAxis:{type:'category',data:x,...axis,boundaryGap:false},yAxis:{type:'value',...axis,minInterval:1},series:[{name:names[0],type:'line',data:a,symbolSize:5,showSymbol:false,lineStyle:{width:2.5},connectNulls:false},{name:names[1],type:'line',data:b,symbolSize:5,showSymbol:false,lineStyle:{width:2.5},connectNulls:false}]}};
  },[data]);
  return <Card title="比分走势" note="点击曲线上的回合，跳转到视频"><Chart id="progression" label="双方各回合结束后的比分走势" option={option} empty={!data.rallies.length} onClick={event=>{const i=indexes[event.dataIndex];if(i!=null)onRally(i);}}/></Card>;
}
export function Distribution({title,items,id,onHelp}) {
  const option=useMemo(()=>{
    const cats=[...new Set(items.flatMap(x=>Object.keys(x.detail||{})))],total=items.reduce((n,x)=>n+x.value,0);
    const series=cats.length?cats.map((name,i)=>({name:term(name),type:'bar',stack:'points',barMaxWidth:29,itemStyle:{color:palette[i%palette.length]},data:items.map(x=>Number(((x.detail[name]||0)*100/(total||1)).toFixed(1)))})):[{name:'占比',type:'bar',barMaxWidth:29,itemStyle:{color:palette[0],borderRadius:[4,4,0,0]},data:items.map(x=>x.percent)}];
    return {tooltip:{...tooltip,formatter:params=>{const item=items[params[0].dataIndex];return `${term(item.name)}  ${format(item.percent,'%')}\n${item.value} ${id==='usage'?'拍':'分'}${item.detail?'\n'+Object.entries(item.detail).map(([k,v])=>`${term(k)}：${v}`).join('\n'):''}`;}},grid:{left:43,right:15,top:20,bottom:57},xAxis:{type:'category',data:items.map(x=>term(x.name)),...axis,axisLabel:{...axis.axisLabel,interval:0,rotate:items.length>5?35:0}},yAxis:{type:'value',...axis,axisLabel:{...axis.axisLabel,formatter:'{value}%'}},series};
  },[items,id]);
  return <Card title={title} action={<button className="analysis-icon" type="button" aria-label={`${title}统计口径`} onClick={onHelp}><Info size={16}/></button>}><Chart id={id} label={`${title}，以所选范围为统计分母`} option={option} empty={!items.length}/></Card>;
}
export function OutcomeCharts({data,onRally}) {
  const outcomes=useMemo(()=>({tooltip:{trigger:'item',renderMode:'richText',formatter:'{b}\n{c} 个回合 · {d}%'},legend:{bottom:3,textStyle:{color:'#907e9d',fontSize:11}},title:{text:String(data.summary.wins),subtext:'赢分回合',left:'center',top:'31%',textStyle:{color:'#674b80',fontSize:31,fontWeight:500},subtextStyle:{color:'#9785a5',fontSize:11}},series:[{type:'pie',radius:['45%','65%'],center:['50%','44%'],label:{show:false},itemStyle:{borderColor:'#fff',borderWidth:4},data:data.outcomes.map(x=>({name:term(x.name),value:x.value}))}]}),[data]);
  const lengths=useMemo(()=>({tooltip,grid:{left:38,right:15,top:20,bottom:35},xAxis:{type:'category',data:data.rallies.map(r=>`第${r.game}局 · 回合${r.number}`),...axis},yAxis:{type:'value',...axis,minInterval:1},series:[{name:'拍数',type:'bar',barMaxWidth:20,data:data.rallies.map(r=>({value:r.length,itemStyle:{color:r.won===true?palette[0]:r.won===false?'#c5b8d6':'#e3dbe9'}}))}]}),[data]);
  return <div className="analysis-two"><Card title="赢分回合的结束方式" note="仅统计所选方赢下的回合"><Chart id="outcomes" label="赢分回合结束方式分布" option={outcomes} empty={!data.outcomes.length}/></Card><Card title="回合长度" note={`平均 ${format(data.summary.avg_shots)} 拍 · ${format(data.summary.avg_duration,' 秒')}`}><Chart id="length-chart" label="每个回合的击球拍数，点击可回放" option={lengths} empty={!data.rallies.length} onClick={event=>onRally(event.dataIndex)}/></Card></div>;
}
export function Patterns({data,size}) {
  const [mode,setMode]=useState('all'),[limit,setLimit]=useState(5),result=data.patterns[String(size)][mode];
  return <Card id={`patterns-${size}`} title={`${size} 拍开局组合`} note={`连续开局 ${size} 拍，共 ${count(result.eligible)} 个符合条件的回合`}><div className="analysis-pills">{[['all','全部回合'],['won','赢分回合'],['lost','输分回合']].map(([key,label])=><button key={key} aria-pressed={mode===key} className={mode===key?'selected':''} onClick={()=>{setMode(key);setLimit(5);}}>{label}</button>)}</div><ol className="react-pattern-list">{result.items.slice(0,limit).map((item,index)=><li key={index}><div>{item.sequence.map((s,i)=><React.Fragment key={i}>{i>0&&<ArrowRight size={12}/>}<span>{sequence(s)}</span></React.Fragment>)}</div><small>出现 {item.count} 次</small></li>)}</ol>{!result.items.length&&<p className="analysis-empty">暂无达到所需拍数的回合</p>}{result.items.length>limit&&<button className="analysis-text-button" onClick={()=>setLimit(limit+5)}>再显示五项<ArrowDown size={13}/></button>}</Card>;
}
const comparisonFields=[['rallies','回合数',''],['own_shots','所选方拍数',''],['wins','赢分回合',''],['losses','输分回合',''],['win_rate','回合胜率','%'],['avg_shots','平均回合拍数',''],['avg_duration','平均回合时长',' 秒']];
export function Comparison({left,right,leftTitle,rightTitle}) {
  return <Card id="comparison" title="比赛对比" note="两场完整比赛 · 相同观察方，不受单局筛选影响"><div className="analysis-table-scroll"><table className="analysis-table"><thead><tr><th>指标</th><th>{leftTitle}</th><th>{rightTitle}</th></tr></thead><tbody>{comparisonFields.map(([key,label,suffix])=><tr key={key}><td>{label}</td><td>{format(left.summary[key],suffix)}</td><td>{format(right.summary[key],suffix)}</td></tr>)}</tbody></table></div></Card>;
}
