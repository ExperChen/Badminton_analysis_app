const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;
const source = fs.readFileSync(__dirname + '/../static/video-upload.js', 'utf8');
const digest = bytes => require('node:crypto').createHash('sha256').update(bytes).digest('hex');
function video(bytes, name = 'match.mp4') {
  const blob = new Blob([bytes]);
  Object.defineProperties(blob, {name: {value: name}, lastModified: {value: 123}});
  return blob;
}
function client({size = 4, storage = new Map(), failAt = null} = {}) {
  const states = new Map(), puts = [], requests = [];
  let serial = 0, broken = failAt;
  const window = {YuxuAuthReady: Promise.resolve({id: 'alice'})};
  const response = (body, status = 200) => ({ok: status < 300, status, json: async () => structuredClone(body)});
  const fetch = async (url, options = {}) => {
    requests.push([url, options]);
    if (url === '/api/uploads') {
      const body = JSON.parse(options.body), id = String(++serial);
      const state = {upload_id: id, size_bytes: body.size_bytes, chunk_size: size, chunks: [],
                     received_bytes: 0, status: 'uploading', result: null};
      states.set(id, state); return response(state, 201);
    }
    const id = url.split('/')[3], state = states.get(id);
    if (url.endsWith('/complete')) {
      state.status = 'complete'; state.result = {match_id: 'video-' + id, storage: 'google_drive'};
      return response(state.result);
    }
    if (options.method === 'PUT') {
      const start = Number(/^bytes (\d+)-/.exec(options.headers['Content-Range'])[1]);
      if (start === broken) throw new Error('offline');
      const content = Buffer.from(await options.body.arrayBuffer());
      assert.equal(options.headers['X-Chunk-SHA256'], digest(content));
      puts.push(start);
      state.received_bytes += content.length;
      state.chunks.push({size: content.length, sha256: digest(content)});
    }
    return response(state);
  };
  vm.runInNewContext(source, {window, crypto, Blob, Uint8Array, JSON, fetch,
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    setTimeout: fn => {fn(); return 0;}});
  return {window, puts, states, requests, storage, resume: () => {broken = null;}};
}
test('uploads bounded chunks and persists a resumable request scoped to the account', async () => {
  const c = client(), progress = [];
  const result = await c.window.YuxuUploadVideo(video('abcdefghij'), {sessionId: 'mine', onProgress: event => progress.push(event)});
  assert.deepEqual(c.puts, [0, 4, 8]);
  assert.equal(result.storage, 'google_drive');
  const init = JSON.parse(c.requests[0][1].body);
  assert.equal(init.match_id, 'mine'); assert.match(init.request_id, /^[0-9a-f]{32}$/);
  assert.equal(progress.at(-1).phase, 'complete'); assert.equal(c.storage.size, 0);
});
test('after a failed transfer it verifies saved chunks and resumes from the server offset', async () => {
  const c = client({failAt: 4}), file = video('abcdefghij');
  await assert.rejects(c.window.YuxuUploadVideo(file), /offline/);
  assert.deepEqual(c.puts, [0]); assert.equal(c.storage.size, 1);
  c.resume();
  await c.window.YuxuUploadVideo(file);
  assert.deepEqual(c.puts, [0, 4, 8]);
  assert.equal(c.states.size, 1); assert.equal(c.storage.size, 0);
});
test('same filename size timestamp and edge samples cannot splice a different middle into an upload', async () => {
  const c = client({size: 65536, failAt: 131072});
  const bytes = Buffer.alloc(262144, 'a'), file = video(bytes);
  await assert.rejects(c.window.YuxuUploadVideo(file), /offline/);
  const changed = Buffer.from(bytes); changed[70000] = 98;
  c.resume();
  await assert.rejects(c.window.YuxuUploadVideo(video(changed)), /已上传片段不一致/);
  assert.deepEqual(c.puts, [0, 65536]);
});
