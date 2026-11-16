// TendAI service worker: lets the app open (and say it is offline) with no network. It never stores anything from /api, so no tender,
// bid, paper or account data is ever kept by the browser's cache. Only the page shell and its fixed, hashed files are.
const SHELL = 'tendai-shell-v1';
const ASSETS = 'tendai-assets-v1';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(['/', '/manifest.webmanifest'])).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== ASSETS).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return; // not ours: the network handles it, nothing is stored

  if (req.mode === 'navigate') {
    // the page itself: always try the network (so a new release shows at once), fall back to the stored shell when offline
    event.respondWith(fetch(req).then((res) => { const copy = res.clone(); caches.open(SHELL).then((c) => c.put('/', copy)); return res; }).catch(() => caches.match('/')));
    return;
  }
  if (url.pathname.startsWith('/assets/')) {
    // files named by a hash of their contents never change: the stored copy is always right
    event.respondWith(caches.open(ASSETS).then(async (c) => {
      const hit = await c.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) c.put(req, res.clone());
      return res;
    }));
    return;
  }
  if (url.pathname.startsWith('/icons/') || url.pathname === '/manifest.webmanifest') {
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
  }
});
