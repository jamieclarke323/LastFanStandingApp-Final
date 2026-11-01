// Service worker for Last Fan Standing push notifications.
// Handles incoming push events and notification clicks.

self.addEventListener('install', function (event) {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(self.clients.claim());
});

// Only ever open pages on this site. Anything else (or a malformed value) falls back to the home page.
function safeTargetUrl(candidate) {
  try {
    const url = new URL(candidate || '/', self.location.origin);
    if (url.origin !== self.location.origin) {
      return new URL('/', self.location.origin).href;
    }
    return url.href;
  } catch (err) {
    return new URL('/', self.location.origin).href;
  }
}

self.addEventListener('push', function (event) {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (err) {
    payload = { title: 'Last Fan Standing', body: event.data ? event.data.text() : '' };
  }

  const title = payload.title || 'Last Fan Standing';
  const options = {
    body: payload.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    data: { url: safeTargetUrl(payload.url) },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  const targetUrl = safeTargetUrl(event.notification.data && event.notification.data.url);

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clientList) {
      // Prefer a window already showing the target page, otherwise reuse any open app window.
      const exact = clientList.find(function (client) { return client.url === targetUrl && 'focus' in client; });
      if (exact) {
        return exact.focus();
      }
      const anyWindow = clientList.find(function (client) { return 'focus' in client; });
      if (anyWindow) {
        // navigate() only works for pages this worker controls; otherwise open a fresh window.
        return anyWindow.focus().then(function (focused) {
          if (focused && 'navigate' in focused) {
            return focused.navigate(targetUrl).catch(function () {
              return self.clients.openWindow ? self.clients.openWindow(targetUrl) : focused;
            });
          }
          return self.clients.openWindow ? self.clients.openWindow(targetUrl) : focused;
        });
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
