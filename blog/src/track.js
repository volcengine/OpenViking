/**
 * Minimal usage beacons to a Viewer-Counter instance
 * (https://github.com/t0saki/Viewer-Counter).
 *
 * Disabled unless VITE_COUNTER_URL is set at build time, so default/local/PR
 * builds carry no tracking. Page views are reported with the route pathname;
 * document.referrer (origin + pathname only) is attached on the first view so
 * the traffic source is captured for the SPA.
 *
 * The `site` dimension is derived from the current hostname so the blog served
 * on openviking.ai and openviking.net is counted separately.
 *
 * Beacons are fire-and-forget: failures are swallowed and never surface to the
 * page.
 */

const COUNTER_URL = (import.meta.env.VITE_COUNTER_URL || '').replace(/\/+$/, '');

function resolveSite() {
  if (typeof window === 'undefined') return 'blog';
  const host = window.location.hostname;
  if (host === 'openviking.ai' || host.endsWith('.openviking.ai')) return 'blog-ai';
  if (host === 'openviking.net' || host.endsWith('.openviking.net')) return 'blog-net';
  return 'blog';
}

function beacon(page, ref) {
  if (!COUNTER_URL) return;
  const params = new URLSearchParams({ page, site: resolveSite() });
  if (ref) params.set('ref', ref);
  const url = `${COUNTER_URL}/api/v1/hit?${params.toString()}`;
  try {
    // Prefer sendBeacon; fall through to the Image() beacon whenever it is
    // unavailable, throws, or reports the payload was not queued.
    if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
      if (navigator.sendBeacon(url)) return;
    }
  } catch {
    // fall through to the Image() fallback below
  }
  try {
    new Image().src = url;
  } catch {
    // Tracking must never break the page.
  }
}

/** Reduce the referrer to origin + pathname so query strings never leave the page. */
function normalizeReferrer(raw) {
  if (!raw) return undefined;
  try {
    const url = new URL(raw);
    return `${url.origin}${url.pathname}`;
  } catch {
    return undefined;
  }
}

let lastPath = null;
let referrerSent = false;

/**
 * Report a page view. Consecutive duplicates (e.g. query-only navigations such
 * as language switches resolving to the same pathname) are collapsed. The first
 * call attaches document.referrer as the traffic source — the beacon's own
 * Referer header would only echo the blog URL.
 */
export function trackPageView(pathname) {
  if (pathname === lastPath) return;
  lastPath = pathname;
  let ref;
  if (!referrerSent) {
    referrerSent = true;
    ref = normalizeReferrer(document.referrer);
  }
  beacon(pathname, ref);
}
