// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, it } from 'node:test';
import { ActivityWebServer } from '../src/server.mjs';

const moduleDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const publicDir = path.join(moduleDir, 'public');

function request(port, pathname, { method = 'GET', headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const call = http.request({
      hostname: '127.0.0.1',
      port,
      path: pathname,
      method,
      headers,
    }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => resolve({
        status: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    call.on('error', reject);
    call.end();
  });
}

function startHttpServer(handler, port = 0) {
  const server = http.createServer(handler);
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}

function closeHttpServer(server) {
  return new Promise((resolve) => {
    if (!server?.listening) {
      resolve();
      return;
    }
    server.close(resolve);
    server.closeAllConnections?.();
  });
}

function config(daemonPort, overrides = {}) {
  return {
    host: '127.0.0.1',
    port: 0,
    instances: [{ id: 'alpha', label: 'Alpha', host: '127.0.0.1', port: daemonPort }],
    defaultInstance: 'alpha',
    publicDir,
    upstreamTimeoutMs: 250,
    shutdownTimeoutMs: 1_000,
    ...overrides,
  };
}

describe('independent activity web server', { concurrency: false }, () => {
  let daemon;
  let secondDaemon;
  let web;

  afterEach(async () => {
    await web?.stop();
    await closeHttpServer(daemon);
    await closeHttpServer(secondDaemon);
    web = null;
    daemon = null;
    secondDaemon = null;
  });

  it('serves only fixed static files even when the daemon is unavailable', async () => {
    const reservation = await startHttpServer((_request, response) => response.end());
    const unavailablePort = reservation.address().port;
    await closeHttpServer(reservation);

    web = new ActivityWebServer(config(unavailablePort));
    await web.start();
    const port = web.address().port;

    const page = await request(port, '/');
    assert.equal(page.status, 200);
    assert.match(page.headers['content-type'], /^text\/html/);
    assert.match(page.body, /instance-select/);
    assert.equal((await request(port, '/activity')).status, 200);
    assert.equal((await request(port, '/?instance=alpha')).status, 200);
    assert.equal((await request(port, '/?instance=alpha&epoch=2')).status, 200);
    assert.equal((await request(port, '/?epoch=current')).status, 200);
    assert.equal((await request(port, '/?epoch=all')).status, 200);
    assert.equal((await request(port, '/?instance=missing')).status, 404);
    assert.equal((await request(port, '/?epoch=nope')).status, 404);
    assert.match((await request(port, '/activity.css')).headers['content-type'], /^text\/css/);
    assert.match((await request(port, '/activity.js')).headers['content-type'], /^text\/javascript/);
    assert.equal((await request(port, '/package.json')).status, 404);
    assert.equal((await request(port, '/activity.css?cache=1')).status, 404);

    const instances = await request(port, '/api/instances');
    assert.equal(instances.status, 200);
    assert.deepEqual(JSON.parse(instances.body), {
      defaultInstance: 'alpha',
      instances: [{ id: 'alpha', label: 'Alpha' }],
    });

    const api = await request(port, '/api/activity');
    assert.equal(api.status, 502);
    assert.deepEqual(JSON.parse(api.body), { error: 'daemon_unavailable' });
    assert.equal((await request(port, '/')).status, 200);
  });

  it('proxies only the fixed daemon routes without forwarding credentials or response cookies', async () => {
    const seen = [];
    daemon = await startHttpServer((incoming, response) => {
      seen.push({ url: incoming.url, method: incoming.method, headers: incoming.headers });
      response.statusCode = incoming.url === '/ready' ? 503 : 200;
      response.setHeader('content-type', 'application/json');
      response.setHeader('set-cookie', 'private=secret');
      response.setHeader('server', 'private-daemon');
      response.end(JSON.stringify({ path: incoming.url }));
    });
    web = new ActivityWebServer(config(daemon.address().port));
    await web.start();
    const port = web.address().port;

    const api = await request(port, '/api/activity?limit=25&before=100', {
      headers: {
        authorization: 'Bearer private',
        cookie: 'session=private',
        origin: `http://127.0.0.1:${port}`,
        'x-forwarded-for': '203.0.113.10',
      },
    });
    assert.equal(api.status, 200);
    assert.deepEqual(JSON.parse(api.body), { path: '/api/activity?limit=25&before=100' });
    assert.equal(api.headers['set-cookie'], undefined);
    assert.equal(api.headers.server, undefined);
    assert.equal(api.headers['cache-control'], 'no-store');
    assert.match(api.headers['content-security-policy'], /default-src 'self'/);
    assert.equal(api.headers['x-content-type-options'], 'nosniff');

    assert.equal(seen.length, 1);
    assert.equal(seen[0].method, 'GET');
    assert.equal(seen[0].headers.authorization, undefined);
    assert.equal(seen[0].headers.cookie, undefined);
    assert.equal(seen[0].headers.origin, undefined);
    assert.equal(seen[0].headers['x-forwarded-for'], undefined);
    assert.equal(seen[0].headers.accept, 'application/json');

    const ready = await request(port, '/ready');
    assert.equal(ready.status, 503);
    assert.equal((await request(port, '/health')).status, 200);
    assert.equal((await request(port, '/ready?probe=1')).status, 404);
    assert.equal((await request(port, '/api/private')).status, 404);
  });

  it('routes only allowlisted instance ids and never accepts a caller-supplied upstream', async () => {
    daemon = await startHttpServer((incoming, response) => {
      response.setHeader('content-type', 'application/json');
      response.end(JSON.stringify({ instance: 'alpha', path: incoming.url }));
    });
    secondDaemon = await startHttpServer((incoming, response) => {
      response.setHeader('content-type', 'application/json');
      response.end(JSON.stringify({ instance: 'beta', path: incoming.url }));
    });
    web = new ActivityWebServer(config(daemon.address().port, {
      instances: [
        { id: 'alpha', label: 'Alpha', host: '127.0.0.1', port: daemon.address().port },
        { id: 'beta', label: '贝塔', host: '127.0.0.1', port: secondDaemon.address().port },
      ],
    }));
    await web.start();
    const port = web.address().port;

    assert.deepEqual(
      JSON.parse((await request(port, '/api/activity?instance=beta&limit=7')).body),
      { instance: 'beta', path: '/api/activity?limit=7' },
    );
    assert.deepEqual(
      JSON.parse((await request(port, '/api/activity?instance=beta&epoch=current&limit=7')).body),
      { instance: 'beta', path: '/api/activity?epoch=current&limit=7' },
    );
    assert.deepEqual(
      JSON.parse((await request(port, '/api/activity?limit=7')).body),
      { instance: 'alpha', path: '/api/activity?limit=7' },
    );
    assert.deepEqual(
      JSON.parse((await request(port, '/api/instances')).body),
      {
        defaultInstance: 'alpha',
        instances: [{ id: 'alpha', label: 'Alpha' }, { id: 'beta', label: '贝塔' }],
      },
    );
    assert.equal((await request(port, '/api/activity?instance=missing')).status, 404);
    assert.equal((await request(port, '/api/activity?instance=alpha&instance=beta')).status, 400);
    assert.equal((await request(port, '/api/activity?instance=http://127.0.0.1:1')).status, 404);
  });

  it('rejects unsafe hosts, origins, methods, and request targets before proxying', async () => {
    let upstreamCalls = 0;
    daemon = await startHttpServer((_request, response) => {
      upstreamCalls += 1;
      response.end('{}');
    });
    web = new ActivityWebServer(config(daemon.address().port));
    await web.start();
    const port = web.address().port;

    for (const host of [`localhost:${port}`, `127.0.0.1:${port}`, `[::1]:${port}`, `LOCALHOST:${port}`]) {
      assert.equal((await request(port, '/', { headers: { host } })).status, 200, host);
    }
    // DNS rebinding: a foreign name is refused even when Origin matches it.
    for (const host of [`attacker.example:${port}`, `10.0.0.8:${port}`, `localhost.attacker.example:${port}`]) {
      assert.equal((await request(port, '/api/activity', { headers: { host, origin: `http://${host}` } })).status, 403, host);
    }
    assert.equal((await request(port, '/', { headers: { host: 'example.test' } })).status, 403);
    assert.equal((await request(port, '/', {
      headers: { origin: `http://localhost:${port}` },
    })).status, 403);
    assert.equal((await request(port, '/', { method: 'POST' })).status, 405);
    assert.equal((await request(port, '/', { method: 'POST' })).headers.allow, 'GET');
    assert.equal((await request(port, '//evil.test/api/activity')).status, 400);
    assert.equal((await request(port, 'http://evil.test/api/activity')).status, 400);
    assert.equal((await request(port, `/api/activity?value=${'x'.repeat(2_100)}`)).status, 414);
    assert.equal((await request(port, '/%2e%2e/package.json')).status, 404);
    assert.equal(upstreamCalls, 0);
  });

  it('returns a generic timeout and recovers from daemon downtime without a web restart', async () => {
    daemon = await startHttpServer((_request, _response) => {});
    const daemonPort = daemon.address().port;
    web = new ActivityWebServer(config(daemonPort, { upstreamTimeoutMs: 75 }));
    await web.start();
    const webPort = web.address().port;

    const timedOut = await request(webPort, '/api/activity');
    assert.equal(timedOut.status, 504);
    assert.deepEqual(JSON.parse(timedOut.body), { error: 'daemon_timeout' });

    await closeHttpServer(daemon);
    daemon = null;
    const unavailable = await request(webPort, '/api/activity');
    assert.equal(unavailable.status, 502);
    assert.deepEqual(JSON.parse(unavailable.body), { error: 'daemon_unavailable' });
    assert.equal((await request(webPort, '/')).status, 200);

    daemon = await startHttpServer((_request, response) => {
      response.setHeader('content-type', 'application/json');
      response.end('{"recovered":true}');
    }, daemonPort);
    const recovered = await request(webPort, '/api/activity');
    assert.equal(recovered.status, 200);
    assert.deepEqual(JSON.parse(recovered.body), { recovered: true });
  });
});
