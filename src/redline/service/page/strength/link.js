/* The session's capability (from a Messenger link or the notebook's launch),
 * resolved by the server and kept on the phone. */
const TOKEN_RE = /^[A-Za-z0-9_-]{32}$/;
const snapshotKey = (token) => `redline:strength-link:${token}`;

function cachedSnapshot(token) {
  try {
    const cached = JSON.parse(localStorage.getItem(snapshotKey(token)) || 'null');
    if (cached && cached.session && cached.session.expires_at * 1000 >= Date.now()) {
      return cached;
    }
  } catch (error) { /* private mode or stale JSON */ }
  return null;
}

async function resolveToken(token) {
  const cached = cachedSnapshot(token);
  try {
    const response = await fetch('/strength-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    let result = {};
    try { result = await response.json(); } catch (error) { /* non-JSON failure */ }
    if (response.status === 410 || result.status === 'expired') throw new Error('expired');
    if (response.status >= 500 || response.status === 429) throw new Error('network');
    if (!response.ok || !result.session) throw new Error('forbidden');
    try {
      localStorage.setItem(snapshotKey(token), JSON.stringify(result));
    } catch (error) { /* private mode: online use still works */ }
    return result;
  } catch (error) {
    if (error && ['expired', 'forbidden'].includes(error.message)) throw error;
    if (cached) return cached;             // already opened: usable through an outage
    throw new Error('network');
  }
}

// Le jeton d'un fragment `#t=…` (lien Messenger), ou '' s'il n'en porte pas.
export function linkToken(hash) {
  const raw = decodeURIComponent(hash.replace(/^#/, ''));
  return raw.startsWith('t=') ? raw.slice(2) : '';
}

export async function resolveLink(token) {
  if (!TOKEN_RE.test(token || '')) throw new Error('fragment');
  return { token, ...await resolveToken(token) };
}
