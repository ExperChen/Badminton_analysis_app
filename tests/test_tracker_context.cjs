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

function tracker(digit = 'a', analysis = 'match /?') {
  const base = digit ? '/s/' + digit.repeat(32) : '';
  const elements = new Map();
  const project = {id:'project /?', session_id:'video-original', title:'Test match',
    game_type:'singles', players:[{},{}], analysis_match_id:analysis};
  class XHR {open() {}}
  const context = vm.createContext({URL, XMLHttpRequest:XHR,
    location:{href:'http://127.0.0.1:8000'+base+'/tracker', pathname:base+'/tracker', origin:'http://127.0.0.1:8000'},
    history:{pushState() {},replaceState() {}}, window:{},
    el:id=>{
      if (!elements.has(id)) elements.set(id,{dataset:{},innerHTML:'',pause() {}});
      return elements.get(id);
    }, esc:value=>String(value).replace(/&/g,'&amp;').replace(/"/g,'&quot;'),
    clearError() {}});
  vm.runInContext(read('account-context.js'), context);
  vm.runInContext('let projects='+JSON.stringify([project])+'; let frameAbort=null;'+
    part('function localProjectUrl(', 'async function editProject('), context);
  return {base, elements, context, run:code=>vm.runInContext(code, context)};
}

test('project and annotated-match links retain each tab context and encode exact IDs', () => {
  for (const digit of ['', 'a', 'b']) {
    const t=tracker(digit);
    t.run('showLibrary()');
    const html=t.elements.get('project-list').innerHTML;
    assert.ok(html.includes('href="'+t.base+'/tracker/projects/project%20%2F%3F"'));
    assert.ok(html.includes('href="'+t.base+'/insights/match-breakdown?match=match%20%2F%3F"'));
    if(t.base) assert.equal(html.includes('href="/insights/'), false);
    assert.equal(t.elements.get('wizard').hidden,true);
    assert.equal(t.elements.get('project-library').hidden,false);
  }
});

test('saved projects without shot annotations offer editing without an analysis link', () => {
  const t=tracker('b', null);
  t.run('showLibrary()');
  const html=t.elements.get('project-list').innerHTML;
  assert.ok(html.includes('href="'+t.base+'/tracker/projects/project%20%2F%3F"'));
  assert.equal(html.includes('/insights/match-breakdown'),false);
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
