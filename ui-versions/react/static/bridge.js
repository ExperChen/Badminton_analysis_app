"use strict";
(() => {
  const diagnostic=document.getElementById('admin-diagnostics');
  const labels={lookup_video:'核对原片',init_video:'准备视频',start_upload:'准备传输',upload_video:'上传原片',finalize_video:'确认上传',wait_video:'等待视频处理',upload_snapshots:'保存球员截图',setup:'保存比赛设置',create_project:'准备分析项目',estimate:'等待服务计算费用',await_confirmation:'等待费用确认',confirm_tracking:'提交分析请求',processing:'比赛分析中',complete:'数据已同步',uncertain:'核查处理结果',blocked:'处理已暂停'};
  const node=(tag,text,cls)=>{const item=document.createElement(tag);if(text!=null)item.textContent=text;if(cls)item.className=cls;return item;};
  function renderLibrary(){
    for(const flow of window.GGABConfirmation.status().workflows||[]){
      const action=document.querySelector('[data-handoff="'+CSS.escape(flow.project_id)+'"]');if(!action)continue;
      const summary=action.closest('.project-item')?.querySelector('[data-cost-summary]');if(summary)summary.textContent=window.GGABCosts.text(null,flow.estimated_cost)+(flow.quota?.needs_recharge?' · 额度不足，请充值':'');
      delete action.dataset.confirmCost;delete action.dataset.confirmRemote;action.title='';
      const ready=window.GGABConfirmation.view(flow.project_id).canConfirm;
      if(ready){action.dataset.confirmCost=String(flow.estimated_cost);action.dataset.confirmRemote=flow.remote_project_id;}
      action.textContent=ready?'确认分析 · '+flow.estimated_cost.toLocaleString()+' 分析额度 →':flow.stage==='await_confirmation'?'查看费用 →':flow.stage==='confirm_tracking'?'正在提交…':flow.stage==='complete'?'查看结果 →':'查看进度 →';
      if(ready)action.title='确认后预留服务报价，处理完成并核验真实费用后结算。';
    }
  }
  function renderDiagnostics(data){
    if(!diagnostic||window.GGABUser?.role!=='admin')return;
    const signature=JSON.stringify([data.workflows,data.video_checks,data.full_export,data.jobs]);
    if(diagnostic.dataset.signature===signature)return;diagnostic.dataset.signature=signature;
    diagnostic.replaceChildren(node('h3','任务诊断'),node('p','文件身份核验、传输进度和本地估算仅在此处显示。','hint'));
    const full=data.full_export||{};diagnostic.append(node('p',full.state==='exporting'?'比赛数据同步中':full.error?'同步未完成：'+window.AppText.error(full.error,true):full.sha256?`已同步 ${full.matches} 场比赛、${full.rows} 拍`:'等待比赛数据同步','hint'));
    if(!(data.workflows||[]).length)diagnostic.append(node('p','暂无处理任务。','hint'));
    for(const flow of data.workflows||[]){
      const card=node('article',null,'diagnostic-card'),head=node('div',null,'section-top'),anchor=node('a',flow.video_filename||flow.project_id,'text-link');anchor.href=window.GGABPath('/tracker/projects/'+encodeURIComponent(flow.project_id));head.append(anchor,node('span',labels[flow.stage]||'等待核查','status-badge'));card.append(head,node('small','项目编号：'+flow.project_id));
      if(flow.source_size>0){const meter=node('progress');meter.max=flow.source_size;meter.value=Math.min(flow.source_size,flow.upload_offset||0);meter.setAttribute('aria-label','原片上传进度');card.append(meter);}
      const checked=data.video_checks?.[flow.project_id];if(checked)card.append(node('p','视频核验：'+({ready:'通过',missing:'不可用',failed:'处理失败',mismatch:'不匹配',processing:'处理中',unknown:'待检测'}[checked.status]||'待检测')+(checked.reason?' · '+window.AppText.error(checked.reason,true):''),'hint'));
      const cost=node('div');window.GGABCosts.render(cost,flow.project_id,flow.cost_prediction,flow.estimated_cost,flow.quota,true);card.append(cost);
      const check=node('button','检测原片');check.type='button';check.onclick=async()=>{check.disabled=true;try{const response=await fetch('/api/bridge/projects/'+encodeURIComponent(flow.project_id)+'/check-video',{method:'POST',headers:{'X-GGAB-Local':'1'}});if(!response.ok)throw new Error('检测请求未完成');document.dispatchEvent(new Event('ggab-wake'));await refresh();}catch(error){check.disabled=false;card.append(node('p',window.AppText.error(error.message,true)));}};card.append(check);
      const detail=node('details'),heading=node('summary','查看关联证据与迁移资料'),body=node('pre','点击后读取资料');detail.append(heading,body);let loaded=false;
      detail.addEventListener('toggle',async()=>{if(!detail.open||loaded)return;loaded=true;try{const response=await fetch('/api/tracker/projects/'+encodeURIComponent(flow.project_id)+'/handoff',{cache:'no-store'}),bundle=await response.json();if(!response.ok)throw new Error(bundle.detail||'读取失败');body.textContent=JSON.stringify({project_id:flow.project_id,original_video:bundle.video,association:bundle.migration?.association,local_prediction:bundle.cost_prediction},null,2);const copy=node('button','复制后台迁移资料');copy.onclick=()=>navigator.clipboard.writeText(bundle.prompt||'').then(()=>{copy.textContent='已复制';}).catch(()=>{copy.textContent='复制未完成';});detail.append(copy);}catch(error){loaded=false;body.textContent=window.AppText.error(error.message,true);}});
      card.append(detail);if(flow.error)card.append(node('p',window.AppText.error(flow.error,true),'hint'));diagnostic.append(card);
    }
    const jobs=node('details'),summary=node('summary','后台作业记录');jobs.append(summary);
    for(const job of data.jobs||[])jobs.append(node('p',`${job.project_id} · ${({queued:'待处理',running:'处理中',done:'已完成',failed:'未完成'})[job.state]||'待核查'}${job.error?' · '+window.AppText.error(job.error,true):''}`,'hint'));
    diagnostic.append(jobs);
  }
  async function refresh(){
    try{const response=await fetch('/api/bridge/status',{cache:'no-store'});if(!response.ok)throw new Error('服务状态暂时不可用');const data=await response.json();window.GGABConfirmation.updateStatus(data);renderDiagnostics(data);}
    catch(error){window.GGABConfirmation.updateStatus({...window.GGABConfirmation.status(),connected:false});if(diagnostic){delete diagnostic.dataset.signature;diagnostic.replaceChildren(node('p',window.AppText.error(error.message,true)));}}
  }
  window.GGABConfirmation.subscribe(renderLibrary);document.addEventListener('ggab:library-rendered',renderLibrary);
  document.addEventListener('ggab-connected',event=>{if(event.detail==='ok')refresh();});
  window.GGABAuthReady.then(()=>{refresh();setInterval(()=>{if(!document.hidden)refresh();},10000);}).catch(()=>{});
})();
