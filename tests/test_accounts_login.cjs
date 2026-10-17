const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
test('successful login for each role goes to Match Breakdown in the same account context',async()=>{
  for(const role of ['admin','user']) {
    const nodes={},redirects=[],base='/s/'+'a'.repeat(32);
    const get=id=>nodes[id]??=( {value:id==='email'?'test@example.test':'test-password-2026',hidden:false,disabled:false});
    vm.runInNewContext(fs.readFileSync(__dirname+'/../static/accounts-login.js','utf8'),{
      window:{GGABPath:url=>base+url},location:{replace:url=>redirects.push(url)},
      document:{getElementById:get,querySelectorAll:()=>[],querySelector:()=>null},
      fetch:async(url)=>({ok:true,json:async()=>url.endsWith('/status')?{initialized:true}:{role}})
    });
    await get('auth-form').onsubmit({preventDefault(){}});
    assert.deepEqual(redirects,[base+'/insights/match-breakdown']);
    assert.equal(get('password').value,'');
  }
});

test('registration confirms password and only submits ordinary credentials in its own context',async()=>{
  const nodes={},calls=[],redirects=[],base='/s/'+'b'.repeat(32);
  const get=id=>nodes[id]??={value:id==='email'?'new@example.test':'test-password-2026',hidden:false,disabled:false};
  const buttons=['login','register','reset'].map(mode=>({dataset:{mode},hidden:mode==='register'}));
  vm.runInNewContext(fs.readFileSync(__dirname+'/../static/accounts-login.js','utf8'),{
    window:{GGABPath:url=>base+url},location:{replace:url=>redirects.push(url)},
    document:{getElementById:get,querySelectorAll:()=>buttons,querySelector:selector=>selector.includes('data-mode')?buttons[1]:get('email-label')},
    fetch:async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>url.endsWith('/status')?{initialized:true,registration_open:true}:{role:'user'}};}
  });
  await new Promise(setImmediate);assert.equal(buttons[1].hidden,false);
  buttons[1].onclick();assert.equal(get('confirm-password').hidden,false);
  assert.equal(get('submit').textContent,'注册并登录');
  get('confirm-password').value='different';await get('auth-form').onsubmit({preventDefault(){}});
  assert.equal(calls.filter(c=>c.options.method==='POST').length,0);
  get('confirm-password').value=get('password').value;await get('auth-form').onsubmit({preventDefault(){}});
  const request=calls.find(c=>c.options.method==='POST');assert.equal(request.url,base+'/api/auth/register');
  assert.deepEqual(JSON.parse(request.options.body),{email:'new@example.test',password:'test-password-2026'});
  assert.deepEqual(redirects,[base+'/insights/match-breakdown']);
});
