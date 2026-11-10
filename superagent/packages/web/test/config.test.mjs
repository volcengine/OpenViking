// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { getConfig } from '../src/config.mjs';

const KEYS = [
  'VIKA_WEB_HOST',
  'VIKA_WEB_PORT',
  'VIKA_WEB_DAEMON_HOST',
  'VIKA_WEB_DAEMON_PORT',
  'VIKA_WEB_INSTANCES',
  'VIKA_WEB_DEFAULT_INSTANCE',
];
let previous;

beforeEach(() => {
  previous = Object.fromEntries(KEYS.map((key) => [key, process.env[key]]));
  for (const key of KEYS) delete process.env[key];
});

afterEach(() => {
  for (const key of KEYS) {
    if (previous[key] === undefined) delete process.env[key];
    else process.env[key] = previous[key];
  }
});

test('web config accepts a fixed loopback allowlist of daemon instances', () => {
  process.env.VIKA_WEB_HOST = '::1';
  process.env.VIKA_WEB_INSTANCES = JSON.stringify([
    { id: 'alpha', label: 'Alpha', host: '127.0.0.1', port: 3738 },
    { id: 'beta', label: '贝塔', host: 'localhost', port: 3740 },
  ]);
  process.env.VIKA_WEB_DEFAULT_INSTANCE = 'beta';
  const config = getConfig();
  assert.deepEqual(config.instances.map(({ id, label, port }) => ({ id, label, port })), [
    { id: 'alpha', label: 'Alpha', port: 3738 },
    { id: 'beta', label: '贝塔', port: 3740 },
  ]);
  assert.equal(config.defaultInstance, 'beta');
  assert.equal(config.host, '::1');
  assert.ok(Object.isFrozen(config.instances));
});

test('web routes are not capped to a fixed agent count', () => {
  process.env.VIKA_WEB_INSTANCES = JSON.stringify(Array.from({ length: 32 }, (_, index) => ({
    id: `agent-${index}`,
    label: `Agent ${index}`,
    host: '127.0.0.1',
    port: 4000 + index,
  })));
  process.env.VIKA_WEB_DEFAULT_INSTANCE = 'agent-17';
  const config = getConfig();
  assert.equal(config.instances.length, 32);
  assert.equal(config.defaultInstance, 'agent-17');
});

test('web config rejects arbitrary upstreams, duplicate ids, and an unknown default', () => {
  process.env.VIKA_WEB_INSTANCES = JSON.stringify([
    { id: 'alpha', label: 'Alpha', host: 'example.test', port: 3738 },
  ]);
  assert.throws(() => getConfig(), /loopback/);

  process.env.VIKA_WEB_INSTANCES = JSON.stringify([
    { id: 'alpha', label: 'Alpha', host: '127.0.0.1', port: 3738 },
    { id: 'alpha', label: 'Other', host: '127.0.0.1', port: 3740 },
  ]);
  assert.throws(() => getConfig(), /duplicate.*id/);

  process.env.VIKA_WEB_INSTANCES = JSON.stringify([
    { id: 'alpha', label: 'Alpha', host: '127.0.0.1', port: 3738 },
  ]);
  process.env.VIKA_WEB_DEFAULT_INSTANCE = 'beta';
  assert.throws(() => getConfig(), /must name an entry/);
});

test('web config binds only to loopback: the page has no login', () => {
  for (const host of ['example.test', '0.0.0.0', '::', '10.0.0.8']) {
    process.env.VIKA_WEB_HOST = host;
    assert.throws(() => getConfig(), /VIKA_WEB_HOST must be a loopback address/, host);
  }
  process.env.VIKA_WEB_HOST = 'localhost';
  assert.equal(getConfig().host, 'localhost');
});
