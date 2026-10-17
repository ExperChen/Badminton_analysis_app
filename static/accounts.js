"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let users = [];
  let uploadBudgets = new Map();
  let uploads = null;
  let tokenAccounts = new Map();
  let tokenRefresh;
  const openVideoGroups=new Set();
  const tokenText = value => Number.isInteger(value) ? value.toLocaleString()+' 分析额度' : '尚未读取';
  async function refreshAdminTokens() {
    const data=await api('/api/admin/tokens');tokenAccounts=new Map(data.users.map(u=>[u.id,u]));
    $('child-consumed').textContent=data.summary.consumed.toLocaleString();
    $('child-reserved').textContent=data.summary.reserved.toLocaleString();
    $('active-users').textContent=data.users.filter(u=>u.enabled).length;
    $('child-token-summary').textContent=`累计结算 ${data.summary.settled_projects} 个项目`;
    $('nav-users').textContent=data.users.length;
    for(const u of data.users){const line=$('quota-'+u.id);if(line)renderQuota(line,u);}
    renderLedger($('admin-token-history'),data.transactions,true);
    await refreshBridge();
  }
  function refreshTokens() {
    if(tokenRefresh)return tokenRefresh;
    tokenRefresh=loadTokens().finally(()=>{tokenRefresh=null;});return tokenRefresh;
  }
  async function loadTokens() {
    const data=await api('/api/tokens/me');
    $('token-summary').textContent=data.uses_local_quota?'独立账号额度，随处理结果自动更新。':'共享账号的真实余额，以及子账号的额度使用情况。';
    $('token-note').textContent=data.uses_local_quota?'额度由管理员手动分配。提交时仅预留预计费用，分析服务 结果完成并核验真实费用后才扣款。额度不足请联系管理员充值。':'';
    renderLedger($('token-history'),data.transactions);
    if(data.uses_local_quota){$('personal-stats').hidden=false;for(const key of ['available','reserved','consumed'])$('personal-'+key).textContent=data[key].toLocaleString();}
    if(data.ggab){$('ggab-tokens').hidden=false;$('personal-history').hidden=true;const g=data.ggab;
      $('ggab-token-summary').textContent=Number.isInteger(g.total)?g.total.toLocaleString():'—';
      badge($('ggab-balance-state'),g.stale?'待同步':'已同步',!g.stale);
      $('ggab-sync-dot').classList.toggle('is-live',!g.stale);
      $('ggab-sync-note').textContent=g.observed_at?`最近同步 ${new Date(g.observed_at*1000).toLocaleString()}${g.stale?' · 等待更新':''}${g.error?' · '+g.error:''}${g.activity_error?' · 扣费明细读取失败，费用保留待结算':''}`:(g.error||'等待后台扩展读取真实余额');
      await refreshAdminTokens();}
    return data;
  }
  async function api(path, body) {
    const r = await fetch(path,{method:body ? 'POST':'GET',headers:body ? {'Content-Type':'application/json'}:{},body:body ? JSON.stringify(body):undefined,cache:'no-store'});
    const data = await r.json(); if(!r.ok)throw new Error(typeof data.detail==='string' ? data.detail:'请求失败'); return data;
  }
  function notify(text) {$('message').textContent=text;$('message').hidden=false;}
  function report(error) {notify(window.AppText.error(error.message));}
  function row(text) {const div=document.createElement('div');div.className='account-row';const span=document.createElement('span');span.textContent=text;div.append(span);return div;}
  function node(tag, text, className) {const value=document.createElement(tag);if(text!=null)value.textContent=text;if(className)value.className=className;return value;}
  function link(text, path) {const value=node('a',text,'button');value.href=window.GGABPath(path);value.target='_blank';value.rel='noopener';return value;}
  function duration(seconds) {if(seconds==null||!Number.isFinite(Number(seconds)))return '时长未知';const total=Math.round(Number(seconds));return `${Math.floor(total/60)}:${String(total%60).padStart(2,'0')}`;}
  function date(value) {const parsed=new Date(value);return value&&!Number.isNaN(parsed.getTime())?parsed.toLocaleString():'上传时间未知';}
  function badge(target,text,live){target.textContent=text;target.className='status-badge '+(live?'is-live':'is-pending');}
  function empty(target,title,description){const block=node('div',null,'empty-state');block.append(node('strong',title),node('p',description));target.replaceChildren(block);}
  function renderQuota(target,q){target.replaceChildren();for(const [label,value] of [['可用额度',q.available],['余额',q.balance],['预留额度',q.reserved],['已结算消耗',q.consumed]]){const metric=node('div');metric.append(node('span',label),node('strong',Number.isInteger(value)?value.toLocaleString():'—'));target.append(metric);}}
  function renderLedger(target,items,withOwner=false){
    if(!items.length)return empty(target,'暂无额度流水','分配额度或完成真实结算后，记录会显示在这里。');
    const table=node('table',null,'ledger-table'),head=node('thead'),header=node('tr'),body=node('tbody');
    for(const label of ['时间',...(withOwner?['账号']:[]),'额度数量','理由 / 备注'])header.append(node('th',label));head.append(header);
    for(const item of items){const line=node('tr');line.append(node('td',new Date(item.created*1000).toLocaleString()));if(withOwner)line.append(node('td',tokenAccounts.get(item.user_id)?.email||item.user_id));const amount=node('td',(item.amount>0?'+':'')+item.amount.toLocaleString(),'amount '+(item.amount>0?'credit':item.amount<0?'debit':'')),note=node('td',item.kind==='settlement'?'自动跟踪比赛':item.note||'管理员发放');if(item.kind==='settlement'&&item.note)note.append(node('small','已按服务核验的真实费用结算'));if(item.project_id)note.append(node('small','项目 '+item.project_id));line.append(amount,note);body.append(line);}
    table.append(head,body);target.replaceChildren(table);
  }
  async function refreshBridge(){
    try{const b=await api('/api/bridge/status'),v=(b.extension_version||'').split('.').map(Number),current=v[0]>1||(v[0]===1&&(v[1]>5||(v[1]===5&&v[2]>=1))),live=b.connected&&b.source_verified&&current;
      const label=live?'已连接':b.extension_version&&!current?'扩展需要更新':'等待连接';badge($('connection-status'),label,live);$('rail-connection-state').textContent=label;$('rail-connection-dot').classList.toggle('is-live',live);
      $('source-state').textContent=b.source_verified?'共享 分析服务 身份已核验':'尚未核验共享 分析服务 账号';$('extension-version').textContent=b.extension_version||'尚未连接';$('extension-last-seen').textContent=b.last_seen?new Date(b.last_seen*1000).toLocaleString():'尚无连接记录';
    }catch(error){badge($('connection-status'),'检查未完成',false);$('rail-connection-state').textContent='检查未完成';}
  }
  function showView(id,updateHash=true){
    if(window.GGABUser?.role!=='admin')return;
    const panel=$(id);if(!panel?.matches('[data-admin-panel]'))id='overview';
    document.querySelectorAll('[data-admin-panel]').forEach(p=>{p.hidden=p.id!==id;});
    document.querySelectorAll('[data-admin-view]').forEach(a=>{if(a.dataset.adminView===id)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
    if(updateHash)history.replaceState(null,'','#'+id);
  }
  document.querySelectorAll('[data-admin-view]').forEach(a=>a.addEventListener('click',event=>{if(window.GGABUser?.role==='admin'){event.preventDefault();showView(a.dataset.adminView);}}));
  window.addEventListener('hashchange',()=>showView(location.hash.slice(1),false));
  function projectState(project) {
    const labels={local:'仅本地',lookup_video:'核对远端视频',init_video:'准备上传',start_upload:'开始上传',upload_video:'上传视频中',finalize_video:'确认视频上传',wait_video:'远端视频处理中',upload_snapshots:'上传截图中',setup:'设置球员中',create_project:'创建远端项目',estimate:'读取费用',await_confirmation:'等待费用确认',confirm_tracking:'提交跟踪中',processing:'分析服务 处理中',complete:'跟踪完成',blocked:'任务暂停',failed:'处理失败',review:'待确认',finalized:'跟踪完成'};
    labels.uncertain='远端结果待核查';
    const status=project.workflow_stage||project.migration_status||'local';
    return labels[status]||'状态待核查';
  }
  function renderUploads() {
    if(!uploads)return;
    const container=$('upload-groups'),query=$('upload-search').value.trim().toLocaleLowerCase(),selected=$('upload-account').value;
    container.replaceChildren();let shown=0,groupCount=0;
    const groups=[...uploads.accounts,{id:'unassigned',email:'待认领的历史视频',videos:uploads.unassigned.videos,video_count:uploads.unassigned.video_count}];
    for(const group of groups) {
      if(selected&&selected!==group.id)continue;
      const videos=group.videos.filter(video=>[group.email,video.title,video.filename,video.session_id,...video.projects.flatMap(p=>[p.title,p.id,p.remote_match_id])].filter(Boolean).join(' ').toLocaleLowerCase().includes(query));
      if(query&&!videos.length)continue;
      if(group.id==='unassigned'&&!group.video_count&&!selected)continue;
      groupCount++;shown+=videos.length;
      const section=node('details',null,'upload-group'+(group.id==='unassigned'?' upload-unassigned':''));section.open=Boolean(query||selected||openVideoGroups.has(group.id));
      section.addEventListener('toggle',()=>{if(section.open)openVideoGroups.add(group.id);else openVideoGroups.delete(group.id);});
      const summary=node('summary');summary.append(node('strong',group.email),node('span',`${videos.length}${videos.length!==group.video_count?' / '+group.video_count:''} 个视频`,'upload-count'));
      if(group.id!=='unassigned')summary.append(node('span',group.role==='admin'?'管理员':group.enabled?'本地用户':'已停用','upload-role'));
      section.append(summary);
      if(!videos.length)section.append(node('p','尚未上传视频','upload-empty'));
      for(const video of videos) {
        const item=node('article',null,'upload-video');
        const info=node('div',null,'upload-info');info.append(node('h3',video.title),node('p',video.filename,'upload-filename'),node('p',`${duration(video.duration)} · ${date(video.uploaded_at)}`,'upload-meta'),node('p','视频 ID：'+video.session_id,'upload-meta upload-id'));
        const projects=node('div',null,'upload-projects');
        if(!video.projects.length)projects.append(node('p','未创建项目','upload-meta'));
        for(const project of video.projects) {
          const detail=node('div',null,'upload-project');const anchor=link(project.title,'/tracker/projects/'+encodeURIComponent(project.id));anchor.className='upload-project-link';detail.append(anchor,node('span',projectState(project),'upload-status'));
          if(project.remote_match_id)detail.append(node('small','分析服务 比赛 ID：'+project.remote_match_id));
          projects.append(detail);
        }
        const actions=node('div',null,'upload-actions');
        if(video.file_available)actions.append(link('查看视频','/api/matches/'+encodeURIComponent(video.session_id)+'/video'));
        else actions.append(node('span','原视频文件不可用','upload-warning'));
        item.append(info,projects,actions);section.append(item);
      }
      container.append(section);
    }
    $('upload-results').textContent=`当前显示 ${shown} 个视频 · ${groupCount} 个分组`;
    if(!groupCount)container.append(node('p','没有符合筛选条件的视频','upload-empty'));
  }
  async function refreshUploads() {
    const button=$('refresh-uploads');button.disabled=true;
    try {
      uploads=await api('/api/admin/uploads');
      $('upload-total').textContent=uploads.total_videos;
      $('upload-users').textContent=uploads.accounts.filter(group=>group.video_count>0).length;
      $('upload-pending').textContent=uploads.unassigned.video_count;
      $('nav-videos').textContent=uploads.total_videos;
      const select=$('upload-account'),selected=select.value;select.replaceChildren();
      for(const group of [{id:'',email:'全部账号'},...uploads.accounts,{id:'unassigned',email:'待认领的历史视频'}]) {const option=node('option',group.email);option.value=group.id;select.append(option);}
      if([...select.options].some(option=>option.value===selected))select.value=selected;
      renderUploads();
    } finally {button.disabled=false;}
  }
  function filterUsers(){const query=$('user-search').value.trim().toLocaleLowerCase();let shown=0;document.querySelectorAll('#users .user-card').forEach(card=>{card.hidden=!card.dataset.email.includes(query);if(!card.hidden)shown++;});$('users-empty').hidden=shown>0;}
  function renderUsers(){
    $('users').replaceChildren();$('admin-account-actions').replaceChildren();
    const children=users.filter(u=>u.role!=='admin');$('user-count').textContent=children.length+' 个子账号';
    for(const u of users){
      const card=node('article',null,'user-card'+(u.role==='admin'?' admin-user':''));card.dataset.email=u.email.toLocaleLowerCase();
      const header=node('div',null,'user-header'),identity=node('div',null,'user-identity'),state=node('span');badge(state,u.role==='admin'?'管理员':u.enabled?'已启用':'已停用',u.enabled);
      identity.append(node('strong',u.email),node('small',u.role==='admin'?'管理员账号':'独立 分析额度 额度'));
      const actions=node('div',null,'user-actions'),more=node('details',null,'user-more'),menu=node('div');more.append(node('summary','更多 ···'),menu);
      if(u.role!=='admin'){
        const toggle=node('button',u.enabled?'停用账号':'启用账号');toggle.onclick=()=>api(`/api/admin/users/${u.id}/state`,{enabled:!u.enabled}).then(refresh).catch(report);menu.append(toggle);
      }
      const reset=node('button','签发密码重置码');reset.onclick=async()=>{try{const r=await api(`/api/admin/users/${u.id}/reset-token`,{});notify('重置码（15 分钟内有效，请私下交给本人）：'+r.token);more.open=false;}catch(error){report(error);}};menu.append(reset,node('p','重置码仅用于设置本地密码，与 分析额度 额度无关。'));
      actions.append(more);header.append(node('span',u.email[0].toUpperCase(),'user-avatar'),identity,state,actions);card.append(header);
      if(u.role==='admin'){$('admin-account-actions').append(card);continue;}
      const quota=node('div',null,'quota-values');quota.id='quota-'+u.id;renderQuota(quota,tokenAccounts.get(u.id)||{});card.append(quota);
      const budget=uploadBudgets.get(u.id);if(budget)card.append(node('p',budgetText(budget),'hint'));
      const form=node('form',null,'token-allocation');form.id='allocation-'+u.id;form.hidden=true;
      const amount=node('input');amount.type='number';amount.min='1';amount.max='1000000000';amount.step='1';amount.required=true;amount.placeholder='例如 10000';amount.id='amount-'+u.id;amount.setAttribute('aria-label',u.email+' 增加 分析额度 数量');
      const reason=node('select');reason.id='allocation-reason-'+u.id;reason.setAttribute('aria-label',u.email+' 分配理由');for(const text of ['充值','管理员发放','其他']){const option=node('option',text);option.value=text;reason.append(option);}reason.value='管理员发放';
      const note=node('input');note.maxLength=280;note.placeholder='可补充说明，例如 本次补充额度';note.id='note-'+u.id;note.setAttribute('aria-label',u.email+' 分配备注');
      const amountField=node('div'),amountLabel=node('label','本次增加数量');amountLabel.htmlFor=amount.id;amountField.append(amountLabel,amount);
      const reasonField=node('div'),reasonLabel=node('label','分配理由');reasonLabel.htmlFor=reason.id;reasonField.append(reasonLabel,reason);
      const noteField=node('div',null,'allocation-note'),noteLabel=node('label','备注（可选）');noteLabel.htmlFor=note.id;noteField.append(noteLabel,note);
      reason.onchange=()=>{note.required=reason.value==='其他';noteLabel.textContent=note.required?'自定义理由（必填）':'备注（可选）';note.placeholder=note.required?'填写本次增加额度的理由':'可补充说明，例如 本次补充额度';};
      const grant=node('button','确认分配','primary');grant.type='submit';let allocation=null;
      form.onsubmit=async event=>{event.preventDefault();try{const value=Number(amount.value),detail=note.value.trim();if(!Number.isSafeInteger(value)||value<1)throw new Error('请输入正整数 分析额度 数量');if(reason.value==='其他'&&!detail)throw new Error('请填写自定义分配理由');const allocationNote=reason.value==='其他'?detail:reason.value+(detail?' · '+detail:'');if(!allocation||allocation.amount!==value||allocation.note!==allocationNote)allocation={amount:value,note:allocationNote,request_id:crypto.randomUUID()};grant.disabled=true;await api(`/api/admin/users/${u.id}/tokens`,allocation);notify(`已为 ${u.email} 增加 ${tokenText(value)}`);amount.value='';note.value='';allocation=null;await Promise.all([refresh(),refreshTokens()]);}catch(error){report(error);grant.disabled=false;}};
      form.append(amountField,reasonField,grant,noteField,node('p','所选理由与备注会保存到额度流水。按输入数量增加本地额度，不会修改真实 分析服务 余额。','allocation-caption'));card.append(form);
      const open=node('button','分配额度','primary');open.setAttribute('aria-expanded','false');open.setAttribute('aria-controls',form.id);open.onclick=()=>{form.hidden=!form.hidden;open.textContent=form.hidden?'分配额度':'收起分配';open.setAttribute('aria-expanded',String(!form.hidden));if(!form.hidden)amount.focus();};actions.prepend(open);
      $('users').append(card);
    }
    filterUsers();
  }
  async function refresh() {
    await refreshUploads();
    await refreshAdminTokens();
    uploadBudgets=new Map((await api('/api/admin/upload-limits')).map(u=>[u.id,u]));
    users=await api('/api/admin/users');renderUsers();
    const items=await api('/api/admin/unassigned');$('unassigned').replaceChildren();
    for(const item of items) {
      const div=row(`${{project:'项目',match:'比赛',session:'视频'}[item.kind]} · ${item.title}`);div.firstChild.append(node('small','ID '+item.id));
      const select=document.createElement('select');
      select.setAttribute('aria-label',item.title+' 的所有者');
      for(const u of users.filter(u=>u.enabled)) {const option=document.createElement('option');option.value=u.id;option.textContent=u.email;select.append(option);}
      const assign=document.createElement('button');assign.textContent='核验后认领';assign.onclick=async()=>{try{const reason=$('reason').value.trim();if(reason.length<3)throw new Error('请先填写至少三个字的认领依据');assign.disabled=true;await api(`/api/admin/resources/${item.kind}/${encodeURIComponent(item.id)}/assign`,{user_id:select.value,reason});notify('认领已保存');await refresh();}catch(e){report(e);assign.disabled=false;}};div.append(select,assign);$('unassigned').append(div);
    }
    $('nav-unassigned').textContent=items.length;if(!items.length)empty($('unassigned'),'所有数据已归属','暂无待认领的历史数据。');
    const events=await api('/api/admin/audit');$('audit').replaceChildren();const emails=new Map(users.map(u=>[u.id,u.email]));
    for(const e of events){const entry=node('div',null,'audit-entry'),label={assign:'数据认领',bind_match:'关联比赛',enable:'启用账号',suspend:'停用账号',reset_token:'签发重置码',create_user:'创建子账号'}[e.action]||e.action,content=node('div');content.append(node('strong',emails.get(e.target_user)||'归属操作'));if(e.resource_id)content.append(node('small',`${{project:'项目',match:'比赛',session:'视频'}[e.kind]||e.kind} · ${e.resource_id}`));if(e.reason)content.append(node('p',e.reason));entry.append(node('span',label,'audit-action'),content,node('time',new Date(e.created*1000).toLocaleString()));$('audit').append(entry);}
    if(!events.length)empty($('audit'),'暂无操作记录','账号和数据归属操作会记录在这里。');
  }
  $('refresh-logs').onclick=async()=>{try{const data=await api('/api/admin/logs');$('backend-logs').textContent=data.lines.length?data.lines.join('\n'):'暂无后台日志';}catch(error){report(error);}};
  $('logout').onclick=async()=>{try{await api('/api/auth/logout',{});location.replace(window.GGABPath('/login'));}catch(e){report(e);}};
  $('refresh').onclick=()=>refresh().catch(report);
  function requestTokenSync(){if(window.GGABUser?.role==='admin')document.dispatchEvent(new Event('ggab-token-sync'));}
  $('refresh-tokens').onclick=()=>{requestTokenSync();refreshTokens().catch(report);};
  document.addEventListener('ggab-token-synced',()=>{const current=tokenRefresh;if(current)current.then(()=>refreshTokens()).catch(report);else refreshTokens().catch(report);});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){requestTokenSync();refreshTokens().catch(report);}});
  $('refresh-uploads').onclick=()=>refreshUploads().catch(report);
  $('upload-account').onchange=renderUploads;
  $('upload-search').oninput=renderUploads;
  $('user-search').oninput=filterUsers;
  function budgetText(q){const gib=n=>(n/1073741824).toFixed(1);return `上传容量已用与预留 ${gib(q.used_capacity_bytes)} / ${gib(q.capacity_bytes)} GiB · 最近 24 小时 ${gib(q.used_daily_bytes)} / ${gib(q.daily_bytes)} GiB，${q.used_daily_files} / ${q.daily_files} 次上传`;}
  $('create-user').onsubmit=async event=>{event.preventDefault();const button=$('create-user-submit');button.disabled=true;try{const u=await api('/api/admin/users',{email:$('new-user-email').value});$('new-user-email').value='';notify(`已创建 ${u.email}。一次性激活码（15 分钟内有效，请私下交给本人）：${u.token}`);await refresh();}catch(error){report(error);}finally{button.disabled=false;}};
  $('pair').onclick=()=>{ notify('正在连接扩展并核验 分析服务 身份…');document.dispatchEvent(new Event('ggab-connect'));};
  document.addEventListener('ggab-connected',e=>{notify(e.detail==='ok'?'后台扩展已连接':'连接失败，请确认扩展已更新为 1.5.1，且 分析服务 已登录正确账号');refreshTokens().catch(report);});
  window.GGABAuthReady.then(async user=>{
    $('upload-capacity').textContent=budgetText(await api('/api/uploads/limits'));
    $('identity').textContent=user.email+' · '+(user.role==='admin'?'管理员':'本地用户');
    if(user.role==='admin'){document.title=user.email+' · 管理工作台 · 羽球分析';document.body.classList.add('admin-console');$('admin').hidden=false;$('admin-rail').hidden=false;$('account-title').textContent='管理工作台';$('account-eyebrow').textContent='管理员工作台';$('token-title').textContent='额度总览';showView(location.hash.slice(1)||'overview',false);}
    requestTokenSync();await refreshTokens();setInterval(()=>{if(!document.hidden){requestTokenSync();refreshTokens().catch(report);}},30000);
    if(user.role==='admin')await refresh();
  }).catch(report);
})();
