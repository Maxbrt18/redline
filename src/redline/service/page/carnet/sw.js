// The notebook without waiting for the network: the shell alone (layout, fonts,
// script; no data) is kept on the device and opens at once. It revalidates behind, and
// the next version serves the next opening. Data (/carnet-api) never goes through here:
// it stays the page's business.
const CACHE = 'carnet-shell-v1';

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.add('/carnet')).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (event.request.mode !== 'navigate' || url.pathname !== '/carnet') return;
  event.respondWith(caches.open(CACHE).then(async cache => {
    const kept = await cache.match('/carnet');
    const fresh = fetch('/carnet', {cache: 'no-cache', credentials: 'same-origin'}).then(response => {
      if (response.ok) cache.put('/carnet', response.clone());
      return response;
    });
    if (!kept) return fresh;
    event.waitUntil(fresh.catch(() => {}));
    return kept;
  }));
});
