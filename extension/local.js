(() => {
  if(location.origin !== 'http://127.0.0.1:8000') return;
  if(globalThis.__ggabLocalCompanionLocalInstalled) return;
  globalThis.__ggabLocalCompanionLocalInstalled = true;
  document.documentElement.dataset.ggabCompanion = 'available';
  function wake() {
    chrome.runtime.sendMessage({type:'wake'}).catch(() => {});
  }
  window.addEventListener('pageshow', wake);
  document.addEventListener('ggab-wake', wake);
  document.addEventListener('visibilitychange', () => {if(!document.hidden) wake();});
  setInterval(wake, 60000);
  wake();
  document.addEventListener('ggab-token-sync', async () => {
    try {
      const context=/^\/s\/[0-9a-f]{32}(?=\/|$)/.exec(location.pathname)?.[0] || '';
      const me=await fetch(context+'/api/me',{cache:'no-store'});
      if(!me.ok || (await me.json()).role!=='admin')return;
      const result=await chrome.runtime.sendMessage({type:'sync-tokens'});
      document.dispatchEvent(new CustomEvent('ggab-token-synced',{detail:result?.ok?'ok':'error'}));
    } catch {document.dispatchEvent(new CustomEvent('ggab-token-synced',{detail:'error'}));}
  });
  document.addEventListener('ggab-connect', async () => {
    try {
      const context = /^\/s\/[0-9a-f]{32}(?=\/|$)/.exec(location.pathname)?.[0] || '';
      const me = await fetch(context + '/api/me', {cache:'no-store'});
      if (!me.ok) throw new Error('请管理员登录');
      const user = await me.json();
      const r = await fetch(context + '/api/bridge/pair', {method:'POST', headers:{'X-GGAB-Local':'1','X-CSRF-Token':user.csrf}});
      if(!r.ok) throw new Error('配对失败');
      const {token} = await r.json();
      const result = await chrome.runtime.sendMessage({type:'pair',token});
      document.dispatchEvent(new CustomEvent('ggab-connected', {detail:result?.ok ? 'ok':'error'}));
    } catch {document.dispatchEvent(new CustomEvent('ggab-connected', {detail:'error'}));}
  });
})();
