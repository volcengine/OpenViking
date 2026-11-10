// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const MAX_REQUEST_TARGET_BYTES = 2_048;
const MAX_UPSTREAM_RESPONSE_BYTES = 8 * 1024 * 1024;

const SECURITY_HEADERS = Object.freeze({
  // style-src allows inline style attributes: the timeline view positions
  // hundreds of blocks via el.style.left/width/transform. Scripts stay 'self'.
  'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; worker-src 'none'; manifest-src 'none'; media-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'no-referrer',
  'cross-origin-resource-policy': 'same-origin',
  'cross-origin-opener-policy': 'same-origin',
  'permissions-policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
  'x-frame-options': 'DENY',
});

const STATIC_FILES = Object.freeze({
  '/': { file: 'index.html', contentType: 'text/html; charset=utf-8' },
  '/activity': { file: 'index.html', contentType: 'text/html; charset=utf-8' },
  '/activity.css': { file: 'activity.css', contentType: 'text/css; charset=utf-8' },
  '/activity.js': { file: 'activity.js', contentType: 'text/javascript; charset=utf-8' },
});

const PROXY_PATHS = new Set(['/api/activity', '/health', '/ready']);
const INSTANCE_QUERY_KEY = 'instance';
const EPOCH_QUERY_KEY = 'epoch';
const PAGE_QUERY_KEYS = new Set([INSTANCE_QUERY_KEY, EPOCH_QUERY_KEY]);
const EPOCH_QUERY_VALUE = /^(?:all|current|[1-9]\d{0,8})$/;

function noop() {}

const NULL_LOGGER = Object.freeze({
  debug: noop,
  info: noop,
  warn: noop,
  error: noop,
});

function responseHeaders(contentType, length, extraHeaders = {}) {
  return {
    ...SECURITY_HEADERS,
    'cache-control': 'no-store',
    'content-type': contentType,
    'content-length': length,
    ...extraHeaders,
  };
}

function sendBuffer(response, status, contentType, payload, extraHeaders = {}) {
  if (response.headersSent || response.destroyed) return;
  response.writeHead(status, responseHeaders(contentType, payload.length, extraHeaders));
  response.end(payload);
}

function sendJson(response, status, body, extraHeaders = {}) {
  const payload = Buffer.from(JSON.stringify(body));
  sendBuffer(response, status, 'application/json; charset=utf-8', payload, extraHeaders);
}

// A page on another site can point its own domain at 127.0.0.1 (DNS
// rebinding) and then read this one as same-origin; only loopback names pass.
const LOOPBACK_HOSTNAMES = new Set(['localhost', '127.0.0.1', '[::1]']);

function parseHttpAuthority(authority, expectedPort) {
  if (typeof authority !== 'string' || authority.length > 255 || /[@/\\\s]/.test(authority)) {
    return null;
  }
  let parsed;
  try {
    parsed = new URL(`http://${authority}`);
  } catch {
    return null;
  }
  if (!LOOPBACK_HOSTNAMES.has(parsed.hostname)) return null;
  if (parsed.username || parsed.password || parsed.pathname !== '/' || parsed.search || parsed.hash) return null;
  const effectivePort = parsed.port ? Number(parsed.port) : 80;
  if (effectivePort !== expectedPort) return null;
  return parsed.host.toLowerCase();
}

function requestIsSameOrigin(request, expectedPort) {
  const authority = parseHttpAuthority(request.headers.host, expectedPort);
  if (!authority) return false;
  const origin = request.headers.origin;
  if (origin === undefined) return true;
  if (typeof origin !== 'string' || origin.length > 512) return false;
  try {
    const parsed = new URL(origin);
    return parsed.protocol === 'http:'
      && !parsed.username
      && !parsed.password
      && parsed.pathname === '/'
      && !parsed.search
      && !parsed.hash
      && parsed.host.toLowerCase() === authority;
  } catch {
    return false;
  }
}

function parseRequestTarget(rawTarget) {
  if (typeof rawTarget !== 'string'
    || Buffer.byteLength(rawTarget, 'utf8') > MAX_REQUEST_TARGET_BYTES) {
    return { error: 'request_target_too_long', status: 414 };
  }
  if (!rawTarget.startsWith('/') || rawTarget.startsWith('//')
    || rawTarget.includes('\\') || rawTarget.includes('#')) {
    return { error: 'invalid_request_target', status: 400 };
  }
  try {
    return { url: new URL(rawTarget, 'http://localhost') };
  } catch {
    return { error: 'invalid_request_target', status: 400 };
  }
}

function loadStaticFiles(publicDirectory) {
  const result = new Map();
  for (const [route, asset] of Object.entries(STATIC_FILES)) {
    const absolutePath = path.join(publicDirectory, asset.file);
    result.set(route, {
      body: fs.readFileSync(absolutePath),
      contentType: asset.contentType,
    });
  }
  return result;
}

function listen(server, port, host) {
  return new Promise((resolve, reject) => {
    const onError = (error) => reject(error);
    server.once('error', onError);
    server.listen(port, host, () => {
      server.off('error', onError);
      resolve();
    });
  });
}

function close(server, timeoutMs) {
  return new Promise((resolve) => {
    if (!server?.listening) {
      resolve();
      return;
    }
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    const timer = setTimeout(() => {
      server.closeAllConnections?.();
      finish();
    }, timeoutMs);
    timer.unref();
    server.close(() => {
      clearTimeout(timer);
      finish();
    });
    server.closeIdleConnections?.();
  });
}

export class ActivityWebServer {
  constructor(config, logger = NULL_LOGGER) {
    this.config = config;
    this.logger = logger;
    this.instances = new Map(config.instances.map((instance) => [instance.id, instance]));
    this.httpServer = null;
    this.staticFiles = null;
    this.stopPromise = null;
  }

  async start() {
    if (this.httpServer?.listening) return;
    this.staticFiles = loadStaticFiles(this.config.publicDir);
    this.httpServer = http.createServer((request, response) => {
      void this.handleRequest(request, response).catch((error) => {
        this.logger.error('web_request_failed', {
          method: request.method,
          error: error instanceof Error ? error.message : String(error),
        });
        if (!response.headersSent) sendJson(response, 500, { error: 'internal_error' });
        else response.destroy();
      });
    });
    this.httpServer.keepAliveTimeout = 5_000;
    this.httpServer.headersTimeout = 6_000;
    this.httpServer.requestTimeout = 10_000;
    this.httpServer.on('clientError', (_error, socket) => {
      if (socket.writable) {
        socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
      }
    });
    await listen(this.httpServer, this.config.port, this.config.host);
    this.logger.info('web_ready', {
      address: `http://${this.config.host}:${this.address().port}`,
      instances: [...this.instances.keys()],
    });
  }

  address() {
    return this.httpServer?.address() ?? null;
  }

  async handleRequest(request, response) {
    const address = this.address();
    const expectedPort = typeof address === 'object' && address ? address.port : this.config.port;
    if (!requestIsSameOrigin(request, expectedPort)) {
      sendJson(response, 403, { error: 'forbidden' });
      return;
    }
    if (request.method !== 'GET') {
      sendJson(response, 405, { error: 'method_not_allowed' }, { allow: 'GET' });
      return;
    }

    const target = parseRequestTarget(request.url);
    if (target.error) {
      sendJson(response, target.status, { error: target.error });
      return;
    }
    const { url } = target;

    const staticAsset = this.staticFiles.get(url.pathname);
    if (staticAsset && this.isSafePageQuery(url)) {
      sendBuffer(response, 200, staticAsset.contentType, staticAsset.body);
      return;
    }

    if (url.pathname === '/api/instances' && url.search === '') {
      sendJson(response, 200, {
        defaultInstance: this.config.defaultInstance,
        instances: [...this.instances.values()].map(({ id, label }) => ({ id, label })),
      });
      return;
    }

    if (PROXY_PATHS.has(url.pathname)) {
      const selected = this.selectInstance(url);
      if (selected.error) {
        sendJson(response, selected.status, { error: selected.error });
        return;
      }
      if (url.pathname !== '/api/activity' && url.search !== '') {
        sendJson(response, 404, { error: 'not_found' });
        return;
      }
      await this.proxy(request, response, selected.instance, `${url.pathname}${url.search}`);
      return;
    }

    sendJson(response, 404, { error: 'not_found' });
  }

  isSafePageQuery(url) {
    if (url.search === '') return true;
    if (url.pathname !== '/' && url.pathname !== '/activity') return false;
    const keys = [...url.searchParams.keys()];
    if (keys.some((key) => !PAGE_QUERY_KEYS.has(key))) return false;
    const instances = url.searchParams.getAll(INSTANCE_QUERY_KEY);
    if (instances.length > 1) return false;
    if (instances.length === 1 && !this.instances.has(instances[0])) return false;
    const epochs = url.searchParams.getAll(EPOCH_QUERY_KEY);
    if (epochs.length > 1) return false;
    if (epochs.length === 1 && !EPOCH_QUERY_VALUE.test(epochs[0])) return false;
    return instances.length === 1 || epochs.length === 1;
  }

  selectInstance(url) {
    const values = url.searchParams.getAll(INSTANCE_QUERY_KEY);
    if (values.length > 1) return { error: 'invalid_instance', status: 400 };
    const id = values[0] || this.config.defaultInstance;
    const instance = this.instances.get(id);
    if (!instance) return { error: 'unknown_instance', status: 404 };
    url.searchParams.delete(INSTANCE_QUERY_KEY);
    return { instance };
  }

  proxy(clientRequest, clientResponse, instance, upstreamPath) {
    return new Promise((resolve) => {
      let completed = false;
      let timedOut = false;
      const finish = () => {
        if (completed) return false;
        completed = true;
        resolve();
        return true;
      };
      const fail = (status, error) => {
        if (!finish()) return;
        sendJson(clientResponse, status, { error });
      };

      const upstream = http.request({
        hostname: instance.host,
        port: instance.port,
        method: 'GET',
        path: upstreamPath,
        headers: {
          accept: 'application/json',
          connection: 'close',
        },
        agent: false,
      }, (upstreamResponse) => {
        const chunks = [];
        let bytes = 0;
        upstreamResponse.on('data', (chunk) => {
          bytes += chunk.length;
          if (bytes > MAX_UPSTREAM_RESPONSE_BYTES) {
            upstreamResponse.destroy(new Error('upstream response too large'));
            return;
          }
          chunks.push(chunk);
        });
        upstreamResponse.on('end', () => {
          if (!finish()) return;
          const status = Number.isInteger(upstreamResponse.statusCode)
            && upstreamResponse.statusCode >= 100
            && upstreamResponse.statusCode <= 599
            ? upstreamResponse.statusCode
            : 502;
          sendBuffer(
            clientResponse,
            status,
            'application/json; charset=utf-8',
            Buffer.concat(chunks),
          );
        });
        upstreamResponse.on('aborted', () => fail(502, 'daemon_unavailable'));
        upstreamResponse.on('error', () => fail(502, 'daemon_unavailable'));
      });

      upstream.setTimeout(this.config.upstreamTimeoutMs, () => {
        timedOut = true;
        upstream.destroy(new Error('daemon timeout'));
      });
      upstream.on('error', () => {
        fail(timedOut ? 504 : 502, timedOut ? 'daemon_timeout' : 'daemon_unavailable');
      });
      clientRequest.on('aborted', () => upstream.destroy());
      clientResponse.on('close', () => {
        if (!clientResponse.writableEnded) upstream.destroy();
      });
      upstream.end();
    });
  }

  async stop() {
    if (this.stopPromise) return this.stopPromise;
    this.stopPromise = close(this.httpServer, this.config.shutdownTimeoutMs)
      .finally(() => {
        this.logger.info('web_stopped');
        this.httpServer = null;
      });
    return this.stopPromise;
  }
}
