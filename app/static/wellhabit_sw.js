const MEDIAPIPE_CACHE = 'wellhabit-mediapipe-v2';
const CACHE_ALLOWLIST = new Set([MEDIAPIPE_CACHE]);
const CACHE_HOSTS = new Set(['cdn.jsdelivr.net', 'storage.googleapis.com']);

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) => Promise.all(
      names.filter((name) => !CACHE_ALLOWLIST.has(name)).map((name) => caches.delete(name))
    ))
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  const isLocalPoseAsset = url.origin === self.location.origin && url.pathname.includes('/static/break_assets/pose_landmarker_lite.task');
  const isMediaPipeAsset = isLocalPoseAsset || (CACHE_HOSTS.has(url.hostname) && (
    url.pathname.includes('/@mediapipe/tasks-vision') ||
    url.pathname.includes('/mediapipe-models/face_landmarker') ||
    url.pathname.includes('/mediapipe-models/pose_landmarker')
  ));
  if (!isMediaPipeAsset || event.request.method !== 'GET') return;

  event.respondWith(
    caches.open(MEDIAPIPE_CACHE).then(async (cache) => {
      const cached = await cache.match(event.request);
      const fetchOptions = url.origin === self.location.origin ? undefined : { mode: 'cors', credentials: 'omit' };
      const networkPromise = fetch(event.request, fetchOptions).then((response) => {
        // Never cache opaque responses; their real status is unreadable, so a CDN error
        // could otherwise poison the MediaPipe cache until the user clears site data.
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
