const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function configured(publicKey) {
  const requests = [];
  const context = vm.createContext({URL, AbortSignal, Date,
    chrome:{storage:{session:{get:async () => ({ggabSession:{
      accessToken:'mock-user-token', userId:'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',
      expiresAt:Date.now()/1000+600}})}}},
    fetch:async (url, options) => {
      requests.push({url, options});
      return {url,status:200,ok:true,headers:{get:() => 'application/json'},text:async () => '[]'};
    }});
  vm.runInContext(fs.readFileSync(__dirname + '/config.js', 'utf8'), context);
  if (publicKey !== undefined) context.GGAB_PUBLIC_ANON_KEY = publicKey;
  vm.runInContext(fs.readFileSync(__dirname + '/migrate.js', 'utf8') + '\nthis.migration = Migration;', context);
  const job = {stage:'lookup_video',data:{},video:{filename:'mock.mp4',duration:10,size_bytes:100}};
  return {run:() => context.migration.step(job), requests};
}

test('the shipped empty public key stops before any upstream request', async () => {
  const run = configured();
  await assert.rejects(run.run(), /config\.js.*GGAB_PUBLIC_ANON_KEY/);
  assert.equal(run.requests.length, 0);
});

test('a configured public key preserves the authenticated user bearer token', async () => {
  const run = configured('  fake-public-anon-key  ');
  assert.equal((await run.run()).progress.stage, 'init_video');
  assert.equal(run.requests.length, 1);
  assert.equal(run.requests[0].options.headers.apikey, 'fake-public-anon-key');
  assert.equal(run.requests[0].options.headers.Authorization, 'Bearer mock-user-token');
});

test('invalid public key configuration never starts an upstream request', async () => {
  for (const value of [' ', 'invalid\nheader', 123, null]) {
    const run = configured(value);
    await assert.rejects(run.run(), /公开 anon key 未配置/);
    assert.equal(run.requests.length, 0);
  }
});
