const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, '../static', name), 'utf8');
const source = read('tracker.js');
function part(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a);
  return source.slice(a, b);
}

function tracker(digit = 'a') {
  const base = digit ? '/s/' + digit.repeat(32) : '';
  const elements = new Map();
  const project = {id:'project-one', session_id:'video-local-original', title:'Test match',
    game_type:'singles', players:[{},{}], migration:{status:'finalized',remote_project_id:'remote-one'}};
  class XHR {open() {}}
  const context = vm.createContext({URL, XMLHttpRequest:XHR,
    location:{href:'http://127.0.0.1:8000'+base+'/tracker', pathname:base+'/tracker', origin:'http://127.0.0.1:8000'},
    history:{pushState() {},replaceState() {}}, window:{GGABConfirmation:{status:()=>({}),view:()=>({}),updateStatus() {}},GGABCosts:{render() {},text:()=> '等待服务计算费用'}},
    document:{dispatchEvent() {}}, Event:class {},
    el:id=>{
      if (!elements.has(id)) elements.set(id,{dataset:{},innerHTML:'',pause() {},scrollIntoView() {}});
      return elements.get(id);
    }, esc:value=>String(value).replace(/&/g,'&amp;').replace(/"/g,'&quot;'),
    migrationLabel:m=>m?.status || 'Local', associationLabel:()=> 'verified',
    clearError() {}, fail:error=>{throw error;}, clock:seconds=>String(seconds),
    api:async(url)=>url==='/api/bridge/status'?null:context.bundle,
    fetch:async()=>({ok:false})});
  vm.runInContext(read('account-context.js'), context);
  vm.runInContext('let projects='+JSON.stringify([project])+'; let frameAbort=null;'+
    part('function localProjectUrl(', 'async function editProject(')+
    part('function renderAutomationCard(', 'window.GGABConfirmation.subscribe(')+
    'let handoffRequest=0;let handoffView=null;'+
    part('function flowMessage(', "el('close-handoff').onclick="), context);
  return {base, elements, project, context, run:code=>vm.runInContext(code, context)};
}

test('library and finalized-project navigation retain each tab login context', () => {
  for (const digit of ['a','b']) {
    const t=tracker(digit);
    t.run('showLibrary()');
    assert.match(t.elements.get('project-list').innerHTML,
      new RegExp('href="'+t.base+'/tracker/projects/project-one"'));
    t.run('renderAutomationCard(projects[0].migration,projects[0])');
    const html=t.elements.get('handoff-automation').innerHTML;
    assert.ok(html.includes('href="'+t.base+'/tracker/projects/project-one"'));
    assert.ok(html.includes('href="'+t.base+'/insights/match-breakdown?match=video-local-original"'));
    assert.equal(html.includes('href="/insights/'), false);
  }
});

test('queued-project new/edit navigation keeps context without changing account or charging', () => {
  const t=tracker('b');
  t.run('renderAutomationCard({...projects[0].migration,status:"processing"},projects[0])');
  const html=t.elements.get('handoff-automation').innerHTML;
  assert.ok(html.includes('href="'+t.base+'/tracker/new"'));
  assert.ok(html.includes('href="'+t.base+'/tracker/new?project=project-one"'));
  assert.equal(html.includes('/s/'+'a'.repeat(32)), false);
});

test('root-context navigation still works and identifiers are URL-encoded', () => {
  for (const digit of ['', 'a']) {
    const t=tracker(digit);
    assert.equal(t.run('localProjectUrl("project /?")'),t.base+'/tracker/projects/project%20%2F%3F');
    t.run('projects[0].session_id="video /?";renderAutomationCard(projects[0].migration,projects[0])');
    assert.ok(t.elements.get('handoff-automation').innerHTML.includes(
      'href="'+t.base+'/insights/match-breakdown?match=video%20%2F%3F"'));
  }
});

test('handoff screenshots retain each tab context, escape URLs and avoid double prefixes', async () => {
  for (const digit of ['', 'a','b']) {
    const t=tracker(digit);
    t.context.bundle={project:{...t.project,
      players:[{slot:'A1',name:'Alice'},{slot:'B1',name:'Bob'}],
      snapshots:{A1:{url:'/api/tracker/snapshots/snap-a?x=1&y=2',time:79.2,rotation:0},
        B1:{url:t.base+'/api/tracker/snapshots/snap-b',time:79.2,rotation:0}}},
      video:{local_path:'D:/original.mp4',duration:316},migration:null,prompt:'Test'};
    await t.run('openHandoff("project-one")');
    const html=t.elements.get('handoff-summary').innerHTML;
    assert.ok(html.includes('src="'+t.base+'/api/tracker/snapshots/snap-a?x=1&amp;y=2"'));
    assert.ok(html.includes('src="'+t.base+'/api/tracker/snapshots/snap-b"'));
    assert.equal(t.elements.get('handoff-panel').hidden,false);
    if(t.base) {
      assert.equal(html.includes('src="/api/'),false);
      assert.equal(html.includes(t.base+t.base),false);
    }
  }
});

test('saving a project replaces old name candidates with current-account suggestions', async () => {
  const elements = new Map();
  const requests = [];
  const context = vm.createContext({
    el:id=>{if(!elements.has(id)) elements.set(id,{});return elements.get(id);},
    esc:value=>String(value).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'),
    errors:()=>[], slots:()=>['A1','B1'],
    player:slot=>({name:slot,handedness:'right',is_you:slot==='A1',snapshot:{id:'snap'}}),
    persist() {}, showLibrary() {}, history:{replaceState() {}}, fail:error=>{throw error;},
    api:async(url,body)=>{requests.push(url);if(body)return {id:'saved-project'};
      return url.endsWith('/player-options')?[{name:'Current "Player"',handedness:'left'}]:[];}
  });
  vm.runInContext('let state={title:"Test",session_id:"video",segments:[]};let busy=false,projects=[],options=[{name:"Other account"}];'+
    part('function renderPlayerOptions(', 'function clearYouAssignments(')+
    part('async function saveProject(', 'function localProjectUrl('),context);
  await vm.runInContext('saveProject()',context);
  assert.ok(requests.includes('/api/tracker/player-options'));
  assert.equal(elements.get('player-options').innerHTML,'<option value="Current &quot;Player&quot;"></option>');
  assert.equal(elements.get('next').disabled,false);
});
