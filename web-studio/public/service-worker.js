// OpenViking Studio service worker.
//
// Minimal pass-through SW whose only purpose is to satisfy the PWA
// installability criteria (Chrome requires a registered SW with a fetch
// handler). We do NOT cache application code or API responses, because:
//   - The OV server already sets Cache-Control: no-store on /studio HTML
//   - Cached SPA shells get out of sync with the bundled assets on upgrade
//   - All API calls must hit the live server for auth and freshness.
//
// If/when we want offline support, switch to a workbox-based bundle via
// vite-plugin-pwa.

self.addEventListener('install', (event) => {
  // Activate this SW immediately; no precache step.
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim())
})

self.addEventListener('fetch', () => {
  // Intentional no-op: let the browser handle the request normally.
})
