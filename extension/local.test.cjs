const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

test('pairing from a scoped administrator tab uses that context for both local requests',async()=>{
  const prefix='/s/'+'a'.repeat(32), events={}, calls=[], notices=[];
  const context=vm.createContext({
    location:{origin:'http://127.0.0.1:8000',pathname:prefix+'/account'},
    document:{documentElement:{dataset:{}},addEventListener:(name,callback)=>{events[name]=callback;},dispatchEvent:event=>notices.push(event)},
    window:{addEventListener:()=>{}},setInterval:()=>{},
    CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
    Event:class {}, chrome:{runtime:{sendMessage:async message=>({ok:true})}},
    fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>url.endsWith('/api/me')?{csrf:'admin-csrf'}:{token:'mock-bridge-token'}};}
  });
  vm.runInContext(fs.readFileSync(__dirname+'/local.js','utf8'),context);
  await events['ggab-connect']();
  assert.deepEqual(calls.map(call=>call.url),[prefix+'/api/me',prefix+'/api/bridge/pair']);
  assert.equal(calls[1].options.headers['X-CSRF-Token'],'admin-csrf');
  assert.equal(notices.at(-1).detail,'ok');
});

test('token refresh verifies the scoped administrator identity and refuses ordinary accounts',async()=>{
  for(const role of ['admin','user']) {
    const prefix='/s/'+'b'.repeat(32),events={},messages=[],calls=[];
    vm.runInNewContext(fs.readFileSync(__dirname+'/local.js','utf8'),{
      location:{origin:'http://127.0.0.1:8000',pathname:prefix+'/insights/match-breakdown'},
      document:{documentElement:{dataset:{}},addEventListener:(name,fn)=>events[name]=fn,dispatchEvent(){}},
      window:{addEventListener(){}},setInterval(){},
      CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
      chrome:{runtime:{sendMessage:async message=>{messages.push(message.type);return {ok:true};}}},
      fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({role})};}
    });
    await events['ggab-token-sync']();
    assert.equal(calls[0].url,prefix+'/api/me');assert.equal(calls[0].options.method,undefined);
    assert.equal(messages.includes('sync-tokens'),role==='admin');
  }
});
