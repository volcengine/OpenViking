// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import http from 'node:http';
import { safeError } from './logger.mjs';

// Local-only observation surface: /api/activity JSON feed plus health probes.
// GET only, loopback only, same-origin only. Sits beside the harness — nothing
// here can reach the inbound pipe or the model.

const SECURITY_HEADERS = Object.freeze({
  'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; worker-src 'none'; manifest-src 'none'; media-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'no-referrer',
  'cross-origin-resource-policy': 'same-origin',
  'cross-origin-opener-policy': 'same-origin',
  'permissions-policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
  'x-frame-options': 'DENY',
});

function loopbackHostname(value) {
  if (typeof value !== 'string' || /[@/\\\s]/.test(value)) return false;
  try {
    const hostname = new URL(`http://${value}`).hostname.toLowerCase();
    return ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(hostname);
  } catch {
    return false;
  }
}

function requestIsSameOrigin(request) {
  const host = request.headers.host;
  if (!host || !loopbackHostname(host)) return false;
  const origin = request.headers.origin;
  if (!origin) return true;
  try {
    const parsed = new URL(origin);
    return parsed.protocol === 'http:' && parsed.host.toLowerCase() === host.toLowerCase();
  } catch {
    return false;
  }
}

function send(response, status, contentType, body, extraHeaders = {}) {
  const payload = typeof body === 'string' ? body : JSON.stringify(body);
  response.writeHead(status, {
    ...SECURITY_HEADERS,
    'cache-control': 'no-store',
    'content-type': contentType,
    'content-length': Buffer.byteLength(payload),
    ...extraHeaders,
  });
  response.end(payload);
}

// serviceSnapshot: ({ includeState }) => { healthy, ready, body } — owned by
// the daemon, which holds the subscriber/runtime state this surface reports.
export async function startHttpApi({ config, store, logger, serviceSnapshot }) {
  const server = http.createServer((request, response) => {
    try {
      if (!requestIsSameOrigin(request)) {
        send(response, 403, 'application/json; charset=utf-8', { error: 'forbidden' });
        return;
      }
      if (request.method !== 'GET') {
        send(response, 405, 'application/json; charset=utf-8', { error: 'method_not_allowed' }, { allow: 'GET' });
        return;
      }
      if (!request.url || request.url.length > 2048) {
        send(response, 414, 'application/json; charset=utf-8', { error: 'request_target_too_long' });
        return;
      }
      if (!request.url.startsWith('/') || request.url.startsWith('//')) {
        send(response, 400, 'application/json; charset=utf-8', { error: 'invalid_request_target' });
        return;
      }
      let url;
      try {
        url = new URL(request.url, 'http://localhost');
      } catch {
        send(response, 400, 'application/json; charset=utf-8', { error: 'invalid_request_target' });
        return;
      }

      if (url.pathname === '/api/activity') {
        const allowed = new Set(['limit', 'before', 'after', 'epoch']);
        if ([...url.searchParams.keys()].some((key) => !allowed.has(key))
          || url.searchParams.getAll('limit').length > 1
          || url.searchParams.getAll('before').length > 1
          || url.searchParams.getAll('after').length > 1
          || (url.searchParams.has('before') && url.searchParams.has('after'))
          || url.searchParams.getAll('epoch').length > 1) {
          send(response, 400, 'application/json; charset=utf-8', { error: 'invalid_query' });
          return;
        }
        const limitText = url.searchParams.get('limit');
        const beforeText = url.searchParams.get('before');
        const afterText = url.searchParams.get('after');
        const epochText = url.searchParams.get('epoch');
        if ((limitText !== null && !/^[1-9]\d*$/.test(limitText))
          || (beforeText !== null && !/^[1-9]\d*$/.test(beforeText))
          || (afterText !== null && !/^(?:0|[1-9]\d*)$/.test(afterText))
          || (epochText !== null && epochText !== 'current' && !/^[1-9]\d{0,8}$/.test(epochText))) {
          send(response, 400, 'application/json; charset=utf-8', { error: 'invalid_query' });
          return;
        }
        const limit = limitText === null ? 100 : Number(limitText);
        const beforeId = beforeText === null ? null : Number(beforeText);
        const afterId = afterText === null ? null : Number(afterText);
        const epoch = epochText === null
          ? null
          : epochText === 'current'
            ? store.getCognitionState().epoch
            : Number(epochText);
        if (!Number.isSafeInteger(limit) || limit > 100
          || (beforeId !== null && !Number.isSafeInteger(beforeId))
          || (afterId !== null && !Number.isSafeInteger(afterId))
          || (epoch !== null && !Number.isSafeInteger(epoch))) {
          send(response, 400, 'application/json; charset=utf-8', { error: 'invalid_query' });
          return;
        }
        const feed = store.listActivities({ limit, beforeId, afterId, epoch });
        const service = serviceSnapshot({ includeState: false });
        send(response, 200, 'application/json; charset=utf-8', {
          generatedAt: Date.now(),
          service: {
            ready: service.ready,
            subscriber: service.body.subscriber,
            uptimeSeconds: service.body.uptimeSeconds,
          },
          overview: store.activityOverview(),
          sessions: store.listActivitySessions(),
          epoch,
          activities: feed.activities,
          nextCursor: feed.nextCursor,
          hasNewer: feed.hasNewer ?? false,
        });
        return;
      }

      if (['/health', '/ready'].includes(url.pathname) && url.searchParams.size === 0) {
        const service = serviceSnapshot();
        const ok = url.pathname === '/health' ? service.healthy : service.ready;
        send(response, ok ? 200 : 503, 'application/json; charset=utf-8', service.body);
        return;
      }

      send(response, 404, 'application/json; charset=utf-8', { error: 'not_found' });
    } catch (error) {
      logger.error('http_request_failed', {
        method: request.method,
        error: safeError(error),
      });
      if (!response.headersSent) {
        try {
          send(response, 500, 'application/json; charset=utf-8', { error: 'internal_error' });
        } catch {
          response.destroy();
        }
      } else {
        response.destroy();
      }
    }
  });
  await new Promise((resolve, reject) => {
    const onError = (error) => reject(error);
    server.once('error', onError);
    server.listen(config.port, config.host, () => {
      server.off('error', onError);
      resolve();
    });
  });
  return server;
}
