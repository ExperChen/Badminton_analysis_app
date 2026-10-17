const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/background.js','utf8');
function runtime(total=516924) {
  const calls=[];
  let messageHandler;
  const context=vm.createContext({URL,AbortSignal,importScripts() {},
    Migration:{identity:async()=> 'source-id'},
    chrome:{runtime:{id:'extension-id',getManifest:()=>({version:'1.5.1'}),onInstalled:{addListener() {}},onStartup:{addListener() {}},onMessage:{addListener(fn) {messageHandler=fn;}}},
      alarms:{onAlarm:{addListener() {}}},storage:{local:{get:async()=>({bridgeToken:'paired-token'}),set:async()=>{}}}},
    fetch:async(url,options={})=>{calls.push({url,options});let data;
      if(url.includes('/worker/'))return {ok:true,json:async()=>({ok:true})};
      if(url.endsWith('/balance'))data={total};
      else if(url.includes('before='))data={items:[{id:'older',amount:-100,pending:false,label:'Track Match Automatically'}],nextCursor:null};
      else data={items:[{id:'exact-transaction',amount:-1929,pending:false,label:'Track Match Automatically'}],nextCursor:'cursor-one'};
      return {ok:true,url,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data)};
    }
  });
  vm.runInContext(source,context);
  return {context,calls,run:code=>vm.runInContext(code,context),message:(...args)=>messageHandler(...args)};
}
test('reads official balance and paginated activity with GET and reports real values to local bridge',async()=>{
  const t=runtime();await t.run('syncTokens()');
  const report=t.calls.find(call=>call.url.endsWith('/worker/token-balance'));
  assert.equal(JSON.parse(report.options.body).total,516924);
  assert.equal(JSON.parse(report.options.body).activity.length,2);
  assert.ok(t.calls.some(call=>call.url.endsWith('/api/tokens/activity?limit=25&before=cursor-one')));
  assert.ok(t.calls.filter(call=>call.url.startsWith('https://')).every(call=>!call.options.method&&call.options.credentials==='include'));
  await t.run('syncTokens()');assert.equal(t.calls.length,4);
});
test('an invalid remote balance is reported as an error and never becomes an invented zero balance',async()=>{
  const t=runtime(null);await t.run('syncTokens()');
  const body=JSON.parse(t.calls.find(call=>call.url.endsWith('/worker/token-balance')).options.body);
  assert.match(body.error,/明确 Token 余额/);assert.equal(body.total,undefined);
});
test('project receipt lookup does not use the recommended settlement as an actual charge',async()=>{
  const t=runtime();
  const remote='aaaaaaaa-cccc-4ddd-8eee-ffffffffffff';
  t.context.fetch=async(url,options={})=>{
    t.calls.push({url,options});let data;
    if(url.endsWith('/worker/claim')) data={job:{id:'job-one',lease:'lease',kind:'sync_status',remote_project_id:remote,
      remote_url:'https://app.getgoodatbadminton.com/tracker/'+remote}};
    else if(url.endsWith('/result')) return {ok:true,json:async()=>({ok:true})};
    else if(url.endsWith('/settle-info')) data={submissionSource:'auto_track',holdTransactionId:'exact-receipt',suggestedSettle:9999};
    else data={project:{id:remote,status:'finalized'}};
    return {ok:true,url,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(data),json:async()=>data};
  };
  await t.run('processOne()');
  const body=JSON.parse(t.calls.find(call=>call.url.endsWith('/result')).options.body);
  assert.equal(body.status,'finalized');assert.equal(body.hold_transaction_id,'exact-receipt');
  assert.equal(body.billing,undefined);
  assert.ok(t.calls.filter(call=>call.url.startsWith('https://')).every(call=>!call.options.method));
});

test('concurrent refreshes coalesce and a ledger failure still reports the real balance separately',async()=>{
  const t=runtime(),fetch=t.context.fetch;
  t.context.fetch=async(url,options)=>url.includes('/api/tokens/activity')?{ok:false,url,status:503}:fetch(url,options);
  await Promise.all([t.run('syncTokens()'),t.run('syncTokens()')]);
  const reports=t.calls.filter(call=>call.url.endsWith('/worker/token-balance'));
  assert.equal(reports.length,1);
  const body=JSON.parse(reports[0].options.body);
  assert.equal(body.total,516924);assert.match(body.activity_error,/503/);assert.equal(body.error,undefined);
});
test('login token refresh only reads balance and activity and never claims or submits a tracking job',async()=>{
  const t=runtime();
  const result=await new Promise(resolve=>t.message({type:'sync-tokens'},
    {id:'extension-id',tab:{id:1},url:'http://127.0.0.1:8000/s/'+ 'a'.repeat(32)+'/insights/match-breakdown'},resolve));
  assert.equal(result.ok,true);
  assert.ok(t.calls.some(c=>c.url.endsWith('/worker/token-balance')));
  assert.equal(t.calls.some(c=>c.url.endsWith('/claim')||c.url.includes('/submit')),false);
});
