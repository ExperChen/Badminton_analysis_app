"use strict";
(() => {
  const match = /^\/s\/[0-9a-f]{32}(?=\/|$)/.exec(location.pathname);
  const base = match ? match[0] : '';
  window.GGABBase = base;
  window.GGABRoute = () => location.pathname.slice(base.length) || '/';
  window.GGABPath = value => {
    if (value == null) return value;
    const original = String(value);
    if (!base) return original;
    const url = new URL(original, location.href);
    if (!['http:','https:'].includes(url.protocol) || url.origin !== location.origin || /^\/s\/[0-9a-f]{32}(?:\/|$)/.test(url.pathname)) return original;
    return base + url.pathname + url.search + url.hash;
  };
  for (const method of ['pushState','replaceState']) {
    const original = history[method].bind(history);
    history[method] = (state, title, url) => original(state, title, url == null ? url : window.GGABPath(url));
  }
  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...args) {
    return originalOpen.call(this, method, window.GGABPath(url), ...args);
  };
})();
