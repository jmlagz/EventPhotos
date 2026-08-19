const CACHE_NAME = "eventphotos-static-v1";

const STATIC_ASSETS = [
    "/static/eventos/pwa/manifest.json",
    "/static/eventos/pwa/icons/eventphotos-icon-192.png",
    "/static/eventos/pwa/icons/eventphotos-icon-512.png"
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(STATIC_ASSETS))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) =>
            Promise.all(
                cacheNames
                    .filter((cacheName) => cacheName !== CACHE_NAME)
                    .map((cacheName) => caches.delete(cacheName))
            )
        ).then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const request = event.request;

    if (request.method !== "GET") {
        return;
    }

    const url = new URL(request.url);

    if (url.origin !== self.location.origin) {
        return;
    }

    if (
        url.pathname.startsWith("/static/eventos/pwa/")
    ) {
        event.respondWith(
            caches.match(request).then((cachedResponse) => {
                return cachedResponse || fetch(request);
            })
        );
    }
});