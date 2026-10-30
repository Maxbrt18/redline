// A setting kept on this device. Without storage (private browsing, full device),
// reads return null and writes do nothing: the page works without it.
export function remembered(key, value) {
  try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); }
  catch { return null; }
  return null;
}
