const MEDIAPIPE_CACHE = 'wellhabit-mediapipe-v1';
const CACHE_HOSTS = new Set(['cdn.jsdelivr.net', 'storage.googleapis.com']);

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  const isMediaPipeAsset = CACHE_HOSTS.has(url.hostname) && (
    url.pathname.includes('/@mediapipe/tasks-vision') ||
    url.pathname.includes('/mediapipe-models/face_landmarker')
  );
  if (!isMediaPipeAsset || event.request.method !== 'GET') return;
  event.respondWith(
    caches.open(MEDIAPIPE_CACHE).then(async (cache) => {
      const cached = await cache.match(event.request);
      if (cached) return cached;
      const response = await fetch(event.request);
      if (response && (response.ok || response.type === 'opaque')) {
        cache.put(event.request, response.clone()).catch(() => {});
      }
      return response;
    })
  );
});
