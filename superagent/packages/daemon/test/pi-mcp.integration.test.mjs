// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { startActionApi, stopActionApi } from '../src/actions/api.mjs';
import { PiAgent } from '../src/pi.mjs';
import { managedPiBin } from '../src/pi-runtime.mjs';
import { localSocketPath } from '../src/platform/index.mjs';
import { Store } from '../src/store.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { configurePiIsolation } from './pi-isolation-fixture.mjs';
import { fakeOV } from './ov-server-fixture.mjs';
import { chatEvent } from './event-fixture.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const modulePath = (relative) => fileURLToPath(new URL(relative, import.meta.url));
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6XcAAAAASUVORK5CYII=', 'base64');

function completion(response, index, tool = null) {
  response.writeHead(200, { 'content-type': 'text/event-stream' });
  const delta = tool ? { role: 'assistant', tool_calls: [{
    index: 0, id: `call_${index}`, type: 'function',
    function: { name: tool.fullName ?? `mcp__personal_agent__${tool.name}`, arguments: JSON.stringify(tool.input) },
  }] } : { role: 'assistant', content: 'done' };
  for (const choice of [
    { index: 0, delta, finish_reason: null },
    { index: 0, delta: {}, finish_reason: tool ? 'tool_calls' : 'stop' },
  ]) response.write(`data: ${JSON.stringify({ id: `response_${index}`, object: 'chat.completion.chunk', choices: [choice] })}\n\n`);
  response.end('data: [DONE]\n\n');
}

async function fixture(t, { conflict = false, dialogs = false, openviking = false, isolation = false } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-mcp-'));
  const workspace = path.join(root, 'workspace');
  const agentDir = path.join(root, 'pi-config');
  fs.mkdirSync(path.join(workspace, '.pi'), { recursive: true });
  fs.mkdirSync(agentDir);
  if (dialogs) {
    // User extensions are still loaded. Exercise Pi's real dialog promises.
    fs.mkdirSync(path.join(agentDir, 'extensions'));
    fs.writeFileSync(path.join(agentDir, 'extensions', 'dialogs.js'), `
      import fs from 'node:fs';
      export default function(pi) {
        pi.on('${dialogs === 'startup' ? 'session_start' : 'before_agent_start'}', async (_event, ctx) => {
          const results = [
            await ctx.ui.confirm('Permission', 'Allow?'),
            await ctx.ui.select('Choose', ['yes', 'no']),
            await ctx.ui.input('Input'),
            await ctx.ui.editor('Edit'),
          ];
          fs.writeFileSync(process.env.PI_CODING_AGENT_DIR + '/dialog-results.json', JSON.stringify(results));
        });
      }
    `);
  }
  if (openviking) {
    // OpenViking's own Pi extension, installed by the owner: never loaded.
    fs.mkdirSync(path.join(agentDir, 'extensions', 'openviking'), { recursive: true });
    fs.writeFileSync(path.join(agentDir, 'extensions', 'openviking', 'index.js'), `
      import fs from 'node:fs';
      export default function(pi) {
        fs.writeFileSync(process.env.PI_CODING_AGENT_DIR + '/openviking-loaded', 'yes');
      }
    `);
  }
  // pm2 puts /usr/bin ahead of the user's Node; a stale node first on PATH
  // must not be the one that runs Pi.
  const staleNodeDir = path.join(root, 'stale-node');
  fs.mkdirSync(staleNodeDir);
  fs.writeFileSync(path.join(staleNodeDir, 'node'), '#!/bin/sh\necho "stale node on PATH" >&2\nexit 97\n', { mode: 0o755 });
  const mcpContents = JSON.stringify({ mcpServers: conflict
    ? { personal_agent: { command: 'unused', enabled: false } } : {} });
  for (const file of [path.join(agentDir, 'mcp.json'), path.join(workspace, '.pi', 'mcp.json')]) {
    fs.writeFileSync(file, mcpContents);
  }
  const isolated = isolation ? configurePiIsolation(root, agentDir, workspace) : null;
  const ov = isolation ? await fakeOV(t) : null;
  const ovConfig = path.join(root, 'ov.json');
  if (ov) {
    fs.writeFileSync(ovConfig, JSON.stringify({ url: ov.url, user: 'fixture' }));
    ov.state.searchResult = { memories: [{ uri: 'viking://user/fixture/memories/events/fixture.md', score: 0.9, abstract: 'Core memory fixture' }], resources: [], skills: [], total: 1 };
  }
  const store = new Store(path.join(root, 'state.sqlite3'), logger);
  store.initializeCognition('pi', 'pi-test-authority');
  const imagePath = path.join(root, 'image.png');
  fs.writeFileSync(imagePath, PNG);
  const connectors = fakeConnectors({ run: (_id, action) => action === 'message.media'
    ? { media: [{ path: imagePath, name: 'image.png', kind: 'image', mime_type: 'image/png', size: PNG.length }] }
    : { message_id: 'om_delivered123456' } });
  const logs = [];
  const service = new ActionService({
    config: { workspace, tempDir: path.join(root, 'tmp'), maxReplyChars: 1_000, ...(ov ? { openVikingConfigPath: ovConfig } : {}) },
    store, connectors,
    logger: { ...logger,
      info: (message, fields) => logs.push({ message, ...fields }),
      warn: (message, fields) => logs.push({ message, ...fields }),
    },
  });
  const socketPath = localSocketPath(root);
  let api;
  let agent;
  const requests = [];
  const incoming = store.ingest(chatEvent({ messageId: 'om_first12345678', chatId: 'oc_chat12345678' })).inbound;
  const model = http.createServer(async (request, response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    requests.push(JSON.parse(body));
    const index = requests.length;
    if (isolation) {
      const steps = [
        { name: 'openviking_find', input: { query: 'fixture memory' } },
        ...['configured_tool', 'packaged_tool', 'project_tool', 'project_configured_tool', 'project_packaged_tool',
          'mcp__global_tools__ping', 'mcp__project_tools__ping'].map((fullName) => ({ fullName, input: {} })),
        { fullName: 'codemode', input: { code: 'text(await tools.mcp__indirect__ping({}));' } },
        { fullName: 'tool_search', input: { query: 'mcp__deferred__ping' } },
        { fullName: 'mcp__deferred__ping', input: {} },
      ];
      completion(response, index, steps[index - 1] ?? null);
    } else if (index === 1) {
      completion(response, index, { name: 'get_message_media', input: { message_id: 'om_first12345678', key: 'img_test12345678' } });
    } else if (index === 2) {
      store.ingest(chatEvent({ messageId: 'om_newer12345678', chatId: 'oc_chat12345678' }));
      completion(response, index, { name: 'reply_message', input: {
        message_id: 'om_first12345678', decision_id: 'message:om_first12345678', markdown: 'stale answer',
      } });
    } else if (index === 3) {
      completion(response, index, { name: 'reply_message', input: {
        message_id: 'om_newer12345678', decision_id: 'message:om_newer12345678', markdown: 'updated answer',
      } });
    } else completion(response, index);
  });
  t.after(async () => {
    await agent?.stop();
    if (api) await stopActionApi(api);
    model.closeAllConnections();
    await new Promise((resolve) => model.close(resolve));
    store.close();
    // Windows may briefly retain the managed child's temp files after exit.
    fs.rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });
  await new Promise((resolve) => model.listen(0, '127.0.0.1', resolve));
  fs.writeFileSync(path.join(agentDir, 'models.json'), JSON.stringify({ providers: { fixture: {
    baseUrl: `http://127.0.0.1:${model.address().port}/v1`, api: 'openai-completions', apiKey: 'fixture-key',
    models: [{ id: 'fixture', input: ['text', 'image'], reasoning: false, contextWindow: 128000, maxTokens: 4096 }],
  } } }));
  api = await startActionApi({ socketPath, service, logger });
  const config = {
    piBin: managedPiBin(), piModel: 'fixture/fixture', piEffort: 'off', workspace,
    dataDir: path.join(root, 'data'), connectors: ['lark'], profile: 'fixture',
    actionSocketPath: socketPath, actionMcpPath: modulePath('../src/actions/mcp.mjs'),
    sendTimeoutMs: 1000,
  };
  const failures = [];
  const options = {
    onTurnFailed: (error) => failures.push(error),
    spawn: (bin, args, opts) => spawn(bin, args, { ...opts, env: {
      PATH: `${staleNodeDir}${path.delimiter}${process.env.PATH}`, HOME: root, ...Object.fromEntries(Object.entries(opts.env).filter(([key]) => key.startsWith('PERSONAL_AGENT_'))),
      PI_CODING_AGENT_DIR: agentDir, PI_OFFLINE: '1', PI_TELEMETRY: '0',
    } }),
  };
  // The daemon and its Pi share PI_CODING_AGENT_DIR, as in production: the
  // launcher reads the same agent directory to load the owner's extensions.
  const previous = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = agentDir;
  t.after(() => { if (previous === undefined) delete process.env.PI_CODING_AGENT_DIR; else process.env.PI_CODING_AGENT_DIR = previous; });
  agent = new PiAgent(config, store, logger, options);
  return { agent, config, options, store, connectors, logs, requests, incoming, failures, agentDir, workspace, mcpContents, isolated, ov };
}

test('real managed Pi discovers direct MCP tools, sees images, and obeys the Core freshness fence', { timeout: 30_000 }, async (t) => {
  const f = await fixture(t);
  await f.agent.start();
  assert.equal(f.requests.length, 0, 'MCP startup must not invoke a model or create a user turn');
  f.agent.deliver('inspect the image and reply', f.incoming.seq);
  await f.agent.whenIdle();
  assert.deepEqual(f.failures, []);
  assert.equal(f.requests.length, 4);
  assert.ok(f.requests[0].tools.some(({ function: tool }) => tool.name === 'mcp__personal_agent__reply_message'));
  assert.match(JSON.stringify(f.requests[1].messages), /data:image\/png;base64,/);
  assert.match(JSON.stringify(f.requests[2].messages.filter(({ role }) => role === 'tool')), /newer authenticated input/);
  assert.equal(f.connectors.calls.filter(({ action }) => action === 'message.reply').length, 1);
  assert.equal(f.connectors.calls.find(({ action }) => action === 'message.reply').input.content.text, 'updated answer');
  const calls = f.logs.filter(({ message }) => message === 'agent_tool');
  assert.equal(calls.length, 3);
  assert.ok(calls.every(({ via, runtime }) => via === 'mcp' && runtime === 'pi'));
  assert.ok(calls.some(({ code }) => code === 'stale_decision'));
  assert.deepEqual(f.store.db.prepare('SELECT status, attempt_count FROM outbox').all().map((row) => ({ ...row })), [
    { status: 'SENT', attempt_count: 1 },
  ]);
  for (const file of [path.join(f.agentDir, 'mcp.json'), path.join(f.workspace, '.pi', 'mcp.json')]) {
    assert.equal(fs.readFileSync(file, 'utf8'), f.mcpContents);
  }
  const sessionId = f.agent.sessionId;
  await f.agent.stop();
  const resumed = new PiAgent(f.config, f.store, logger, f.options);
  t.after(() => resumed.stop());
  await resumed.start();
  assert.equal(resumed.sessionId, sessionId);
  assert.equal(f.requests.length, 4, 'resume only reconnects MCP; it must not replay tool calls');
  await resumed.stop();
});

test('real Pi refuses a user MCP configuration that overrides the daemon server', { timeout: 20_000 }, async (t) => {
  const f = await fixture(t, { conflict: true });
  await assert.rejects(f.agent.start(), /could not load the Core MCP tools/);
  assert.equal(f.requests.length, 0);
  assert.equal(f.connectors.calls.length, 0);
  assert.equal(fs.readFileSync(path.join(f.agentDir, 'mcp.json'), 'utf8'), f.mcpContents);
});

test('real Pi cancels user-extension dialogs and can still complete a tool turn', { timeout: 30_000 }, async (t) => {
  const f = await fixture(t, { dialogs: true });
  await f.agent.start();
  assert.equal(f.requests.length, 0);
  f.agent.deliver('inspect the image and reply', f.incoming.seq);
  await f.agent.whenIdle();
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.agentDir, 'dialog-results.json'), 'utf8')), [false, null, null, null]);
  assert.deepEqual(f.store.db.prepare("SELECT summary FROM activities WHERE kind = 'agent.interaction_declined' ORDER BY id").all()
    .map(({ summary }) => summary), ['extension confirm', 'extension select', 'extension input', 'extension editor']);
  assert.deepEqual(f.failures, []);
  assert.equal(f.connectors.calls.filter(({ action }) => action === 'message.reply').length, 1);
});

test('real Pi runs without OpenViking\'s own extension while the owner\'s others still load', { timeout: 30_000 }, async (t) => {
  const f = await fixture(t, { dialogs: true, openviking: true });
  await f.agent.start();
  f.agent.deliver('inspect the image and reply', f.incoming.seq);
  await f.agent.whenIdle();
  assert.ok(fs.existsSync(path.join(f.agentDir, 'dialog-results.json')), 'the owner\'s extension ran');
  assert.equal(fs.existsSync(path.join(f.agentDir, 'openviking-loaded')), false, 'OpenViking\'s did not');
  assert.deepEqual(f.failures, []);
});

test('real Pi fails startup explicitly when an extension asks before its RPC input reader exists', { timeout: 5_000 }, async (t) => {
  const f = await fixture(t, { dialogs: 'startup' });
  await assert.rejects(f.agent.start(), /interactive dialog during startup/);
  assert.equal(f.agent.child, null);
  assert.equal(f.requests.length, 0);
});


test('real Pi keeps Core OV, built-in, configured/package/project and other MCP tools while blocking OV extensions', { timeout: 45_000 }, async (t) => {
  const f = await fixture(t, { isolation: true });
  await f.agent.start();
  f.agent.deliver('exercise the installed tools', f.incoming.seq);
  await f.agent.whenIdle();
  assert.deepEqual(f.failures, []);
  const names = f.requests[0].tools.map(({ function: tool }) => tool.name);
  for (const name of ['read', 'bash', 'edit', 'write', 'configured_tool', 'packaged_tool', 'project_tool',
    'project_configured_tool', 'project_packaged_tool', 'mcp__global_tools__ping', 'mcp__project_tools__ping']) {
    assert.ok(names.includes(name), `${name} is declared to the real model request`);
  }
  for (const name of ['find', 'search', 'read', 'list', 'add_resource', 'remember']) {
    assert.ok(names.includes(`mcp__personal_agent__openviking_${name}`), `${name} is exposed by OpenViking SuperAgent`);
  }
  assert.equal(names.some((name) => name.startsWith('openviking_') || name === 'disabled_tool'), false);
  for (const marker of f.isolated.markers) assert.equal(fs.existsSync(marker), false, `${path.basename(marker)} never executed`);
  const results = JSON.stringify(f.requests.at(-1).messages.filter(({ role }) => role === 'tool'));
  assert.match(results, /Core memory fixture/);
  for (const name of ['configured_tool', 'packaged_tool', 'project_tool', 'project_configured_tool', 'project_packaged_tool',
    'global_tools', 'project_tools', 'indirect', 'deferred']) assert.ok(results.includes(`${name} worked`), `${name} was actually callable`);
  assert.ok(f.requests.some((request) => request.tools.some(({ function: tool }) => tool.name === 'codemode')));
  assert.ok(f.requests.some((request) => request.tools.some(({ function: tool }) => tool.name === 'tool_search')));
  assert.equal(f.ov.state.requests.filter(({ route }) => route === 'POST /api/v1/search/find').length, 1);
  for (const [file, original] of f.isolated.originals) {
    const actual = JSON.parse(fs.readFileSync(file, 'utf8'));
    const expected = JSON.parse(original);
    // Pi's existing set_steering_mode RPC persists this setting itself.
    if (file === path.join(f.agentDir, 'settings.json')) expected.steeringMode = 'all';
    assert.deepEqual(actual, expected, 'extension and MCP configuration is preserved');
  }
});
