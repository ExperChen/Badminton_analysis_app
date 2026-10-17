const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

const context = {URL, AbortSignal, chrome:{}};
vm.runInNewContext(fs.readFileSync(__dirname + '/migrate.js', 'utf8') + '\nthis.migration = Migration;', context);

test('rotated player boxes map to complete original frame coordinates', () => {
  const box = {x:.2,y:.3,width:.1,height:.2};
  const expected = {
    0:[.2,.3,.1,.2],
    90:[.3,.7,.2,.1],
    180:[.7,.5,.1,.2],
    270:[.5,.2,.2,.1]
  };
  for (const [rotation, result] of Object.entries(expected)) {
    const actual = context.migration.originalBox({box,rotation:Number(rotation)});
    result.forEach((value, index) => assert.ok(Math.abs(actual[index] - value) < 1e-12));
  }
});

test('segments preserve GGAB near/far enum and omit unknown scores', () => {
  const result = context.migration.segments([{start:3,end:7,first_server:'far',
    a_start:null,b_start:4,a_end:null,b_end:null}]);
  assert.equal(result[0].firstServer, 'far');
  assert.equal(context.migration.segments([{start:0,end:1,first_server:'near'}])[0].firstServer,'near');
  assert.throws(() => context.migration.segments([{start:0,end:1,first_server:'A'}]),/先发球方/);
  assert.equal(result[0].scoreBStart, 4);
  assert.equal(Object.hasOwn(result[0], 'scoreAStart'), false);
});

function sessions() {
  let now = 1000000;
  let stored;
  let tabSession = {ok:true, accessToken:'test-session',
    userId:'11111111-2222-4333-8444-555555555555', expiresAt:now/1000 + 600};
  let queries = 0, messages = 0, requests = 0, status = 200, errorBody;
  const context = vm.createContext({GGAB_PUBLIC_ANON_KEY:'test-public-key',
    URL, AbortSignal, Date:{now:() => now},
    chrome:{
      storage:{session:{
        get:async () => ({ggabSession:stored}),
        set:async value => {stored = value.ggabSession;},
        remove:async () => {stored = undefined;}
      }},
      tabs:{
        query:async () => {queries++; return [{id:1}];},
        sendMessage:async () => {messages++; return tabSession;},
        create:async () => {assert.fail('background must not open GGAB tabs');},
        remove:async () => {assert.fail('background must not close GGAB tabs');}
      }
    },
    fetch:async url => {
      requests++;
      return {url,status,ok:status===200,headers:{get:() => 'application/json'},
        text:async () => JSON.stringify(errorBody || [{status:'processing'}])};
    }
  });
  const load = () => vm.runInContext(fs.readFileSync(__dirname + '/migrate.js','utf8') +
    '\nthis.migration = Migration;',context);
  // A new VM represents service-worker suspension/restart, sharing only
  // Chrome's memory-backed storage and mocked network/browser interfaces.
  const restart = () => {
    const restarted = vm.createContext({GGAB_PUBLIC_ANON_KEY:'test-public-key',URL,AbortSignal,Date:context.Date,chrome:context.chrome,fetch:context.fetch});
    vm.runInContext(fs.readFileSync(__dirname + '/migrate.js','utf8') +
      '\nthis.migration = Migration;',restarted);
    return restarted.migration;
  };
  load();
  const job = {stage:'wait_video',data:{video_id:'video-id'},video:{filename:'original.mp4',duration:100}};
  return {migration:context.migration,restart,job,
    counts:() => ({queries,messages,requests}),
    advance:ms => {now += ms;}, logout:() => {tabSession = {ok:false};},
    reject:() => {status = 401;}, stored:() => stored,
    badRequest:body => {status = 400; errorBody = body;}};
}

test('consecutive and concurrent steps reuse session; worker restart reuses browser memory', async () => {
  const run = sessions();
  await Promise.all([run.migration.step(run.job),run.migration.step(run.job)]);
  await run.migration.step(run.job);
  assert.deepEqual(run.counts(),{queries:1,messages:1,requests:3});
  await run.restart().step(run.job);
  assert.deepEqual(run.counts(),{queries:1,messages:1,requests:4});
});

test('expired or logged-out session stops without creating or closing any tab', async () => {
  const run = sessions();
  await run.migration.step(run.job);
  run.advance(600000);
  await assert.rejects(run.migration.step(run.job),/登录/);
  assert.equal(run.counts().requests,1);
  assert.equal(run.stored(),undefined);

  const logout = sessions();
  await logout.migration.step(logout.job);
  logout.logout();
  await logout.migration.rememberSession(null);
  await assert.rejects(logout.migration.step(logout.job),/登录/);
  assert.equal(logout.counts().requests,1);
});

test('a different shared source blocks the migration before any remote request', async () => {
  const run = sessions();
  await assert.rejects(run.migration.step({...run.job,expected_ggab_subject:'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'}), /身份已变化/);
  assert.equal(run.counts().requests,0);
});

test('shared local users never reuse the GGAB account primary player as their own identity', async () => {
  const calls = [];
  const shared = vm.createContext({GGAB_PUBLIC_ANON_KEY:'test-public-key',URL, AbortSignal, Date,
    chrome:{storage:{session:{get:async()=>({ggabSession:{accessToken:'test-session',
      userId:'11111111-2222-4333-8444-555555555555',expiresAt:Date.now()/1000+600}})}}},
    fetch:async(url,options)=>{
      calls.push(url);
      assert.equal(url.includes('user_player_profiles'), false);
      const result = url.includes('/workspace_players?') ? [{id:'workspace-player'}] : {};
      return {url,status:200,ok:true,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(result)};
    }});
  vm.runInContext(fs.readFileSync(__dirname+'/migrate.js','utf8')+'\nthis.migration=Migration;',shared);
  const player={slot:'A1',name:'local player',handedness:'right',is_you:true};
  const result = await shared.migration.step({stage:'setup',shared_local_users:true,
    video:{filename:'original.mp4',duration:100,size_bytes:1000},
    data:{video_id:'video',snapshot_paths:{A1:'image.jpg'}},project:{game_type:'singles',players:[player],
      segments:[],snapshots:{A1:{rotation:0,time:0,box:{x:0,y:0,width:1,height:1}}}}});
  assert.equal(result.progress.data.profile_ids.A1.userPlayerProfileId,null);
  assert.equal(result.progress.data.profile_ids.A1.workspacePlayerId,'workspace-player');
  assert.equal(calls.filter(url=>url.includes('workspace_players')).length,1);
});

function recovery({active = false, windowFocused = true, missingScript = false, refreshSucceeds = false} = {}) {
  const values = {};
  const calls = {messages:0, injections:0, reloads:0, remoteRequests:0};
  let scriptInstalled = !missingScript;
  let signedIn = false;
  const valid = {ok:true, accessToken:'recovered-session',
    userId:'11111111-2222-4333-8444-555555555555', expiresAt:Date.now()/1000 + 600};
  const context = vm.createContext({GGAB_PUBLIC_ANON_KEY:'test-public-key',URL, AbortSignal, Date, setTimeout:fn => fn(),
    chrome:{
      storage:{session:{
        get:async key => ({[key]:values[key]}),
        set:async value => Object.assign(values,value),
        remove:async key => {delete values[key];}
      }},
      windows:{get:async () => ({focused:windowFocused})},
      tabs:{
        query:async () => [{id:7,active,windowId:2}],
        sendMessage:async () => {
          calls.messages++;
          if (!scriptInstalled) throw new Error('Receiving end does not exist');
          return signedIn ? valid : {ok:false};
        },
        reload:async () => {calls.reloads++; if (refreshSucceeds) signedIn = true;},
        create:async () => assert.fail('must not create a GGAB tab'),
        remove:async () => assert.fail('must not close a GGAB tab')
      },
      scripting:{executeScript:async request => {
        assert.equal(request.target.tabId,7);
        assert.equal(request.files[0],'remote.js');
        calls.injections++;
        scriptInstalled = true;
        if (missingScript) signedIn = true;
      }}
    },
    fetch:async url => {calls.remoteRequests++; return {url,status:200,ok:true,
      headers:{get:() => 'application/json'},text:async () => '[{"id":"video-id","status":"processing"}]'};}
  });
  vm.runInContext(fs.readFileSync(__dirname + '/migrate.js','utf8') + '\nthis.migration=Migration;',context);
  const job = {stage:'wait_video',data:{video_id:'video-id'},video:{filename:'original.mp4',duration:100}};
  return {migration:context.migration, job, calls, values};
}

test('missing content script is injected into the existing GGAB tab without a reload', async () => {
  const run = recovery({missingScript:true});
  await run.migration.step(run.job);
  assert.equal(run.calls.injections,1);
  assert.equal(run.calls.reloads,0);
  assert.equal(run.calls.remoteRequests,1);
});

test('stale session refreshes only an inactive existing tab once and resumes migration', async () => {
  const run = recovery({refreshSucceeds:true});
  await run.migration.step(run.job);
  assert.equal(run.calls.reloads,1);
  assert.equal(run.calls.injections,0);
  assert.equal(run.calls.remoteRequests,1);
});

test('real logout is throttled and an active GGAB tab is never refreshed', async () => {
  const inactive = recovery();
  await assert.rejects(inactive.migration.step(inactive.job),/登录/);
  await assert.rejects(inactive.migration.step(inactive.job),/登录/);
  assert.equal(inactive.calls.reloads,1);
  assert.equal(inactive.calls.remoteRequests,0);
  const active = recovery({active:true});
  await assert.rejects(active.migration.step(active.job),/登录/);
  assert.equal(active.calls.reloads,0);
});

test('GGAB tab selected in an unfocused browser window may recover in background', async () => {
  const run = recovery({active:true,windowFocused:false,refreshSucceeds:true});
  await run.migration.step(run.job);
  assert.equal(run.calls.reloads,1);
  assert.equal(run.calls.remoteRequests,1);
});

test('remote authentication rejection clears session and never retries the request', async () => {
  const run = sessions();
  await run.migration.step(run.job);
  run.reject();
  await assert.rejects(run.migration.step(run.job),/登录/);
  assert.equal(run.counts().requests,2);
  assert.equal(run.stored(),undefined);
});

test('setup validation failure reports endpoint and rejected field without replaying writes', async () => {
  const run = sessions();
  run.badRequest({error:'Invalid setup',issues:[{path:['players',0,'box'],message:'Required'}]});
  await assert.rejects(run.migration.step({...run.job,stage:'setup',
    project:{players:[],segments:[],game_type:'singles'}}),
    /HTTP 400.*\/api\/tracker\/setup.*players\.0\.box: Required/);
  assert.equal(run.counts().requests,1);
});

function tracking(estimate = 4389, submit = {jobId:'mock-job',status:'pending'}) {
  const calls = [];
  const context = vm.createContext({GGAB_PUBLIC_ANON_KEY:'test-public-key',URL,AbortSignal,Date,
    chrome:{storage:{session:{get:async () => ({ggabSession:{accessToken:'mock-session',
      userId:'11111111-2222-4333-8444-555555555555',expiresAt:Date.now()/1000+600}})}}},
    fetch:async (url, options) => {
      calls.push({url,options});
      const body = url.includes('/auto-track-estimate?mode=duration') ? {estimate,balance:530121} : submit;
      assert.ok(url.startsWith('https://app.getgoodatbadminton.com/api/tracker/projects/remote-one/'));
      return {url,status:200,ok:true,headers:{get:()=>'application/json'},text:async()=>JSON.stringify(body)};
    }});
  vm.runInContext(fs.readFileSync(__dirname+'/migrate.js','utf8')+'\nthis.migration=Migration;',context);
  const job = {stage:'estimate',data:{remote_project_id:'remote-one',estimated_cost:4389},
    video:{filename:'original.mp4'}};
  return {migration:context.migration,job,calls};
}

test('estimating stops before payment; only the explicitly confirmed stage submits and requires a real queue receipt', async () => {
  const run = tracking();
  const awaiting = await run.migration.step(run.job);
  assert.equal(awaiting.progress.stage,'await_confirmation');
  assert.equal(awaiting.progress.data.estimated_cost,4389);
  assert.equal(run.calls.filter(call=>call.options.method==='POST').length,0);
  const accepted = await run.migration.step({...run.job,stage:'confirm_tracking'});
  assert.equal(accepted.progress.stage,'processing');
  assert.equal(accepted.progress.data.remote_job_id,'mock-job');
  const posts = run.calls.filter(call=>call.options.method==='POST');
  assert.equal(posts.length,1);
  assert.equal(posts[0].url,'https://app.getgoodatbadminton.com/api/tracker/projects/remote-one/submit');
  assert.deepEqual(JSON.parse(posts[0].options.body),{source:'auto_track',mode:'duration'});
});

test('a changed GGAB estimate returns the real amount for renewed consent without submitting', async () => {
  const changed = tracking(5000);
  const corrected = await changed.migration.step({...changed.job,stage:'confirm_tracking'});
  assert.equal(corrected.progress.stage,'await_confirmation');
  assert.equal(corrected.progress.data.estimated_cost,5000);
  assert.equal(corrected.progress.data.estimate_changed_from,4389);
  assert.equal(corrected.progress.data.balance_before,530121);
  assert.equal(changed.calls.filter(call=>call.options.method==='POST').length,0);
});

test('an invalid GGAB queue receipt never reports IN QUEUE', async () => {
  const invalid = tracking(4389,{});
  await assert.rejects(invalid.migration.step({...invalid.job,stage:'confirm_tracking'}),/未确认自动跟踪排队/);
  assert.equal(invalid.calls.filter(call=>call.options.method==='POST').length,1);
});

function videoLibrary(rows) {
  const calls = [];
  let videos = rows;
  const context = vm.createContext({GGAB_PUBLIC_ANON_KEY:'test-public-key',URL,AbortSignal,Date,
    chrome:{storage:{session:{get:async()=>({ggabSession:{accessToken:'mock-session',
      userId:'11111111-2222-4333-8444-555555555555',expiresAt:Date.now()/1000+600}})}}},
    fetch:async(url,options)=>{
      calls.push({url,options});
      assert.match(url,/\/rest\/v1\/videos\?/);
      const value=url.includes('&id=eq.') ? videos.filter(v=>v.id===decodeURIComponent(url.split('&id=eq.')[1].split('&')[0])) : videos;
      return {url,status:200,ok:true,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(value)};
    }});
  vm.runInContext(fs.readFileSync(__dirname+'/migrate.js','utf8')+'\nthis.migration=Migration;',context);
  const job={stage:'lookup_video',data:{},video:{filename:'original.mp4',duration:100,size_bytes:1000}};
  return {migration:context.migration,job,calls,setRows:next=>{videos=next;}};
}

const readyOriginal={id:'video-one',title:'original.mp4',size_bytes:1000,
  duration_s:100,storage_path:'owner/original.mp4',status:'ready'};

test('one verified ready GGAB original is reused without creating another remote video', async()=>{
  const run=videoLibrary([readyOriginal]);
  const result=await run.migration.step(run.job);
  assert.equal(result.progress.stage,'upload_snapshots');
  assert.equal(result.progress.data.source,'reused_existing');
  assert.equal(result.progress.data.video_id,'video-one');
  assert.equal(result.progress.data.remote_evidence.method,'verified_existing_video');
  assert.equal(run.calls.length,1);
});

test('a unique same-name same-size video still processing is awaited and then verified, not uploaded again', async()=>{
  const run=videoLibrary([{...readyOriginal,status:'processing',duration_s:null}]);
  const waiting=await run.migration.step(run.job);
  assert.equal(waiting.progress.stage,'wait_video');
  assert.equal(waiting.progress.data.source,'reused_existing');
  run.setRows([{...readyOriginal,status:'ready'}]);
  const reused=await run.migration.step({...run.job,stage:'wait_video',data:waiting.progress.data});
  assert.equal(reused.progress.stage,'upload_snapshots');
  assert.equal(reused.progress.data.remote_evidence.method,'verified_existing_video');
  assert.equal(run.calls.length,2);
});

test('ambiguous or incomplete GGAB candidates stop safely instead of duplicating an upload', async()=>{
  for(const rows of [[readyOriginal,{...readyOriginal,id:'video-two'}],
    [{...readyOriginal,status:'processing',storage_path:null}],
    [{...readyOriginal,duration_s:123}]]){
    const run=videoLibrary(rows);
    await assert.rejects(run.migration.step(run.job),/停止|不匹配/);
    assert.equal(run.calls.length,1);
  }
  const missing=videoLibrary([]);
  assert.equal((await missing.migration.step(missing.job)).progress.stage,'init_video');
  assert.equal(missing.calls.length,1);
});

test('a processing GGAB candidate that changes identity fails closed after becoming ready', async()=>{
  const run=videoLibrary([{...readyOriginal,status:'processing',duration_s:null}]);
  const waiting=await run.migration.step(run.job);
  run.setRows([{...readyOriginal,title:'different.mp4'}]);
  await assert.rejects(run.migration.step({...run.job,stage:'wait_video',data:waiting.progress.data}),
    /已变化/);
});
