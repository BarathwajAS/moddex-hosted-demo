const CACHE = 'moddex-pitch-v1';
const STATIC = [
  '/ui/styles.css', '/ui/app.js', '/manifest.webmanifest',
  '/assets/brand/app-icon-192.png', '/assets/brand/app-icon-512.png'
];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(STATIC)));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key !== CACHE).map(key => caches.delete(key))
  )));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== location.origin ||
      url.pathname.startsWith('/api/') || url.pathname.startsWith('/img/') ||
      event.request.mode === 'navigate') return;
  event.respondWith(caches.match(event.request).then(hit => hit || fetch(event.request)));
});
