const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const user = '11111111-2222-4333-8444-555555555555';
const id = '0f720448-aaf7-4213-ba04-32d58f1ef58c';
function harness({rows, code=206, range='bytes 0-0/123', login=true, expected={}} = {}) {
  const calls = [];
  const ctx = vm.createContext({GGAB_PUBLIC_ANON_KEY:'test-public-key',URL,AbortSignal,Date,chrome:{storage:{session:{
    get:async()=>({ggabSession: login ? {accessToken:'test',userId:user,expiresAt:Date.now()/1000+600} : null}),
    remove:async()=>{} }},tabs:{query:async()=>[]}},
    fetch:async (url,options)=>{
      calls.push({url,options}); assert.equal(options.method || 'GET','GET');
      if (url.includes('/rest/v1/videos')) return {ok:true,status:200,url,
        headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(rows ?? [
          {id,title:'original.mp4',size_bytes:123,duration_s:10,status:'ready',storage_path:user+'/original.mp4'}])};
      assert.equal(options.headers.Range,'bytes=0-0');
      return {ok:code<300,status:code,url,headers:{get:name=>name==='content-range'?range:null},
        body:{cancel:async()=>{calls.push({cancelled:true});}}};
    }});
  vm.runInContext(fs.readFileSync(__dirname+'/migrate.js','utf8')+'\nthis.migration=Migration;',ctx);
  return {calls,check:()=>ctx.migration.checkVideo({video:{video_id:id,filename:'original.mp4',
    size_bytes:123,duration:10,storage_path:user+'/original.mp4',...expected}})};
}
test('exact ready video and stored byte count verified using read-only range request',async()=>{
  const h=harness();assert.equal((await h.check()).status,'ready');
  assert.ok(h.calls[0].url.includes('id=eq.'+id));assert.equal(h.calls.length,3);
});
test('missing library ID and missing object are unavailable, without upload or submit',async()=>{
  assert.equal((await harness({rows:[]}).check()).status,'missing');
  assert.equal((await harness({code:404}).check()).status,'missing');
});
test('login failure is unknown, not missing; processing is not completed',async()=>{
  const h=harness({login:false});assert.equal((await h.check()).status,'unknown');assert.equal(h.calls.length,0);
  assert.equal((await harness({rows:[{id,status:'processing'}]}).check()).status,'processing');
});
test('incomplete original storage or changed duration is a specific mismatch, not completed',async()=>{
  const incomplete=await harness({range:'bytes 0-0/100'}).check();
  assert.equal(incomplete.status,'mismatch');assert.match(incomplete.reason,/123.*100/);
  const duration=await harness({rows:[{id,status:'ready',title:'original.mp4',
    size_bytes:123,duration_s:20,storage_path:user+'/original.mp4'}]}).check();
  assert.equal(duration.status,'mismatch');assert.match(duration.reason,/时长.*10.*20/);
  assert.equal((await harness({code:500}).check()).status,'unknown');
});

test('processed metadata and renamed title do not reject a verified complete original',async()=>{
  const h=harness({rows:[{id,status:'ready',title:'Renamed match',size_bytes:80,
    duration_s:10.005,storage_path:user+'/progressive.mp4'}]});
  const result=await h.check();
  assert.equal(result.status,'ready');assert.equal(result.rule_version,2);
  assert.equal(result.observed.metadata_size_bytes,80);
  assert.equal(result.observed.original_size_bytes,123);
  assert.ok(h.calls[1].url.endsWith('/original.mp4'));
  assert.match(result.reason,/元数据大小为 80/);
});

test('missing metadata size is not mistaken for a zero-byte or incomplete upload',async()=>{
  const result=await harness({rows:[{id,status:'ready',title:'original.mp4',
    duration_s:10,storage_path:user+'/original.mp4'}]}).check();
  assert.equal(result.status,'ready');assert.equal(result.observed.metadata_size_bytes,null);
});

test('unknown duration and unregistered renamed source require evidence, not guessed identity',async()=>{
  assert.equal((await harness({rows:[{id,status:'ready',title:'original.mp4',
    storage_path:user+'/original.mp4'}]}).check()).status,'unknown');
  const h=harness({expected:{storage_path:null},rows:[{id,status:'ready',title:'renamed.mp4',
    duration_s:10,storage_path:user+'/original.mp4'}]});
  assert.equal((await h.check()).status,'unknown');assert.equal(h.calls.length,1);
});

test('different video directory, deleted record and foreign storage cannot be accepted',async()=>{
  const base={id,status:'ready',title:'original.mp4',size_bytes:123,duration_s:10};
  const wrong=harness({rows:[{...base,storage_path:user+'/another/progressive.mp4'}]});
  assert.equal((await wrong.check()).status,'mismatch');assert.equal(wrong.calls.length,1);
  assert.equal((await harness({rows:[{...base,deleted_at:'2026-10-02'}]}).check()).status,'missing');
  const foreign=harness({expected:{storage_path:'someone-else/original.mp4'}});
  assert.equal((await foreign.check()).status,'unknown');assert.equal(foreign.calls.length,1);
});
