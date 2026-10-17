const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

const id = '55f9891e-86b0-41a3-bd1c-e6b4ee629606';
async function run(remote, kind = 'sync_status', video) {
  let alarm;
  const calls = [];
  const job = {id:'j1', lease:'lease', kind, video, remote_project_id:id,
    remote_url:'https://app.getgoodatbadminton.com/tracker/' + id};
  let resolve;
  const done = new Promise(r => {resolve = r;});
  const context = {
    URL, AbortSignal,
    importScripts:() => {},
    Migration:{identity:async () => "11111111-2222-4333-8444-555555555555",step:async () => {throw new Error('unexpected migration');},
      checkVideo:async job => ({video_id:job.video.video_id,status:'missing',reason:'source unavailable'})},
    chrome: {
      storage:{local:{get:async () => ({bridgeToken:'test-token'}),set:async () => {}}},
      alarms:{onAlarm:{addListener:f => {alarm=f;}},get:async () => ({name:'ggab-sync'}),create:async () => {}},
      runtime:{id:'test',onInstalled:{addListener:() => {}},onStartup:{addListener:() => {}},
        onMessage:{addListener:() => {}}}
    },
    fetch:async (url, options) => {
      calls.push({url,options});
      if(url.endsWith('/claim')) return {ok:true,json:async () => ({job})};
      if(url.endsWith('/result')) {resolve(); return {ok:true,json:async () => ({ok:true})};}
      return remote(url);
    }
  };
  vm.runInNewContext(fs.readFileSync(__dirname + '/background.js','utf8'),context);
  alarm({name:'ggab-sync'});
  await done;
  return calls;
}
function project(status = 'finalized', projectId = id) {
  const url = 'https://app.getgoodatbadminton.com/api/tracker/projects/' + id;
  return {ok:true,status:200,url,headers:{get:() => 'application/json'},
    text:async () => JSON.stringify({project:{id:projectId,status,title:'Random match 1'}})};
}

test('explicit video check makes no project writes or CSV export; periodic sync also checks source', async () => {
  const video = {video_id:'exact-video'};
  const explicit = await run(() => assert.fail('manual check must not read project or export CSV'), 'check_video', video);
  const report = JSON.parse(explicit.find(call => call.url.endsWith('/result')).options.body);
  assert.equal(report.video_check.status,'missing');
  assert.equal(report.status,undefined);
  const periodic = await run(() => project(),'sync_status',video);
  const observed = JSON.parse(periodic.find(call => call.url.endsWith('/result')).options.body);
  assert.equal(observed.status,'finalized');
  assert.equal(observed.video_check.video_id,'exact-video');
});

test('reads exact GGAB project API with GET and reports status to bridge', async () => {
  const calls = await run(() => project());
  assert.equal(calls[0].options.headers['X-GGAB-Subject'],'11111111-2222-4333-8444-555555555555');
  assert.equal(calls.length,3);
  assert.equal(calls[1].url,'https://app.getgoodatbadminton.com/api/tracker/projects/' + id);
  assert.equal(calls[1].options.method,undefined);
  assert.equal(calls[1].options.credentials,'include');
  const result = JSON.parse(calls[2].options.body);
  assert.equal(result.status,'finalized');
  assert.equal(result.diagnostic.remote_project_id,id);
});

test('unverified association gets observation without write authority', async () => {
  const calls = await run(() => project(),'inspect_remote');
  const result = JSON.parse(calls.at(-1).options.body);
  assert.equal(result.status,'finalized');
  assert.equal(result.diagnostic.remote_status,'finalized');
});

test('login redirect and mismatched project fail closed', async () => {
  let calls = await run(() => ({ok:true,status:200,url:'https://app.getgoodatbadminton.com/signin'}));
  assert.match(JSON.parse(calls.at(-1).options.body).error,/登录/);
  calls = await run(() => project('finalized','different'));
  assert.match(JSON.parse(calls.at(-1).options.body).error,/ID.*不匹配/);
});

test('unknown status and HTML response fail closed', async () => {
  let calls = await run(() => project('unknown'));
  assert.match(JSON.parse(calls.at(-1).options.body).error,/未知项目状态/);
  calls = await run(() => ({...project(),headers:{get:() => 'text/html'}}));
  assert.match(JSON.parse(calls.at(-1).options.body).error,/非 JSON/);
});

async function drain(stages, accepted = true) {
  const events = [], timers = [];
  let claimed = 0, inflight = 0, peak = 0;
  const context = vm.createContext({
    URL, AbortSignal, importScripts:() => {},
    setTimeout:callback => timers.push(callback),
    chrome:{
      storage:{local:{get:async () => ({bridgeToken:'test-token'}),set:async () => {}}},
      alarms:{onAlarm:{addListener:() => {}},get:async () => ({name:'ggab-sync'}),create:async () => {}},
      runtime:{id:'test',getManifest:() => ({version:'1.3.1'}),onInstalled:{addListener:() => {}},
        onStartup:{addListener:() => {}},onMessage:{addListener:() => {}}}
    },
    Migration:{identity:async () => "11111111-2222-4333-8444-555555555555",step:async job => {
      peak = Math.max(peak, ++inflight);
      events.push('upload:' + job.id);
      await Promise.resolve();
      inflight--;
      if (stages[job.index] === 'error') throw new Error('network failure');
      return {progress:{stage:stages[job.index],data:{upload_offset:(job.index + 1) * 12582912}}};
    }},
    fetch:async (url, options) => {
      if (url.endsWith('/claim')) {
        const index = claimed++;
        events.push('claim:' + index);
        return {ok:true,json:async () => ({job:index < stages.length ?
          {id:String(index),index,kind:'migrate',lease:'lease'} : null})};
      }
      assert.ok(url.endsWith('/result'));
      const body = JSON.parse(options.body);
      events.push((body.error ? 'error:' : 'commit:') + url.split('/').at(-2));
      return {ok:true,json:async () => ({ok:accepted})};
    }
  });
  vm.runInContext(fs.readFileSync(__dirname + '/background.js','utf8'),context);
  await Promise.all([vm.runInContext('sync()',context), vm.runInContext('sync()',context)]);
  return {events,timers,peak,claimed};
}

test('drains chunks sequentially, committing each before next claim and stopping at cost confirmation', async () => {
  const run = await drain(['upload_video','upload_video','await_confirmation','confirm_tracking']);
  assert.deepEqual(run.events, ['claim:0','upload:0','commit:0','claim:1','upload:1','commit:1',
    'claim:2','upload:2','commit:2']);
  assert.equal(run.peak,1);
  assert.equal(run.timers.length,0);
});

test('failed chunk or rejected lease stops immediate draining', async () => {
  const failed = await drain(['error','upload_video']);
  assert.deepEqual(failed.events,['claim:0','upload:0','error:0']);
  const rejected = await drain(['upload_video','upload_video'],false);
  assert.equal(rejected.claimed,1);
  assert.equal(rejected.timers.length,0);
});

test('long transfers yield bounded batches and schedule immediate continuation', async () => {
  const run = await drain(Array(40).fill('upload_video'));
  assert.equal(run.claimed,32);
  assert.equal(run.timers.length,1);
  assert.equal(run.peak,1);
});

test('a restored GGAB session wakes pending work without submitting a fee', async () => {
  let onMessage, claims = 0, replies = [];
  const context = vm.createContext({URL,AbortSignal,importScripts:() => {},
    chrome:{
      storage:{local:{get:async () => ({bridgeToken:'test-token'}),set:async () => {}}},
      alarms:{onAlarm:{addListener:() => {}},get:async () => ({name:'ggab-sync'}),create:async () => {}},
      runtime:{id:'test',onInstalled:{addListener:() => {}},onStartup:{addListener:() => {}},
        onMessage:{addListener:fn => {onMessage=fn;}}}
    },
    Migration:{identity:async () => "11111111-2222-4333-8444-555555555555",rememberSession:async () => {},step:async () => assert.fail('no job expected')},
    fetch:async url => {
      assert.ok(url.endsWith('/claim'));
      claims++;
      return {ok:true,json:async () => ({job:null})};
    }
  });
  vm.runInContext(fs.readFileSync(__dirname + '/background.js','utf8'),context);
  onMessage({type:'ggab-session-updated',session:{ok:true}},
    {id:'test',tab:{id:7},url:'https://app.getgoodatbadminton.com/tracker'},reply => replies.push(reply));
  await new Promise(resolve => setTimeout(resolve,10));
  assert.equal(replies[0].ok,true);
  assert.equal(claims,1);
});

test('worker startup restores a missing alarm and local wake recovers it again without resetting an existing alarm', async () => {
  let onMessage, alarm = null, created = 0, claims = 0, alarmFailure = false;
  const context = vm.createContext({URL,AbortSignal,importScripts:() => {},
    chrome:{
      storage:{local:{get:async () => ({bridgeToken:'test-token'}),set:async () => {}}},
      alarms:{get:async () => {
        if (alarmFailure) throw new Error('alarm unavailable');
        return alarm;
      },create:async (name,options) => {
        assert.equal(name,'ggab-sync');
        assert.equal(options.periodInMinutes,1);
        created++;
        alarm = {name};
      },onAlarm:{addListener:() => {}}},
      runtime:{id:'test',onInstalled:{addListener:() => {}},onStartup:{addListener:() => {}},
        onMessage:{addListener:fn => {onMessage=fn;}}}
    },
    Migration:{identity:async () => "11111111-2222-4333-8444-555555555555",step:async () => assert.fail('no job expected')},
    fetch:async url => {
      assert.ok(url.endsWith('/claim'));
      claims++;
      return {ok:true,json:async () => ({job:null})};
    }
  });
  vm.runInContext(fs.readFileSync(__dirname + '/background.js','utf8'),context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(created,1);
  const sender = {id:'test',tab:{id:7},url:'http://127.0.0.1:8000/tracker'};
  const replies = [];
  onMessage({type:'wake'},sender,result => replies.push(result));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(created,1);
  assert.equal(claims,1);
  assert.equal(replies[0].ok,true);
  alarm = null;
  onMessage({type:'wake'},sender,result => replies.push(result));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(created,2);
  assert.equal(claims,2);
  alarmFailure = true;
  onMessage({type:'wake'},sender,result => replies.push(result));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(claims,3);
});
