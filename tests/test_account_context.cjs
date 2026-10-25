const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, '../static', name), 'utf8');

function tab(digit, email = 'alice@example.test') {
  const base = '/s/' + digit.repeat(32);
  const calls = [], historyCalls = [], intervals = [];
  class XHR {
    open(...args) {this.opened=args;}
    setRequestHeader(name,value) {(this.headers ||= {})[name]=value;}
    send(body) {this.body=body;}
  }
  const identity = {id:digit,email,role:'user',csrf:'csrf-'+digit};
  const window = {fetch:async(input, options={})=>{
    calls.push({input,options});
    return new Response(JSON.stringify(String(input).endsWith('/api/me') ? identity : {ok:true}), {status:200});
  }};
  const context = vm.createContext({window,URL,Headers,Request,Response,XMLHttpRequest:XHR,
    location:{href:'http://127.0.0.1:8000'+base+'/account',pathname:base+'/account',origin:'http://127.0.0.1:8000',replace:url=>{calls.push({redirect:url});}},
    history:{pushState:(...args)=>historyCalls.push(args),replaceState:(...args)=>historyCalls.push(args)},
    document:{title:'GGAB Local',readyState:'complete',getElementById:()=>null,querySelectorAll:()=>[]},
    setInterval:callback=>intervals.push(callback)});
  vm.runInContext(read('account-context.js'),context);
  return {context,window,base,calls,historyCalls,intervals,XHR,identity};
}

test('navigation and XHR keep context while blob and external URLs remain unchanged',()=>{
  const run=tab('a'), {YuxuPath}=run.window;
  assert.equal(YuxuPath('/api/my/export.csv?game=2'),run.base+'/api/my/export.csv?game=2');
  assert.equal(YuxuPath(run.base+'/tracker'),run.base+'/tracker');
  assert.equal(YuxuPath('https://app.getgoodatbadminton.com/tracker/test'),'https://app.getgoodatbadminton.com/tracker/test');
  assert.equal(YuxuPath('blob:http://127.0.0.1:8000/test'),'blob:http://127.0.0.1:8000/test');
  run.context.history.replaceState(null,'','/tracker/new');
  assert.equal(run.historyCalls[0][2],run.base+'/tracker/new');
  const xhr=new run.XHR();xhr.open('POST','/api/sessions/import',true);
  assert.equal(xhr.opened[1],run.base+'/api/sessions/import');
  assert.equal(run.window.YuxuRoute(),'/account');
});

test('two tabs keep their API, upload CSRF and revocation polling identities independent',async()=>{
  const alice=tab('a'), bob=tab('b','bob@example.test');
  for(const run of [alice,bob]) {
    vm.runInContext(read('account-session.js'),run.context);
    await run.window.YuxuAuthReady;
    await run.window.fetch('/api/tracker/projects',{method:'POST',body:'{}'});
    const mutation=run.calls.find(call=>String(call.input).endsWith('/api/tracker/projects'));
    assert.equal(mutation.input,run.base+'/api/tracker/projects');
    assert.equal(mutation.options.headers.get('X-CSRF-Token'),run.identity.csrf);
    const xhr=new run.XHR();xhr.open('POST','/api/sessions/import');xhr.send('video');
    await Promise.resolve();
    assert.equal(xhr.opened[1],run.base+'/api/sessions/import');
    assert.equal(xhr.headers['X-CSRF-Token'],run.identity.csrf);
    assert.equal(xhr.body,'video');
    await run.intervals[0]();
    assert.equal(run.calls.some(call=>call.redirect),false);
    assert.equal(run.calls.filter(call=>String(call.input).endsWith('/api/me')).every(call=>call.input===run.base+'/api/me'),true);
  }
  assert.equal(alice.window.YuxuUser.email,'alice@example.test');
  assert.equal(bob.window.YuxuUser.email,'bob@example.test');
});
