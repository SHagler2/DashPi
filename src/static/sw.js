// DashPi Service Worker
const CACHE_NAME = 'dashpi-v1';
const STATIC_ASSETS = [
  '/',
  '/static/styles/main.css',
  '/static/scripts/dark_mode.js',
  '/static/scripts/image_modal.js'
];

// Install - cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// Activate - clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.filter(key => key !== CACHE_NAME)
            .map(key => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch - network first, fall back to cache
self.addEventListener('fetch', event => {
  // Skip non-GET requests
  if (event.request.method !== 'GET') return;

  // For API calls and images, always go to network (we want fresh data)
  if (event.request.url.includes('/api/') ||
      event.request.url.includes('current_image')) {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then(response => {
        // Clone and cache successful responses
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => {
        // Network failed, try cache
        return caches.match(event.request);
      })
  );
});
