// Runs only on GGAB. Programmatic re-injection after an extension reload is
// safe: one listener and one timer per page. Credentials stay in browser memory.
(() => {
if (globalThis.__ggabLocalCompanionRemoteInstalled) return;
globalThis.__ggabLocalCompanionRemoteInstalled = true;
function readGGABSession() {
  try {
    const raw = localStorage.getItem('sb-adscteqgwmvherkctzfm-auth-token');
    const session = raw ? JSON.parse(raw) : null;
    const accessToken = session?.access_token;
    const userId = session?.user?.id;
    if (typeof accessToken !== 'string' || typeof userId !== 'string' || !/^[0-9a-f-]{36}$/i.test(userId)) {
      return {ok:false};
    }
    return {ok:true, accessToken, userId, expiresAt:Number(session.expires_at) || null};
  } catch {return {ok:false};}
}
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || message?.type !== 'ggab-session') return;
  reply(readGGABSession());
});
let lastSession;
function publishSession() {
  const session = readGGABSession();
  const signature = JSON.stringify(session);
  if (signature === lastSession) return;
  chrome.runtime.sendMessage({type:'ggab-session-updated', session}).then(() => {
    lastSession = signature;
  }).catch(() => {});
}
// Same-document auth changes do not fire the storage event; also observe them
// periodically so logout/account switching clears the background cache.
window.addEventListener('storage', publishSession);
window.addEventListener('pageshow', publishSession);
setInterval(publishSession, 15000);
publishSession();
})();
