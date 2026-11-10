// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// The DSH side of the keyless runtime probe: initialize, the `vika/hello`
// bridge handshake, `vika/llm/models`, and `shutdown` -- no session, no turn.
// The ACP/app-server probes live in runtime-probe.test.mjs; this file only
// carries the DSH cases so it cannot collide with that upstream file.

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from '../../daemon/test/dsh-node-gate.mjs';
import { dshCatalogueRoute, probeDsh } from '../src/runtime-probe.mjs';

function fixture(t, script) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-app-server-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const bin = path.join(dir, 'fake-cli');
  fs.writeFileSync(bin, `#!${process.execPath}\n${script}`, { mode: 0o755 });
  const children = [];
  const closed = [];
  const spawnImpl = (...args) => {
    const child = spawn(...args);
    children.push(child);
    closed.push(once(child, 'close'));
    return child;
  };
  return { bin, dir, children, closed, options: { env: { PATH: dir, HOME: dir }, spawnImpl, timeoutMs: 1000 } };
}

test('DSH probe completes initialize, hello and shutdown without a model turn', async t => {
  const f = fixture(t, `
    const fs = require('node:fs');
    const readline = require('node:readline');
    if (process.argv[2] !== '--profile' || process.argv[3] !== 'sdk' || process.argv[4] !== '--patch') process.exit(2);
    const patch = JSON.parse(fs.readFileSync(process.argv[5], 'utf8'));
    fs.writeFileSync(process.env.HOME + '/patch.json', JSON.stringify(patch));
    readline.createInterface({ input: process.stdin }).on('line', line => {
      const request = JSON.parse(line);
      fs.appendFileSync(process.env.HOME + '/requests', request.method + '\\n');
      if (request.method === 'vika/hello') process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: { bridge: 'vika-dsh-bridge', version: 2, methods: ['session/steer', 'session/cancel', 'vika/llm/models', 'vika/llm/model/info', 'vika/session/model/select', 'vika/settings/describe', 'vika/settings/mutate', 'vika/credentials/set', 'vika/credentials/unset'] } }) + '\\n');
      else process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: {} }) + '\\n');
    });
  `);
  const result = await probeDsh(f.bin, f.options);
  assert.equal(result.compatible, true);
  await Promise.all(f.closed);
  // The catalogue is asked before shutdown: it is what decides the route a
  // probe reports, and it must not need a second DSH process.
  assert.deepEqual(fs.readFileSync(path.join(f.dir, 'requests'), 'utf8').trim().split('\n'),
    ['initialize', 'vika/hello', 'vika/llm/models', 'shutdown']);
  const patch = JSON.parse(fs.readFileSync(path.join(f.dir, 'patch.json'), 'utf8'));
  assert.equal(patch.some(row => row.id === 'vika-personal-agent'), false);
  assert.equal(patch.some(row => row.insert?.[0]?.config?.args?.includes('--authority')), false);
  // Routes belong to DSH's settings document; the probe's patch must not
  // override it with a provider section.
  assert.equal(patch.some(row => row.id === 'llm-pi-ai'), false);
});

test('DSH probe rejects a missing or incorrect bridge', async t => {
  const f = fixture(t, `
    require('node:readline').createInterface({ input: process.stdin }).on('line', line => {
      const request = JSON.parse(line);
      process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, ...(request.method === 'vika/hello' ? { error: { code: -32601, message: 'method not found' } } : { result: {} }) }) + '\\n');
    });
  `);
  const result = await probeDsh(f.bin, f.options);
  assert.equal(result.compatible, false);
  assert.match(result.reason, /bridge handshake failed/);
  await Promise.all(f.closed);
});

// A fake DSH that answers the bridge handshake and a caller-supplied catalogue.
function dshCatalogueFixture(t, { catalogue, hello = true }) {
  return fixture(t, `
    const fs = require('node:fs');
    // The generated patch is what the daemon would start DSH from; keep it for
    // the assertions about what this probe does (and does not) override.
    fs.writeFileSync(process.env.HOME + '/patch.json', fs.readFileSync(process.argv[5], 'utf8'));
    require('node:readline').createInterface({ input: process.stdin }).on('line', line => {
      const request = JSON.parse(line);
      fs.appendFileSync(process.env.HOME + '/requests', JSON.stringify({ method: request.method, params: request.params }) + '\\n');
      fs.appendFileSync(process.env.HOME + '/env', JSON.stringify({
        DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY || null,
        PI_KEYS: Object.keys(process.env).filter(key => key.startsWith('PI_KEY_')),
      }) + '\\n');
      if (request.method === 'vika/hello') {
        const result = ${hello ? "{ bridge: 'vika-dsh-bridge', version: 2, methods: ['session/steer', 'session/cancel', 'vika/llm/models', 'vika/llm/model/info', 'vika/session/model/select', 'vika/settings/describe', 'vika/settings/mutate', 'vika/credentials/set', 'vika/credentials/unset'] }" : '{ error: { code: -32601, message: "method not found" } }'};
        process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, ...(result.error ? { error: result.error } : { result }) }) + '\\n');
        return;
      }
      const result = request.method === 'vika/llm/models' ? ${JSON.stringify(catalogue)} : {};
      process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result }) + '\\n');
    });
  `);
}

test('DSH probe reports the route the bridge catalogue names', async t => {
  const f = dshCatalogueFixture(t, {
    catalogue: {
      providers: [
        // A route whose key is missing serves no model, so it cannot be probed.
        { provider: 'idle', name: 'Idle', models: [], error: { code: 'NO_KEY', message: 'no credential' }, credential: null },
        {
          provider: 'deepseek', name: 'DeepSeek', credential: { ref: 'PI_KEY_DEEPSEEK', present: true },
          // The model id may itself contain a slash: provider and model stay
          // separate fields all the way to initialize.
          models: [{ id: 'deepseek-chat', name: 'DeepSeek Chat', inputModalities: ['text'] }],
          error: null,
        },
      ],
    },
  });
  const result = await probeDsh(f.bin, f.options);
  assert.equal(result.compatible, true);
  assert.equal(result.route, 'deepseek/deepseek-chat');
  assert.equal(result.fallbackRoute, '');
  assert.equal(result.note, '');
  await Promise.all(f.closed);
  const requests = fs.readFileSync(path.join(f.dir, 'requests'), 'utf8').trim().split('\n').map(JSON.parse);
  assert.deepEqual(requests.map(request => request.method), ['initialize', 'vika/hello', 'vika/llm/models', 'shutdown']);
  // The probe is keyless: it initializes the built-in route, and no placeholder
  // key is invented (that would make an unconfigured route look servable).
  assert.equal(requests[0].params.provider, 'deepseek-official');
  assert.equal(requests[0].params.model, 'deepseek-flash');
  const env = JSON.parse(fs.readFileSync(path.join(f.dir, 'env'), 'utf8').trim().split('\n')[0]);
  assert.equal(env.DEEPSEEK_API_KEY, null);
  assert.doesNotMatch(result.route, /^idle\//);
});

test('DSH probe passes the caller key through and never invents one', async t => {
  const f = dshCatalogueFixture(t, { catalogue: { providers: [{ provider: 'deepseek-official', models: [{ id: 'deepseek-flash' }] }] } });
  const result = await probeDsh(f.bin, { ...f.options, env: { ...f.options.env, DEEPSEEK_API_KEY: 'operator-key' } });
  assert.equal(result.compatible, true);
  assert.equal(result.route, 'deepseek-official/deepseek-flash');
  await Promise.all(f.closed);
  const env = JSON.parse(fs.readFileSync(path.join(f.dir, 'env'), 'utf8').trim().split('\n')[0]);
  assert.equal(env.DEEPSEEK_API_KEY, 'operator-key');
});

test('DSH probe falls back to the initialize route when no route serves a model', async t => {
  const f = dshCatalogueFixture(t, {
    catalogue: {
      providers: [
        { provider: 'deepseek-official', name: 'DeepSeek', models: [], error: { code: 'AUTH', message: 'invalid api key' }, credential: null },
      ],
    },
  });
  const result = await probeDsh(f.bin, f.options);
  // A catalogue with no servable route is information, not a failed probe.
  assert.equal(result.compatible, true);
  assert.equal(result.route, '', 'no route is claimed');
  assert.equal(result.fallbackRoute, 'deepseek-official/deepseek-flash', 'the initialize route is still reported');
  assert.match(result.note, /deepseek-official: 0 models \(invalid api key\)/);
  await Promise.all(f.closed);
  const initialize = JSON.parse(fs.readFileSync(path.join(f.dir, 'requests'), 'utf8').trim().split('\n')[0]);
  assert.equal(initialize.params.provider, 'deepseek-official');
  assert.equal(initialize.params.model, 'deepseek-flash');
});

test('DSH probe never reads a models.json or injects PI_KEY_ credentials', async t => {
  // The channel is gone: a models.json in the operator's home must reach neither
  // the patch nor the child environment, whatever it declares.
  const f = dshCatalogueFixture(t, { catalogue: { providers: [] } });
  fs.writeFileSync(path.join(f.dir, 'models.json'), JSON.stringify({
    providers: { deepseek: { baseUrl: 'http://127.0.0.1:9/v1', api: 'openai-completions', apiKey: 'fixture-key-not-real', models: [{ id: 'deepseek-chat' }] } },
  }));
  const patchPath = path.join(f.dir, 'patch.json');
  const result = await probeDsh(f.bin, f.options);
  assert.equal(result.compatible, true);
  assert.equal(result.route, '');
  await Promise.all(f.closed);
  const patch = JSON.parse(fs.readFileSync(patchPath, 'utf8'));
  assert.equal(JSON.stringify(patch).includes('llm-pi-ai'), false);
  assert.equal(JSON.stringify(patch).includes('fixture-key-not-real'), false);
  for (const line of fs.readFileSync(path.join(f.dir, 'env'), 'utf8').trim().split('\n')) {
    assert.deepEqual(JSON.parse(line).PI_KEYS, [], 'no route credential is injected any more');
  }
});

test('dshCatalogueRoute picks a route that serves a model and never splits the id', () => {
  assert.equal(dshCatalogueRoute(null), null);
  assert.equal(dshCatalogueRoute({}), null);
  assert.equal(dshCatalogueRoute({ providers: 'deepseek' }), null);
  assert.equal(dshCatalogueRoute({ providers: [] }), null);
  // A route with no model cannot run a turn, even when it has a credential.
  assert.equal(dshCatalogueRoute({ providers: [{ provider: 'a', models: [], credential: { present: true } }] }), null);
  assert.deepEqual(
    dshCatalogueRoute({ providers: [{ provider: 'a', models: [{ id: 'deepseek-chat' }], credential: { present: false } }] }),
    { provider: 'a', model: 'deepseek-chat', id: 'a/deepseek-chat' },
  );
  // A credential-ready route wins over one DSH only lists.
  assert.equal(dshCatalogueRoute({
    providers: [
      { provider: 'listed', models: [{ id: 'one' }], credential: { present: false } },
      { provider: 'ready', models: [{ id: 'two' }], credential: { present: true } },
    ],
  }).id, 'ready/two');
});

// B9a: a read-only setup probe must not require the credential write methods.
// A bridge that answers the catalogue but exposes no `vika/credentials/*` is
// still a working install; the probe only has to see what the runtime it drives
// will call, plus the one settings read the models page starts from.
test('DSH probe accepts a bridge without the credential write methods', async t => {
  const f = fixture(t, `
    const readline = require('node:readline');
    readline.createInterface({ input: process.stdin }).on('line', line => {
      const request = JSON.parse(line);
      if (request.method === 'vika/hello') {
        process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: {
          bridge: 'vika-dsh-bridge', version: 2,
          methods: ['session/steer', 'session/cancel', 'vika/llm/models', 'vika/llm/model/info',
            'vika/session/model/select', 'vika/settings/describe'],
        } }) + '\\n');
        return;
      }
      process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: {} }) + '\\n');
    });
  `);
  const result = await probeDsh(f.bin, f.options);
  assert.equal(result.compatible, true, result.reason);
  await Promise.all(f.closed);
});

test('DSH probe rejects a bridge that lacks a method the daemon itself calls', async t => {
  // The daemon method set is the real requirement: without model selection this
  // runtime cannot honour a /model switch at all.
  const f = fixture(t, `
    const readline = require('node:readline');
    readline.createInterface({ input: process.stdin }).on('line', line => {
      const request = JSON.parse(line);
      if (request.method === 'vika/hello') {
        process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: {
          bridge: 'vika-dsh-bridge', version: 2,
          methods: ['session/steer', 'session/cancel', 'vika/llm/models', 'vika/llm/model/info'],
        } }) + '\\n');
        return;
      }
      process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: {} }) + '\\n');
    });
  `);
  const result = await probeDsh(f.bin, f.options);
  assert.equal(result.compatible, false);
  assert.match(result.reason, /bridge handshake failed/);
  await Promise.all(f.closed);
});
