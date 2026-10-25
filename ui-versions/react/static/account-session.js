"use strict";
(() => {
  const nativeFetch = window.fetch.bind(window);
  const path = window.YuxuPath;
  const signIn = () => location.replace(path('/login'));
  const ready = nativeFetch(path('/api/me'), {cache:'no-store'}).then(async r => {
    if (!r.ok) {signIn(); throw new Error('请登录本地账号');}
    const user = await r.json(); window.YuxuUser = user; return user;
  });
  window.YuxuAuthReady = ready;
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
    if (url.origin !== location.origin) return nativeFetch(input, options);
    const user = await ready;
    const method = String(options.method || input.method || 'GET').toUpperCase();
    const headers = new Headers(options.headers || input.headers || {});
    if (!['GET','HEAD','OPTIONS'].includes(method)) {headers.set('X-CSRF-Token', user.csrf); headers.set('X-Yuxu-Local','1');}
    const target = typeof input === 'string' || input instanceof URL ? path(input) : new Request(path(input.url), input);
    const response = await nativeFetch(target, {...options, headers});
    if (response.status === 401) signIn();
    return response;
  };
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function(body) {
    const xhr = this;
    ready.then(user => {xhr.setRequestHeader('X-CSRF-Token',user.csrf); originalSend.call(xhr,body);}).catch(() => {});
  };
  ready.then(user => {const showUser=()=>{
    document.title=user.email+' · '+document.title;
    const sidebar = document.getElementById('sidebar');
    if (sidebar) {
      const account = document.createElement('a'); account.className='nav-item';account.href=path('/account');account.textContent='账号 · ' + user.email;sidebar.append(account);
      const other = document.createElement('a'); other.className='nav-item';other.href=path('/login?new=1');other.target='_blank';other.rel='noopener';other.textContent='＋ 登录其他账号';sidebar.append(other);
    }
    document.querySelectorAll('.profile').forEach(node => {node.textContent = user.email;});
  };if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',showUser);else showUser();}).catch(() => {});
  // Only this login context is checked; another account's tab never changes it.
  setInterval(async () => {
    try {const r=await nativeFetch(path('/api/me'),{cache:'no-store'});if(!r.ok)return signIn();const me=await r.json();const user=await ready;if(me.id!==user.id||me.csrf!==user.csrf)location.replace(path('/insights/match-breakdown'));}catch(_){}
  },15000);
})();
