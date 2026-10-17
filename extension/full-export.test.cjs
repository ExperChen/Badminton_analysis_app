const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

async function run({status='finalized',kind='sync_status',accepted=true,skip=false,csvType='text/csv',csvStatus=200}={}) {
  const requests=[],errors=[];
  const remote='d8a76ac5-2f3a-49ce-a27f-408c7e5d98e2';
  const csv='match_id,rally_id,shot_id\n'+remote+',r1,s1\n';
  const ticket='a'.repeat(43);
  const context=vm.createContext({URL,AbortSignal,TextDecoder,importScripts:()=>{}, Migration:{identity:async()=>"11111111-2222-4333-8444-555555555555"},
    chrome:{storage:{local:{get:async()=>({bridgeToken:'paired'}),set:async value=>errors.push(value)}},
      alarms:{get:async()=>({}),onAlarm:{addListener:()=>{}}},
      runtime:{onInstalled:{addListener:()=>{}},onStartup:{addListener:()=>{}},onMessage:{addListener:()=>{}}}},
    fetch:async(url,options)=>{
      requests.push({url,options});
      let body;
      if(url.endsWith('/claim'))body={job:{id:'j1',kind,lease:'lease',local_project_id:'local',
        remote_project_id:remote,remote_url:'https://app.getgoodatbadminton.com/tracker/'+remote,needs_export:true}};
      else if(url.endsWith('/result'))body={ok:accepted};
      else if(url.endsWith('/full-export/start'))body=skip?{skipped:true}:{ticket};
      else if(url.includes('/worker/full-export/'))body={ok:true};
      else if(url.endsWith('/api/export/shots.csv'))return {url,ok:csvStatus===200,status:csvStatus,
        headers:{get:name=>name==='content-type'?csvType:null},arrayBuffer:async()=>new TextEncoder().encode(csv).buffer};
      else body={project:{id:remote,status}};
      return {url,ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(body),json:async()=>body};
    }});
  vm.runInContext(fs.readFileSync(__dirname+'/background.js','utf8'),context);
  await vm.runInContext('processOne()',context);
  return {requests,errors,csv};
}

test('finalized status fetches official unfiltered CSV and submits whole snapshot after acknowledgement',async()=>{
  const {requests,csv}=await run();
  const download=requests.find(r=>r.url.endsWith('/api/export/shots.csv'));
  assert.equal(download.options.credentials,'include');
  assert.equal(download.options.method,undefined);
  const submitted=requests.find(r=>r.url.includes('/worker/full-export/'));
  assert.equal(JSON.parse(submitted.options.body).csv_text,csv);
  assert.ok(requests.findIndex(r=>r.url.endsWith('/result'))<requests.indexOf(download));
  assert.equal(requests.some(r=>/confirm|tracking\/requests|shots-export/.test(r.url)),false);
});

test('unfinished, unverified, rejected or coalesced observations never replace data',async()=>{
  for(const options of [{status:'tracking'},{kind:'inspect_remote'},{accepted:false},{skip:true}]){
    const {requests}=await run(options);
    assert.equal(requests.some(r=>r.url.endsWith('/api/export/shots.csv')),false);
  }
});

test('login errors and HTML responses report failure without posting a CSV',async()=>{
  for(const options of [{csvStatus:401},{csvType:'text/html'}]){
    const {requests,errors}=await run(options);
    const report=JSON.parse(requests.find(r=>r.url.includes('/worker/full-export/')).options.body);
    assert.equal(report.csv_text,undefined);
    assert.ok(report.error);
    assert.equal(errors.length,1);
  }
});
