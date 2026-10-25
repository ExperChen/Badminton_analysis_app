const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/../static/tracker.js','utf8');

test('project list uses local settings and historical data only',()=>{
  const nodes=new Map();
  const el=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,textContent:'',innerHTML:'',pause(){}});return nodes.get(id);};
  const prefix='/s/'+'a'.repeat(32);
  const context={el,window:{YuxuPath:p=>prefix+p},projects:[
    {id:'one',title:'<Test>',game_type:'singles',players:[{},{}],analysis_match_id:'local-match'},
    {id:'two',title:'Saved',game_type:'doubles',players:[{},{},{},{}]}
  ],frameAbort:null,esc:v=>String(v??'').replaceAll('<','&lt;').replaceAll('>','&gt;')};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function localProjectUrl'),source.indexOf('async function editProject')),context);
  vm.runInContext('showLibrary()',context);
  const html=el('project-list').innerHTML;
  assert.ok(html.includes(prefix+'/tracker/projects/one'));
  assert.ok(html.includes(prefix+'/insights/match-breakdown?match=local-match'));
  assert.ok(html.includes('&lt;Test&gt;'));
  assert.ok(html.includes('设置已保存')&&html.includes('已有复盘数据'));
  assert.doesNotMatch(html,/自动分析|费用|额度|remote|handoff/);
});

test('active browser scripts contain no remote or billing endpoints',()=>{
  for(const file of fs.readdirSync(__dirname+'/../static').filter(f=>f.endsWith('.js'))){
    const text=fs.readFileSync(__dirname+'/../static/'+file,'utf8');
    assert.doesNotMatch(text,/getgoodatbadminton|\/api\/bridge|\/api\/tokens|\/account\/tokens|GGABConfirmation|token-ui\.js/);
  }
});
