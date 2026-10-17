const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/../static/tracker.js','utf8');
function part(start,end) {const a=source.indexOf(start),b=source.indexOf(end,a);assert.ok(a>=0&&b>a);return source.slice(a,b);}
const session='video-local-original',remote='remote-match';
function model(extra=[]) {
  const elements=new Map();
  const context=vm.createContext({el:id=>{if(!elements.has(id))elements.set(id,{value:'',innerHTML:''});return elements.get(id);},
    esc:String,clock:String,fail:message=>{context.error=message;},confirm:()=>{throw new Error('same original must not ask to clear snapshots');},
    slots:()=>['A1','B1'],player:()=>({snapshot:{id:'existing'}}),preparePlayers:()=>{},showStep:()=>{}});
  const library=[{match_id:remote,local_session_id:session,has_video:true,title:'Demo match',video:{duration:240.5}},...extra];
  vm.runInContext('let library='+JSON.stringify(library)+'; let state={session_id:'+JSON.stringify(session)+',players:{A1:{snapshot:{id:"existing"}}},segments:[{start:1,end:10}]}; let diskVideos=[],busy=false;'+
    part('function localVideoId(', 'function persist(')+part('function renderVideos(', 'async function refreshDiskVideos(')+
    part('function setVideo(', 'function renderPlayerTabs('),context);
  return {context,elements,read:expression=>vm.runInContext(expression,context)};
}
test('CSV remote match alias resolves existing local project and its duration',()=>{
  const m=model();assert.equal(m.read('selectedVideo().match_id'),remote);assert.equal(m.read('duration()'),240.5);
  m.read('renderVideos()');assert.match(m.elements.get('videos').innerHTML,/data-video="video-local-original"/);
  assert.match(m.elements.get('videos').innerHTML,/已选择/);assert.equal(m.elements.get('reclaim-video').disabled,false);
});
test('selecting the same video via remote or local ID preserves all existing annotations',()=>{
  const m=model();m.read('setVideo("remote-match")');
  assert.equal(m.read('state.session_id'),session);assert.equal(m.read('state.players.A1.snapshot.id'),'existing');
  assert.equal(m.read('state.segments.length'),1);m.read('setVideo("video-local-original")');
  assert.equal(m.read('state.players.A1.snapshot.id'),'existing');
});
test('old remote-ID drafts normalize without clearing boxes or creating sessions',()=>{
  const m=model();m.read('state.session_id="remote-match";normalizeSelectedVideo()');
  assert.equal(m.read('state.session_id'),session);assert.equal(m.read('state.players.A1.snapshot.id'),'existing');
});
test('shared original is listed once and missing sources cannot be selected by title',()=>{
  const m=model([{match_id:'remote-two',local_session_id:session,has_video:true,title:'Demo match',video:{duration:240.5}},
    {match_id:'missing',local_session_id:'lost',has_video:false,title:'Demo match',video:null}]);
  m.read('renderVideos()');assert.equal((m.elements.get('videos').innerHTML.match(/data-video=/g)||[]).length,1);
  m.read('setVideo("missing")');assert.equal(m.read('state.session_id'),session);assert.ok(m.context.error);
});
