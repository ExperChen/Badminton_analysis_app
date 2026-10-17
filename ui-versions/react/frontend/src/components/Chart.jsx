import React,{useEffect,useRef,useState} from 'react';
import {useReducedMotion} from 'motion/react';
import {loadScript} from '../lib/api';

export const palette=['#9370b8','#45a597','#d9a454','#baa0d4','#729cce','#cc8ca7'];
export const axis={axisLine:{lineStyle:{color:'#e8e0f1'}},axisTick:{show:false},axisLabel:{color:'#907e9d',fontSize:11},splitLine:{lineStyle:{color:'#f1edf6'}}};
export const tooltip={trigger:'axis',renderMode:'richText',backgroundColor:'#fff',borderColor:'#e8e0f1',textStyle:{color:'#64516f',fontSize:12},confine:true};

export default function Chart({id,option,onClick,label,height=280,empty=false,emptyText='暂无符合条件的标注'}) {
  const ref=useRef(null),click=useRef(onClick),[error,setError]=useState(''),reduced=useReducedMotion();click.current=onClick;
  useEffect(()=>{
    if(empty)return;
    let disposed=false,instance,observer;
    setError('');
    (async()=>{
      try{
        if(!window.echarts)await loadScript('/static/vendor/echarts.min.js');
        if(disposed)return;
        instance=window.echarts.init(ref.current,null,{renderer:'svg'});
        instance.setOption({animation:!reduced,color:palette,textStyle:{fontFamily:'Segoe UI, Microsoft YaHei, sans-serif',color:'#907e9d',fontSize:11},aria:{enabled:true,label:{description:label}},...option});
        instance.on('click',event=>click.current?.(event));
        observer=new ResizeObserver(()=>instance?.resize());observer.observe(ref.current);
      }catch(e){if(!disposed)setError(e.message);}
    })();
    return()=>{disposed=true;observer?.disconnect();instance?.dispose();};
  },[option,empty,reduced,label]);
  return <div id={id} className="react-chart" style={{height}} role="img" aria-label={label}>{empty?<p>{emptyText}</p>:<><div ref={ref} className="react-chart-canvas"/>{error&&<p role="alert">图表暂时无法加载，请刷新重试。</p>}</>}</div>;
}
