"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let mode = 'login', submitting = false;
  function setMode(value) {
    mode = value;
    const reset = value === 'reset', confirm = ['setup', 'register', 'reset'].includes(value);
    $('heading').textContent = {login:'登录本地账号',register:'注册普通账号',setup:'初始化管理员',reset:'激活账号 / 重置密码'}[value];
    $('intro').textContent = value === 'setup' ? '首次使用请在本机设置管理员。已有视频和比赛不会自动分配，登录后可核验认领。' : reset ? '输入管理员私下提供的激活码或重置码，十五分钟内有效，仅可使用一次。' : value==='register' ? '自行设置密码即可注册。账号由本站管理员管理，初始分析额度为 0；请使用自己的邮箱和独立密码。' : '使用本系统邮箱和密码登录，或注册自己的普通账号。';
    $('email').hidden = reset; $('email').required = !reset;
    document.querySelector('label[for="email"]').hidden = reset;
    $('reset-token').hidden = !reset; $('reset-label').hidden = !reset; $('reset-token').required = reset;
    $('confirm-password').hidden = !confirm; $('confirm-label').hidden = !confirm; $('confirm-password').required = confirm;
    $('password').autocomplete = value === 'login' ? 'current-password' : 'new-password';
    $('submit').textContent = {login:'登录',register:'注册并登录',setup:'创建管理员',reset:'设置新密码'}[value];
    $('message').textContent = '';
  }
  document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => setMode(b.dataset.mode));
  $('auth-form').onsubmit = async e => {
    e.preventDefault(); if (submitting) return;
    if (mode !== 'login' && $('password').value !== $('confirm-password').value) { $('message').textContent = '两次输入的密码不一致'; return; }
    submitting = true; $('submit').disabled = true;
    try {
      const body = mode === 'reset' ? {token:$('reset-token').value,password:$('password').value} : {email:$('email').value,password:$('password').value};
      const r = await fetch(window.GGABPath('/api/auth/' + (mode === 'reset' ? 'password-reset/complete' : mode)), {method:'POST',headers:{'Content-Type':'application/json','X-GGAB-Local':'1'},body:JSON.stringify(body)});
      const data = await r.json(); if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请检查邮箱和密码（至少 12 个字符）');
      $('password').value = ''; $('confirm-password').value = '';
      if (mode === 'reset') {setMode('login'); $('message').textContent = '密码已更新，请重新登录';}
      else location.replace(window.GGABPath('/insights/match-breakdown'));
    } catch (error) { $('message').textContent = window.AppText?.error(error.message)||error.message; }
    finally { submitting = false; $('submit').disabled = false; }
  };
  fetch(window.GGABPath('/api/auth/status'), {cache:'no-store'}).then(r => r.json()).then(data => {
    if (!data.initialized) {setMode('setup'); $('modes').hidden = true;}
    const register=document.querySelector('[data-mode="register"]');if(register)register.hidden=!data.registration_open;
  }).catch(() => {$('message').textContent = '无法连接本地服务';});
})();
