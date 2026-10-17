const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

test('remote content script remains idempotent after programmatic reinjection', async () => {
  const listeners = [];
  let timers = 0;
  const context = vm.createContext({
    localStorage:{getItem:() => JSON.stringify({access_token:'token',
      user:{id:'11111111-2222-4333-8444-555555555555'},expires_at:Date.now()/1000+600})},
    window:{addEventListener:() => {}},
    setInterval:() => {timers++;},
    chrome:{runtime:{id:'test-extension',onMessage:{addListener:fn => listeners.push(fn)},
      sendMessage:async () => ({ok:true})}}
  });
  const script = fs.readFileSync(__dirname + '/remote.js','utf8');
  vm.runInContext(script,context);
  vm.runInContext(script,context);
  assert.equal(listeners.length,1);
  assert.equal(timers,1);
  let response;
  listeners[0]({type:'ggab-session'},{id:'test-extension'},value => {response=value;});
  assert.equal(response.ok,true);
  assert.equal(response.accessToken,'token');
});
