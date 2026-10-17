"use strict";
(() => {
  const sidebar=document.getElementById('sidebar');
  const page=document.getElementById('tokens-page');
  if(!sidebar&&!page)return;
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  const number=n=>Number.isSafeInteger(n)?n.toLocaleString():'—';
  let user,pending,card,amount,metrics,footer;
  function makeCard(){
    card=node('a',null,'ggab-token-card');card.href=window.GGABPath('/account/tokens');card.setAttribute('aria-label','分析额度 · 查看我的余额与流水');
    const heading=node('div',null,'token-card-heading');heading.append(node('span','◉ 分析额度'),amount=node('strong','—'));
    metrics=node('div',null,'token-card-metrics');footer=node('p','正在读取…','token-card-footer');footer.setAttribute('role','status');
    card.append(heading,metrics,footer);sidebar.append(card);sidebar.classList.add('has-token-card');
  }
  function render(data){
    const admin=user.role==='admin',g=data.ggab||{},s=data.summary||{};
    const total=admin?g.total:data.available;
    const rows=admin?[['服务余额',g.total,'共享账号余额'],['预留',s.reserved,'子账号预留'],['已消耗',s.consumed,'子账号已结算消耗']]:
      [['总额度',data.balance,'我的总额度'],['预留',data.reserved,'处理中，尚未结算'],['已消耗',data.consumed,'已核验真实费用']];
    const note=admin?(g.error&& (window.AppText?.error(g.error)||'服务余额读取失败')|| (g.stale?'余额等待更新':'分析服务 余额已同步')):(data.available<=0?'额度不足 · 联系管理员充值':'我的可用额度 · 查看流水 →');
    if(card){amount.textContent=number(total);card.setAttribute('aria-label',`分析额度 · ${number(total)} · ${admin?'分析服务 余额':'我的可用额度'} · 查看余额与流水`);metrics.replaceChildren(...rows.map(([label,value])=>{const n=node('div');n.append(node('span',label),node('strong',number(value)));return n;}));footer.textContent=note;card.dataset.state=admin&&g.stale?'stale':'ready';}
    if(!page)return;
    document.getElementById('tokens-current').textContent=number(total);
    document.getElementById('tokens-source').textContent=admin?'共享 分析服务 账号的真实余额。':'你的本地可用额度，由管理员分配；不代表共享 分析服务 账号的总余额。';
    document.getElementById('tokens-breakdown').replaceChildren(...rows.map(([label,value,description])=>{const n=node('div');n.append(node('span',label),node('strong',number(value)),node('p',description));return n;}));
    document.getElementById('tokens-status').textContent=note;
    const history=document.getElementById('tokens-activity');history.replaceChildren();
    const items=data.transactions||[];
    for(const item of items){
      const row=node('li',null,'token-activity-row');const copy=node('div');
      const at=node('time',new Date(item.created*1000).toLocaleString());
      const label=item.kind==='allocation'?(item.note||'管理员发放'):item.kind==='settlement'?'自动跟踪比赛':'额度变动';
      copy.append(at,node('p',label));
      if(item.kind==='allocation')copy.append(node('small','额度增加'));
      else if(item.note)copy.append(node('small',item.kind==='settlement'?'已按服务核验的真实费用结算':item.note));
      if(admin&&item.user_id){const owner=(data.users||[]).find(u=>u.id===item.user_id);copy.append(node('small',owner?.email||item.user_id));}
      if(item.project_id){const link=node('a','查看本地项目 →');link.href=window.GGABPath('/tracker/projects/'+encodeURIComponent(item.project_id));copy.append(link);}
      row.append(copy,node('strong',(item.amount>0?'+':'')+number(item.amount),item.amount>0?'token-credit':'token-debit'));history.append(row);
    }
    document.getElementById('tokens-empty').hidden=items.length>0;
  }
  async function refresh(){
    if(!user||document.hidden)return;
    if(pending)return pending;
    pending=(async()=>{try{
      const r=await fetch(user.role==='admin'?'/api/admin/tokens':'/api/tokens/me',{cache:'no-store'});
      const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'分析额度 余额读取失败');render(data);
    }catch(e){if(footer)footer.textContent='读取失败 · '+e.message;if(page)document.getElementById('tokens-status').textContent='读取失败 · '+e.message;}
    })().finally(()=>{pending=null;});return pending;
  }
  window.GGABAuthReady.then(identity=>{
    user=identity;if(sidebar)makeCard();refresh();
    if(page){document.getElementById('tokens-refresh').onclick=refresh;document.getElementById('tokens-nav-toggle').onclick=()=>document.body.classList.toggle('nav-collapsed');}
    setInterval(refresh,30000);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
    document.addEventListener('ggab-token-synced',()=>{const current=pending;if(current)current.then(refresh);else refresh();});
  }).catch(()=>{});
})();
