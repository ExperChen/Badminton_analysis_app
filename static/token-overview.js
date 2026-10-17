"use strict";
(() => {
  const panel=document.getElementById('token-overview');
  if(!panel)return;
  const value=document.getElementById('token-overview-values');
  const status=document.getElementById('token-overview-status');
  const number=n=>Number.isSafeInteger(n)?n.toLocaleString():'尚未读取';
  const tokens=n=>number(n)+(Number.isSafeInteger(n)?' 分析额度':'');
  function metric(index,label,amount,note){
    const labelNode=document.getElementById('mini-token-label-'+index),valueNode=document.getElementById('mini-token-value-'+index),noteNode=document.getElementById('mini-token-note-'+index);
    if(labelNode)labelNode.textContent=label;if(valueNode)valueNode.textContent=Number.isSafeInteger(amount)?amount.toLocaleString():'—';if(noteNode)noteNode.textContent=note;
  }
  let user,refreshing;
  async function refresh(requestRemote=true) {
    if(!user || document.hidden)return;
    if(refreshing)return refreshing;
    if(requestRemote && user.role==='admin')document.dispatchEvent(new Event('ggab-token-sync'));
    refreshing=(async()=>{
      try {
        const response=await fetch(user.role==='admin'?'/api/admin/tokens':'/api/tokens/me',{cache:'no-store'});
        const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'额度读取失败');
        if(user.role==='admin') {
          const g=data.ggab,s=data.summary;
          value.textContent=`分析服务 剩余 ${tokens(g.total)} · 子账号已结算消耗 ${tokens(s.consumed)} · 子账号预留 ${tokens(s.reserved)}`;
          metric(0,'分析服务 真实余额',g.total,'共享账号 · 分析额度');metric(1,'子账号已结算消耗',s.consumed,'累计真实费用 · 分析额度');metric(2,'子账号预留额度',s.reserved,'尚未扣款 · 分析额度');
          status.textContent=`${g.observed_at?'余额读取于 '+new Date(g.observed_at*1000).toLocaleString():'等待后台扩展读取，请确认扩展已更新为 1.5.1、已连接且 分析服务 已登录'}${g.stale?' · 余额等待更新':''}${g.error?' · '+(window.AppText?.error(g.error)||'服务余额读取失败'):''}${g.activity_error?' · 扣费明细同步失败，未核验费用保留待结算':''}`;
        } else {
          value.textContent=`我的可用额度 ${number(data.available)} 分析额度 · 预留 ${number(data.reserved)} 分析额度 · 已结算消耗 ${number(data.consumed)} 分析额度`;
          metric(0,'我的可用额度',data.available,'管理员分配 · 分析额度');metric(1,'已结算消耗',data.consumed,'累计真实费用 · 分析额度');metric(2,'预留额度',data.reserved,'尚未扣款 · 分析额度');
          status.textContent=data.available<=0?'额度不足请联系管理员充值。':'结果完成并核验 分析服务 真实费用后结算。';
        }
      } catch(error) {status.textContent=window.AppText.error(error.message);}
    })().finally(()=>{refreshing=null;});
    return refreshing;
  }
  window.GGABAuthReady.then(identity=>{
    user=identity;panel.hidden=false;
    document.getElementById('token-overview-link').textContent=user.role==='admin'?'进入管理工作台 →':'查看我的额度与流水 →';
    const title=document.getElementById('token-overview-title');if(title)title.textContent=user.role==='admin'?'管理员额度概览':'我的分析额度';
    refresh();setInterval(refresh,30000);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
    document.addEventListener('ggab-token-synced',()=>{const current=refreshing;if(current)current.then(()=>refresh(false));else refresh(false);});
  }).catch(()=>{});
})();
