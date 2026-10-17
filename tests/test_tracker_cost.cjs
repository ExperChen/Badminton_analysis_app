const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Node {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.events={};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(event,handler){this.events[event]=handler;}
}
function setup(role='user') {
  const calls=[],window={GGABUser:{role},GGABPath:p=>'/s/'+'a'.repeat(32)+p};
  vm.runInNewContext(fs.readFileSync(__dirname+'/../static/tracker-cost.js','utf8'),{
    window,document:{createElement:tag=>new Node(tag)},fetch:async(url,options)=>{
      calls.push({url,options});return {ok:true,json:async()=>({current:null,quotes:[]})};
    }
  });
  return {calls,costs:window.GGABCosts};
}
const prediction={id:'prediction-one',tokens:999999,model:{confidence:'tentative'}};
test('user waits for official cost and never sees or requests local forecasts',()=>{
  const {calls,costs}=setup();
  assert.equal(costs.text(prediction,null),'等待服务计算费用');
  assert.equal(costs.text(prediction,0),'服务报价：0 分析额度');
  assert.equal(costs.text(prediction,2588),'服务报价：2,588 分析额度');
  const target=new Node('div');costs.render(target,'project-one',prediction,null,null,true);
  assert.equal(target.children.length,1);
  assert.equal(target.children[0].tag,'p');
  assert.equal(target.children[0].textContent,'等待服务计算费用');
  assert.equal(calls.length,0);
  assert.equal(JSON.stringify(target.children).includes('999,999'),false);
});
test('administrator can read calibration only inside diagnostics and cannot submit a fee',async()=>{
  const {calls,costs}=setup('admin'),target=new Node('div');
  costs.render(target,'project-one',prediction,null);
  assert.equal(target.children.length,1);
  costs.render(target,'project-one',prediction,2588,null,true);
  const details=target.children[1];assert.equal(details.tag,'details');
  assert.match(details.children[0].textContent,/999,999/);
  assert.equal(calls.length,0);
  details.open=true;await details.events.toggle();
  assert.equal(calls.length,1);
  assert.equal(calls[0].url,'/s/'+'a'.repeat(32)+'/api/tracker/projects/project-one/cost-history');
  assert.equal(calls[0].options.method,undefined);
  await details.events.toggle();assert.equal(calls.length,1);
  costs.render(target,'project-one',prediction,2588,null,true);
  assert.equal(target.children[1],details);
});
