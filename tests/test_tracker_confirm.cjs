const test = require('node:test');
const assert = require('node:assert/strict');
const {createConfirmation} = require('../static/tracker-confirm.js');
const fs = require('node:fs');
const vm = require('node:vm');

const expected = {remote_project_id: 'remote-one', estimated_cost: 4389};
const flow = {project_id: 'local-one', stage: 'await_confirmation', ...expected,
  balance_before: 530121, confirmation_ready: true};
const status = (changes = {}) => ({connected: true, extension_version: '1.5.0',
  workflows: [{...flow, ...changes}]});
const response = (body, code = 200) => ({ok: code < 400, status: code, json: async () => body});

test('merely rendering/polling never authorizes tracking, and missing prerequisites disable confirmation', async () => {
  const calls = [];
  const model = createConfirmation((...args) => {calls.push(args); throw new Error('unexpected request');});
  model.updateStatus(status());
  assert.equal(model.view('local-one').canConfirm, true);
  assert.equal(calls.length, 0);
  for (const data of [status({confirmation_ready: false}), status({estimated_cost: null}),
    status({remote_project_id: null}), status({stage: 'processing'}), {...status(), connected: false}]) {
    model.updateStatus(data);
    assert.equal(await model.confirm('local-one', expected), false);
  }
  assert.equal(calls.length, 0);
});

test('a single direct click submits exact project/cost; rapid clicks and a stale poll cannot replay it', async () => {
  let finishPost;
  const calls = [];
  const model = createConfirmation(async (url, options) => {
    calls.push({url, options});
    if (options.method === 'POST') return new Promise(resolve => {finishPost = resolve;});
    return response(status()); // Deliberately stale poll after an accepted POST.
  });
  model.updateStatus(status());
  const first = model.confirm('local-one', expected);
  assert.equal(model.view('local-one').operation, 'sending');
  assert.equal(await model.confirm('local-one', expected), false);
  model.updateStatus(status());
  assert.equal(await model.confirm('local-one', expected), false);
  finishPost(response({queued: true, ...expected}));
  await first;
  assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
  assert.equal(calls[0].url, '/api/bridge/confirm/local-one');
  assert.equal(calls[0].options.headers['X-GGAB-Local'], '1');
  assert.deepEqual(JSON.parse(calls[0].options.body), expected);
  assert.equal(model.view('local-one').operation, 'accepted');
  assert.match(model.view('local-one').message, /处理状态和实际费用仍待服务确认/);
  assert.equal(await model.confirm('local-one', expected), false);
  model.updateStatus(status({stage: 'processing'}));
  assert.equal(model.view('local-one').operation, undefined);
  assert.equal(model.view('local-one').canConfirm, false);
});

test('fee/project changes between display and click require a new explicit click, never implicit consent', async () => {
  const calls = [];
  const model = createConfirmation((...args) => {calls.push(args);});
  for (const changes of [{estimated_cost: 5000}, {remote_project_id: 'remote-two'}]) {
    model.updateStatus(status(changes));
    assert.equal(await model.confirm('local-one', expected), false);
    assert.match(model.view('local-one').message, /再次点击/);
  }
  assert.equal(calls.length, 0);
});

test('library one-click action only submits the exact GGAB cost and project displayed in its label', async () => {
  const calls = [];
  const model = createConfirmation(async (url, options) => {
    calls.push({url, options});
    return response(options.method === 'POST' ? {queued: true, ...expected} : status({stage: 'confirm_tracking'}));
  });
  model.updateStatus(status());
  for (const displayed of [null, {}, {remote_project_id: 'remote-two', estimated_cost: '4389'},
    {remote_project_id: 'remote-one', estimated_cost: '5000'}]) {
    assert.equal(await model.confirmDisplayed('local-one', displayed), false);
  }
  assert.equal(calls.length, 0);
  assert.equal(await model.confirmDisplayed('local-one',
    {remote_project_id: 'remote-one', estimated_cost: '4389'}), true);
  assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
  assert.equal(await model.confirmDisplayed('local-one',
    {remote_project_id: 'remote-one', estimated_cost: '4389'}), false);
});

test('a corrected GGAB estimate unlocks the action only at the newly displayed amount', async () => {
  const calls = [];
  const model = createConfirmation(async (url, options) => {
    calls.push({url, options});
    return response(options.method === 'POST' ?
      {queued: true, remote_project_id: 'remote-one', estimated_cost: JSON.parse(options.body).estimated_cost} :
      status());
  });
  model.updateStatus(status());
  await model.confirmDisplayed('local-one', {remote_project_id:'remote-one', estimated_cost:'4389'});
  model.updateStatus(status({estimated_cost:5000, estimate_changed_from:4389}));
  assert.equal(model.view('local-one').canConfirm, true);
  assert.match(model.view('local-one').message, /更正预计费用/);
  assert.equal(await model.confirmDisplayed('local-one',
    {remote_project_id:'remote-one', estimated_cost:'4389'}), false);
  assert.equal(await model.confirmDisplayed('local-one',
    {remote_project_id:'remote-one', estimated_cost:'5000'}), true);
  assert.deepEqual(calls.filter(call => call.options.method === 'POST').map(call =>
    JSON.parse(call.options.body).estimated_cost), [4389, 5000]);
});

test('an old extension cannot enable the charge action', async () => {
  const model = createConfirmation(() => {throw new Error('must not submit');});
  model.updateStatus({...status(), extension_version:'1.4.0'});
  assert.equal(model.view('local-one').canConfirm, false);
  assert.match(model.view('local-one').reason, /需要更新/);
  assert.equal(await model.confirmDisplayed('local-one',
    {remote_project_id:'remote-one', estimated_cost:'4389'}), false);
});

test('a rejected local request displays the actual error and refreshes the fee, without replaying', async () => {
  const calls = [];
  const model = createConfirmation(async (url, options) => {
    calls.push({url, options});
    return options.method === 'POST' ? response({detail: '费用或远端项目已变化，请重新核对'}, 409) :
      response(status({estimated_cost: 5000}));
  });
  model.updateStatus(status());
  await model.confirm('local-one', expected);
  assert.equal(calls.length, 2);
  assert.match(model.view('local-one').message, /费用或远端项目已变化/);
  assert.equal(model.view('local-one').flow.estimated_cost, 5000);
  assert.equal(await model.confirm('local-one', expected), false);
  assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
});

test('lost/5xx/malformed confirmation responses stay locked even if the next status read looks unchanged', async () => {
  for (const result of ['network', 'server', 'malformed']) {
    let posts = 0;
    const model = createConfirmation(async (url, options) => {
      if (options.method !== 'POST') return response(status());
      posts++;
      if (result === 'network') throw new Error('network');
      return result === 'server' ? response({}, 500) : response({queued: true, ...expected, remote_project_id: 'other'});
    });
    model.updateStatus(status());
    await model.confirm('local-one', expected);
    assert.equal(model.view('local-one').operation, 'unknown');
    assert.equal(await model.confirm('local-one', expected), false);
    assert.equal(posts, 1);
    model.updateStatus(status({stage: 'confirm_tracking'}));
    assert.equal(model.view('local-one').canConfirm, false);
  }
});

test('confirming one project does not authorize any other project', async () => {
  const calls = [];
  const data = {...status(), workflows: [flow, {...flow, project_id: 'local-two', remote_project_id: 'remote-two'}]};
  const model = createConfirmation(async (url, options) => {
    calls.push({url, options});
    return response(options.method === 'POST' ? {queued: true, ...expected} : data);
  });
  model.updateStatus(data);
  await model.confirm('local-one', expected);
  assert.equal(model.view('local-two').canConfirm, true);
  assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
});

test('the rendered CONFIRM button is wired to the local endpoint and treats project names as text', async () => {
  class Node {
    constructor(tag) {this.tag = tag; this.children = []; this.textContent = '';}
    append(...children) {this.children.push(...children);}
    replaceChildren(...children) {this.children = children;}
    setAttribute() {}
  }
  const calls = [], page = {window:{}, document:{createElement:tag=>new Node(tag)},
    fetch:async (url, options) => {
      calls.push({url,options});
      return response(options.method === 'POST' ? {queued:true,...expected} : status({stage:'confirm_tracking'}));
    }};
  vm.runInNewContext(fs.readFileSync(__dirname+'/../static/tracker-confirm.js','utf8'),page);
  const ui = page.window.GGABConfirmation, target = new Node('div');
  const find = (node, tag) => node.tag===tag ? node : node.children.map(child=>find(child,tag)).find(Boolean);
  const render = () => ui.render(target,'local-one','<img src=x onerror=submit()>');
  ui.subscribe(render);
  ui.updateStatus(status());
  assert.equal(calls.length,0);
  assert.equal(find(target,'button').textContent,'确认提交');
  assert.equal(find(target,'button').disabled,false);
  assert.equal(find(target,'p').textContent,'<img src=x onerror=submit()>');
  await find(target,'button').onclick();
  assert.equal(calls.filter(call=>call.options.method==='POST').length,1);
  assert.equal(calls[0].url,'/api/bridge/confirm/local-one');
  assert.equal(find(target,'button').disabled,true);
  assert.equal(find(target,'button').textContent,'正在提交…');
});
