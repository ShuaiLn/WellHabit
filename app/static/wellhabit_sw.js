const MEDIAPIPE_CACHE = 'wellhabit-mediapipe-v3';
const CACHE_ALLOWLIST = new Set([MEDIAPIPE_CACHE]);

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) => Promise.all(
      names.filter((name) => !CACHE_ALLOWLIST.has(name)).map((name) => caches.delete(name))
    ))
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Never touch dynamic app routes. Only static MediaPipe assets should
  // ever be served from this worker cache.
  if (url.origin === self.location.origin && (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/profile') ||
    url.pathname.startsWith('/tasks/')
  )) {
    return;
  }

  const isSameOrigin = url.origin === self.location.origin;
  const isLocalMediaPipeRuntime = isSameOrigin && url.pathname.includes('/static/vendor/mediapipe/');
  const isLocalTaskModel = isSameOrigin &&
    url.pathname.startsWith('/static/break_assets/') &&
    url.pathname.endsWith('.task');
  const isMediaPipeAsset = isLocalMediaPipeRuntime || isLocalTaskModel;
  if (!isMediaPipeAsset || event.request.method !== 'GET') return;

  event.respondWith(
    caches.open(MEDIAPIPE_CACHE).then(async (cache) => {
      const cached = await cache.match(event.request);
      const networkPromise = fetch(event.request).then((response) => {
        // Cache only successful same-origin static responses.
        if (response && response.ok && response.type !== 'opaque') {
          cache.put(event.request, response.clone()).catch(() => {});
        }
        return response;
      });

      if (cached) {
        networkPromise.catch(() => {});
        return cached;
      }
      return networkPromise;
    })
  );
});
