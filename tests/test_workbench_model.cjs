const test=require('node:test');
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const path=require('node:path');
const model=import(pathToFileURL(path.join(__dirname,'../ui-versions/react/frontend/src/features/workbench/model.js')));
const videos=[{match_id:'historical-match',local_session_id:'original',title:'Video',has_video:true,video:{duration:100}}];
test('video aliases and existing snapshots survive hydration',async()=>{
  const {hydrateProject,selectedVideo,projectPayload}=await model;
  const p=hydrateProject({id:'project',title:'Match',session_id:'historical-match',game_type:'singles',playing:true,players:[{slot:'A1',name:'A',is_you:true},{slot:'B1',name:'B'}],segments:[],snapshots:{A1:{id:'a',time:8,rotation:90,box:{x:.1,y:.2,width:.2,height:.3}},B1:{id:'b',time:8,rotation:90,box:{x:.6,y:.2,width:.2,height:.3}}}});
  assert.equal(selectedVideo(p,videos).local_session_id,'original');
  assert.equal(p.players.B1.snapshot.id,'b');assert.deepEqual(p.frame,{time:8,rotation:90});
  const payload=projectPayload(p,videos);assert.equal(payload.session_id,'original');assert.equal(payload.players[1].snapshot_id,'b');assert.ok(!('frame' in payload));
});
test('a new shared frame clears every player crop but preserves names',async()=>{
  const {freshProject,setSharedFrame}=await model;const p=freshProject();p.players.A1.snapshot={id:'a'};p.players.B1.box={x:.5};p.players.A1.name='Remember me';
  const next=setSharedFrame(p,20,180);assert.equal(next.players.A1.name,'Remember me');assert.equal(next.players.A1.snapshot,null);assert.equal(next.players.B1.box,null);assert.equal(next.players.B1.time,20);assert.equal(next.players.A1.rotation,180);assert.equal(p.players.A1.snapshot.id,'a');
});
test('scores stay optional, range overlap and invalid dates are rejected',async()=>{
  const {freshProject,validateProject}=await model;const p={...freshProject(),title:'Match',session_id:'original',match_date:'2026-02-30'};
  assert.ok(validateProject(p,videos,0).some(e=>e.includes('日期')));
  p.match_date='123456-10-25';assert.ok(validateProject(p,videos,0).some(e=>e.includes('日期')));
  p.match_date='2026-10-25';assert.deepEqual(validateProject(p,videos,0),[]);
  p.segments=[{start:0,end:30,first_server:'near'}];assert.deepEqual(validateProject(p,videos,3),[]);
  p.segments.push({start:29,end:50,first_server:'far'});assert.ok(validateProject(p,videos,3).length);
});
test('double matches need four saved crops and exactly one self only when playing',async()=>{
  const {freshProject,emptyPlayer,slotsFor,validateProject}=await model;const p={...freshProject(),game_type:'doubles'};
  for(const s of slotsFor('doubles'))p.players[s]={...emptyPlayer(s),name:s,snapshot:{id:s},is_you:s==='A2'};
  assert.deepEqual(validateProject(p,videos,2),[]);p.players.B2.saving=true;assert.ok(validateProject(p,videos,2).length);p.players.B2.saving=false;p.playing=false;assert.ok(validateProject(p,videos,2).length);p.players.A2.is_you=false;assert.deepEqual(validateProject(p,videos,2),[]);
});
test('new segments fill real gaps, and clip padding cannot be negative or blank',async()=>{
  const {nextSegment,clipSettings}=await model;
  assert.deepEqual(nextSegment([{start:0,end:10},{start:20,end:100}],100),{start:10,end:20,first_server:'near'});
  assert.equal(nextSegment([{start:0,end:100}],100),null);
  assert.equal(clipSettings('',-1,0),null);assert.equal(clipSettings('','',0),null);assert.equal(clipSettings('',6,0),null);assert.deepEqual(clipSettings('2','.5','0'),{game:2,before:.5,after:0});
});
