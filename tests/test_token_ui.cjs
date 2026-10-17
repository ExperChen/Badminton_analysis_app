const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/../static/token-ui.js','utf8');
class Element {
  constructor(tag='div'){this.tag=tag;this.textContent='';this.children=[];this.dataset={};this.attributes={};this.hidden=false;this.classList={add:()=>{}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(key,value){this.attributes[key]=value;}
  get text(){return this.textContent+this.children.map(n=>n.text).join(' ');}
}
function start(role='user',sidebarOnly=false){
  const ids=['sidebar','tokens-page','tokens-current','tokens-source','tokens-breakdown','tokens-status','tokens-activity','tokens-empty','tokens-refresh','tokens-nav-toggle'];
  const nodes=Object.fromEntries(ids.map(id=>[id,new Element()]));if(sidebarOnly)nodes['tokens-page']=null;
  const events={},requests=[],intervals=[];let failure=false;
  let data=role==='user'?{balance:1000,available:600,reserved:400,consumed:250,transactions:[{amount:1000,kind:'allocation',created:100,note:'<script>not markup</script>'}]}:
    {ggab:{total:500000,stale:false},summary:{reserved:400,consumed:250},transactions:[]};
  const document={hidden:false,body:{classList:{toggle:()=>{}}},getElementById:id=>nodes[id],createElement:tag=>new Element(tag),addEventListener:(event,fn)=>events[event]=fn};
  vm.runInNewContext(source,{window:{AppText:{error:value=>value},GGABAuthReady:Promise.resolve({role}),GGABPath:p=>'/s/'+'a'.repeat(32)+p},document,setInterval:fn=>intervals.push(fn),
    fetch:async(url,options)=>{requests.push({url,options});if(failure)throw new Error('offline');return {ok:true,json:async()=>data};}});
  return {nodes,events,requests,intervals,document,setData:d=>data=d,fail:()=>failure=true};
}
test('subaccount sidebar and GGAB-style token page show own available balance and safe activity',async()=>{
  const p=start();await new Promise(setImmediate);
  assert.equal(p.requests[0].url,'/api/tokens/me');
  const card=p.nodes.sidebar.children[0];assert.equal(card.href,'/s/'+'a'.repeat(32)+'/account/tokens');
  assert.match(card.text,/分析额度.*600.*总额度.*1,000.*预留.*400.*已消耗.*250/);
  assert.equal(p.nodes['tokens-current'].textContent,'600');
  assert.match(p.nodes['tokens-source'].textContent,/不代表共享 分析服务/);
  assert.match(p.nodes['tokens-activity'].text,/<script>not markup<\/script>/);
  assert.equal(p.nodes['tokens-empty'].hidden,true);
  assert.ok(p.requests.every(r=>r.options.cache==='no-store'&&!r.options.method));
});
test('wallet refreshes without charge, handles zero and retains last known amount on error',async()=>{
  const p=start();await new Promise(setImmediate);
  p.setData({balance:0,available:0,reserved:0,consumed:0,transactions:[]});await p.nodes['tokens-refresh'].onclick();
  assert.equal(p.nodes['tokens-current'].textContent,'0');assert.match(p.nodes['tokens-status'].textContent,/额度不足/);
  assert.equal(p.nodes['tokens-empty'].hidden,false);
  p.fail();await p.intervals[0]();assert.equal(p.nodes['tokens-current'].textContent,'0');assert.match(p.nodes['tokens-status'].textContent,/读取失败/);
  const before=p.requests.length;p.document.hidden=true;await p.intervals[0]();assert.equal(p.requests.length,before);
});
test('administrator sees official cached balance, subaccount aggregates and stale warning',async()=>{
  const p=start('admin',true);await new Promise(setImmediate);
  assert.equal(p.requests[0].url,'/api/admin/tokens');assert.match(p.nodes.sidebar.text,/500,000.*400.*250/);
  p.setData({ggab:{total:500000,stale:true,error:'登录失效'},summary:{reserved:0,consumed:250}});
  p.events['ggab-token-synced']();await new Promise(setImmediate);
  assert.equal(p.nodes.sidebar.children[0].dataset.state,'stale');assert.match(p.nodes.sidebar.text,/登录失效/);
});
