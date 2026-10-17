"use strict";
(() => {
  const text=(_prediction,official)=>Number.isInteger(official)?`服务报价：${official.toLocaleString()} 分析额度`:'等待服务计算费用';
  const node=(tag,value)=>{const item=document.createElement(tag);item.textContent=value;return item;};
  function render(target,id,prediction,official,quota,diagnostic=false){
    if(!target)return;
    const admin=diagnostic&&window.GGABUser?.role==='admin';
    const signature=JSON.stringify([id,admin?prediction?.id:null,official,quota,admin]);
    if(target.dataset.costSignature===signature)return;
    target.dataset.costSignature=signature;target.replaceChildren(node('p',text(null,official)));
    if(quota?.uses_local_quota)target.append(node('p',quota.requires_assignment?'请联系管理员分配分析额度。':`我的可用额度：${quota.available?.toLocaleString()}${quota.needs_recharge?' · 额度不足，请联系管理员充值。':''}`));
    if(quota?.settlement){const hold=quota.settlement;target.append(node('p',hold.state==='settled'?`已按真实费用结算 ${hold.actual.toLocaleString()} 分析额度。`:hold.state==='released'?'预留额度已释放。':`已预留 ${hold.estimated.toLocaleString()} 分析额度，等待处理完成后核验真实费用。`));}
    if(!admin)return;
    const details=node('details',''),heading=node('summary',`后台本地估算：${Number.isInteger(prediction?.tokens)?prediction.tokens.toLocaleString():'尚未计算'} · 查看校准记录`),body=node('div','');
    details.append(heading,body);target.append(details);let loaded=false;
    details.addEventListener('toggle',async()=>{
      if(!details.open||loaded)return;loaded=true;body.textContent='正在读取后台费用记录…';
      try{const response=await fetch(window.GGABPath('/api/tracker/projects/'+encodeURIComponent(id)+'/cost-history'),{cache:'no-store'}),data=await response.json();if(!response.ok)throw new Error(data.detail||'记录读取失败');
        body.replaceChildren(node('p','本地估算仅用于后台校准，不用于用户报价、额度不足判断或扣款。'));
        if(data.current)body.append(node('p',`有效时长 ${data.current.features.effective_seconds.toFixed(3)} 秒 · ${data.current.model.sample_count} 个校准样本`));
        for(const quote of data.quotes)body.append(node('p',`${new Date(quote.observed_at).toLocaleString()} · 服务报价 ${quote.ggab_tokens.toLocaleString()} · 当时本地估算 ${Number.isInteger(quote.prediction_tokens)?quote.prediction_tokens.toLocaleString():'无记录'} · 估算误差 ${quote.prediction_error??'无记录'}`));
      }catch(error){loaded=false;body.textContent=window.AppText?.error(error.message)||'记录读取失败';}
    });
  }
  window.GGABCosts={text,render};
})();
