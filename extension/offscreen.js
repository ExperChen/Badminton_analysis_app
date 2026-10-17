chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || message.target !== 'offscreen' || message.type !== 'parse') return;
  const doc = new DOMParser().parseFromString(message.html, 'text/html');
  // Inert document only: never execute remote scripts or parse hydration data as visible status.
  doc.querySelectorAll('script,style,template,noscript,nav,[hidden],[aria-hidden="true"]').forEach(n => n.remove());
  const main = doc.querySelector('main');
  const diagnostic = {title: doc.title.slice(0, 200), elements: []};
  if (main) {
    for (const node of [...main.querySelectorAll('h1,h2,h3,button,a,[role="status"],[data-project-status]')].slice(0, 80)) {
      const text = node.textContent.replace(/\s+/g, ' ').trim().slice(0, 160);
      let path = null;
      if (node.tagName === 'A' && node.getAttribute('href')) {
        try {const url = new URL(node.getAttribute('href'), 'https://app.getgoodatbadminton.com'); if(url.origin === 'https://app.getgoodatbadminton.com') path = url.pathname;} catch {}
      }
      diagnostic.elements.push({tag:node.tagName.toLowerCase(), role:node.getAttribute('role'), text, path,
        data:[...node.attributes].map(a=>a.name).filter(n=>n.startsWith('data-')).slice(0,12)});
    }
  }
  if (!main) {reply({error: 'GGAB 需要动态页面或页面结构已变化；后台适配器尚未验证，保留本地状态', diagnostic}); return;}
  // Conservative candidate adapter, not yet verified against authenticated GGAB HTML.
  // Dedicated semantic badges only: never interpret player names or headings as status.
  const badges = [...main.querySelectorAll('[role="status"], [data-project-status]')];
  const states = new Set(badges.map(n => GGABStatus.parse(n.textContent)).filter(Boolean));
  if (states.size !== 1) {reply({error: '后台 HTML 无唯一明确项目状态标记；需要核实 GGAB 接口，保留本地状态', diagnostic}); return;}
  reply({status: [...states][0], diagnostic});
});
