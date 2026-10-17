"use strict";
(() => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const storage = {
    get(key) { try { return JSON.parse(localStorage.getItem(key)); } catch (_) { return null; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {} },
    remove(key) { try { localStorage.removeItem(key); } catch (_) {} }
  };
  async function hash(blob) {
    const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
    return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
  }
  async function request(url, options = {}) {
    for (let attempt = 0; ; attempt++) {
      try {
        const response = await fetch(url, {cache: 'no-store', ...options});
        let result;
        try { result = await response.json(); } catch (_) { result = {}; }
        if (response.ok) return result;
        const error = new Error(typeof result.detail === 'string' ? result.detail : `上传失败（${response.status}）`);
        error.status = response.status;
        throw error;
      } catch (error) {
        if (attempt >= 3 || (error.status && ![408, 429, 502, 503, 504].includes(error.status))) throw error;
        await sleep(500 * 2 ** attempt);
      }
    }
  }
  const jsonOptions = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  window.GGABUploadVideo = async (file, {sessionId = null, onProgress = () => {}} = {}) => {
    if (!file.size || file.size > 50 * 1024 ** 3) throw new Error('请选择大于 0 且不超过 50 GB 的视频');
    const user = window.GGABAuthReady ? await window.GGABAuthReady : {id: 'local'};
    const fingerprint = await hash(new Blob([file.slice(0, 65536), file.slice(Math.max(0, file.size - 65536)),
                                             JSON.stringify([file.name, file.size, file.lastModified])]));
    const key = 'ggab-upload:' + JSON.stringify([user.id, sessionId, fingerprint]);
    let saved = storage.get(key), state;
    if (saved?.upload_id) {
      try { state = await request('/api/uploads/' + saved.upload_id); }
      catch (error) {
        if (![404, 410].includes(error.status)) throw error;
        storage.remove(key); saved = null;
      }
    }
    if (!state) {
      saved = saved || {request_id: crypto.randomUUID().replaceAll('-', '')};
      storage.set(key, saved);
      state = await request('/api/uploads', jsonOptions({filename: file.name, size_bytes: file.size,
                            match_id: sessionId, request_id: saved.request_id}));
      saved.upload_id = state.upload_id;
      storage.set(key, saved);
    }
    if (state.size_bytes !== file.size) throw new Error('未完成上传与所选视频大小不同');
    const report = phase => onProgress({loaded: state.received_bytes, total: file.size, phase});
    // Revalidate every skipped chunk; filename/size/sample hashes alone are insufficient.
    for (let index = 0; index < state.chunks.length; index++) {
      report('checking');
      const chunk = state.chunks[index], start = index * state.chunk_size;
      if (await hash(file.slice(start, start + chunk.size)) !== chunk.sha256) {
        throw new Error('所选文件与已上传片段不一致，请重新选择原视频');
      }
    }
    if (state.status === 'complete') {
      storage.remove(key); report('complete'); return state.result;
    }
    while (state.received_bytes < file.size) {
      const start = state.received_bytes, end = Math.min(start + state.chunk_size, file.size);
      const chunk = file.slice(start, end), digest = await hash(chunk);
      report('uploading');
      state = await request('/api/uploads/' + state.upload_id, {method: 'PUT',
        headers: {'Content-Type': 'application/octet-stream', 'Content-Range': `bytes ${start}-${end - 1}/${file.size}`,
                  'X-Chunk-SHA256': digest}, body: chunk});
      report('uploading');
    }
    report('verifying');
    let result = await request('/api/uploads/' + state.upload_id + '/complete', {method: 'POST'});
    let polls = 0;
    while (result.upload_id) {
      if (result.status === 'complete') { result = result.result; break; }
      if (result.status === 'failed') throw new Error(result.error || '视频验证失败，请重新选择视频重试');
      await sleep(1500);
      // Requeue after a service restart; completed requests return the same receipt.
      result = ++polls % 20 === 0
        ? await request('/api/uploads/' + state.upload_id + '/complete', {method: 'POST'})
        : await request('/api/uploads/' + state.upload_id);
    }
    storage.remove(key); report('complete'); return result;
  };
})();
