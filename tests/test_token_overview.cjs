const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/../static/token-overview.js','utf8');
function page(role) {
  const nodes=Object.fromEntries(['token-overview','token-overview-values','token-overview-status','token-overview-link'].map(id=>[id,{hidden:true,textContent:''}]));
  const events={},requests=[],notices=[],intervals=[];
  let data=role==='admin'?{ggab:{total:500000,observed_at:100,stale:false},summary:{consumed:250,reserved:400}}:
    {available:600,reserved:400,consumed:250};
  const document={hidden:false,getElementById:id=>nodes[id],addEventListener:(name,fn)=>events[name]=fn,dispatchEvent:event=>notices.push(event.type)};
  vm.runInNewContext(source,{window:{GGABAuthReady:Promise.resolve({role})},document,
    Event:class {constructor(type){this.type=type;}},setInterval:fn=>intervals.push(fn),
    fetch:async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>data};}});
  return {nodes,events,requests,notices,intervals,document,setData:value=>data=value};
}
test('administrator automatically reads official balance and cumulative settlements separately and refreshes after sync',async()=>{
  const p=page('admin');await new Promise(setImmediate);
  assert.equal(p.nodes['token-overview'].hidden,false);
  assert.equal(p.requests[0].url,'/api/admin/tokens');
  assert.ok(p.notices.includes('ggab-token-sync'));
  assert.match(p.nodes['token-overview-values'].textContent,/500,000.*250.*400/);
  p.setData({ggab:{total:499000,observed_at:200,stale:false},summary:{consumed:350,reserved:0}});
  p.events['ggab-token-synced']();await new Promise(setImmediate);
  assert.match(p.nodes['token-overview-values'].textContent,/499,000.*350.*0/);
  const count=p.requests.length;p.document.hidden=true;await p.intervals[0]();assert.equal(p.requests.length,count);
  assert.ok(p.requests.every(r=>!r.options.method));
});
test('ordinary account never requests administrator balances or remote sync and only shows own consumption',async()=>{
  const p=page('user');await new Promise(setImmediate);
  assert.equal(p.requests[0].url,'/api/tokens/me');
  assert.equal(p.notices.length,0);
  assert.match(p.nodes['token-overview-values'].textContent,/600.*400.*250/);
  assert.doesNotMatch(p.nodes['token-overview-values'].textContent,/GGAB 剩余|子账号/);
});
