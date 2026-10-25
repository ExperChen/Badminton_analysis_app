"use strict";
(()=>{
  const $=id=>document.getElementById(id);
  const node=(tag,text,className)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(className)e.className=className;return e;};
  const notify=text=>{$('message').hidden=false;$('message').textContent=text;};
  const report=error=>notify(error.message||String(error));
  let users=[];
  async function api(url,body,method){const r=await fetch(url,{method:method||(body?'POST':'GET'),cache:'no-store',headers:body?{'Content-Type':'application/json','X-Yuxu-Local':'1'}:{},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'请求失败');return data;}
  function link(text,url){const e=node('a',text,'button');e.href=window.YuxuPath(url);return e;}
  function button(text,action){const e=node('button',text);e.type='button';e.onclick=async()=>{e.disabled=true;try{await action();}catch(error){report(error);}finally{e.disabled=false;}};return e;}
  function renderUsers(){
    $('users').replaceChildren();
    for(const user of users){
      const card=node('article',null,'user-card');
      card.append(node('strong',user.email),node('p',user.role==='admin'?'管理员':user.enabled?'用户 · 已启用':'用户 · 已停用'));
      if(user.role!=='admin')card.append(button(user.enabled?'停用账号':'启用账号',async()=>{await api('/api/admin/users/'+user.id+'/state',{enabled:!user.enabled});await refresh();}));
      card.append(button('签发密码重置码',async()=>{const result=await api('/api/admin/users/'+user.id+'/reset-token',{});notify('重置码（15 分钟内有效，请私下交给本人）：'+result.token);}));
      $('users').append(card);
    }
  }
  async function refreshUploads(){
    const data=await api('/api/admin/uploads');$('upload-total').textContent='共 '+data.total_videos+' 个视频';
    $('upload-groups').replaceChildren();
    for(const group of [...data.accounts,{email:'待认领的历史视频',videos:data.unassigned.videos}]){
      const section=node('details',null,'upload-group');section.append(node('summary',group.email+' · '+group.videos.length+' 个视频'));
      for(const video of group.videos){
        const item=node('article',null,'upload-video');item.append(node('strong',video.title),node('p',video.filename),node('small','视频 ID：'+video.session_id));
        for(const project of video.projects)item.append(link(project.title,'/tracker/projects/'+encodeURIComponent(project.id)));
        if(video.file_available)item.append(link('查看视频','/api/matches/'+encodeURIComponent(video.session_id)+'/video'));
        else item.append(node('p','原片暂不可用'));
        section.append(item);
      }
      $('upload-groups').append(section);
    }
  }
  async function refresh(){
    const [all,unassigned,audit]=await Promise.all([api('/api/admin/users'),api('/api/admin/unassigned'),api('/api/admin/audit')]);
    users=all;renderUsers();$('unassigned').replaceChildren();
    for(const resource of unassigned){
      const row=node('article',null,'resource-list');row.append(node('p',resource.title+' · '+resource.kind+' · '+resource.id));
      const select=node('select');for(const user of users.filter(u=>u.enabled)){const option=node('option',user.email);option.value=user.id;select.append(option);}
      row.append(select,button('认领',async()=>{const reason=$('reason').value.trim();if(reason.length<3)throw new Error('请填写至少三个字符的认领依据');await api('/api/admin/resources/'+resource.kind+'/'+encodeURIComponent(resource.id)+'/assign',{user_id:select.value,reason});await refresh();}));$('unassigned').append(row);
    }
    if(!unassigned.length)$('unassigned').append(node('p','没有待认领的数据'));
    $('audit').replaceChildren();for(const entry of audit)$('audit').append(node('p',new Date(entry.created*1000).toLocaleString()+' · '+entry.action+' · '+(entry.resource_id||entry.target_user||'')));
    await refreshUploads();
  }
  $('logout').onclick=async()=>{try{await api('/api/auth/logout',{});location.href=window.YuxuPath('/login');}catch(e){report(e);}};
  $('create-user').onsubmit=async event=>{event.preventDefault();const submit=event.submitter;submit.disabled=true;try{const user=await api('/api/admin/users',{email:$('new-user-email').value});notify('已创建 '+user.email+'。激活码（15 分钟内有效，请私下交给本人）：'+user.token);$('new-user-email').value='';await refresh();}catch(e){report(e);}finally{submit.disabled=false;}};
  $('refresh').onclick=()=>refresh().catch(report);$('refresh-uploads').onclick=()=>refreshUploads().catch(report);
  $('refresh-logs').onclick=async()=>{try{const data=await api('/api/admin/logs');$('backend-logs').textContent=data.lines.join('\n');}catch(e){report(e);}};
  window.YuxuAuthReady.then(async user=>{
    $('identity').textContent=user.email+' · '+(user.role==='admin'?'管理员':'用户');
    const quota=await api('/api/uploads/limits');const gib=n=>(n/1073741824).toFixed(1);
    $('upload-capacity').textContent='上传容量已用与预留 '+gib(quota.used_capacity_bytes)+' / '+gib(quota.capacity_bytes)+' GiB';
    if(user.role==='admin'){$('admin').hidden=false;await refresh();}
  }).catch(report);
})();
