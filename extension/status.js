/* Shared, conservative parser: conflicting or unknown states never overwrite a link. */
globalThis.GGABStatus = {
  parse(text) {
    const lines = text.split(/\n/).map(s => s.trim().toLowerCase()).filter(Boolean);
    const states = new Set();
    if (lines.includes('finalized')) states.add('finalized');
    if (lines.some(s => ['in queue', 'processing', 'tracking in progress'].includes(s))) states.add('processing');
    if (lines.includes('setup') || lines.includes('tag automatically?')) states.add('review');
    if (lines.includes('video unavailable')) states.add('failed');
    return states.size === 1 ? [...states][0] : null;
  },
  id(url) {
    try { const u = new URL(url); return u.origin === 'https://app.getgoodatbadminton.com' ? /^\/tracker\/([a-f0-9-]{36})\/?$/i.exec(u.pathname)?.[1] : null; }
    catch { return null; }
  }
};
