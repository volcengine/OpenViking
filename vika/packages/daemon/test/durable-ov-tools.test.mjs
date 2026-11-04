// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { DURABLE_HOST } from '../src/durable-runtime.mjs';
import { DurableAgent } from '../src/durable.mjs';
import {
  OVToolSource,
  mcpEndpoint,
  toDurableTools,
  ovToolName,
  resolveOvConfig,
} from '../src/durable-ov-tools.mjs';

const PROTOCOL = '2025-06-18';

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'vika-ov-tools-test-'));
}

async function waitFor(predicate, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error('timed out waiting for condition');
}

// A minimal Streamable HTTP MCP server: initialize, tools/list and tools/call.
// `state` drives failures (`down`, `hang`, `httpFail`, `rpcFail`) and records
// every JSON-RPC message with its headers, plus the executed tool calls.
async function fakeMcpServer(state) {
  state.requests = [];
  state.calls = [];
  state.initializations = 0;
  const server = http.createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const raw = Buffer.concat(chunks);
    if (request.method === 'GET') { response.writeHead(405); response.end(); return; }
    if (request.method === 'DELETE') {
      state.requests.push({ method: 'DELETE', headers: request.headers });
      response.writeHead(200); response.end('{}'); return;
    }
    let message;
    try { message = JSON.parse(raw.toString('utf8')); } catch {
      response.writeHead(400); response.end(); return;
    }
    state.requests.push({ method: message.method, id: message.id, params: message.params, headers: request.headers });
    if (state.down) {
      response.writeHead(503, { 'content-type': 'application/json' });
      response.end(JSON.stringify({ status: 'error', error: { message: 'unavailable' } }));
      return;
    }
    if (message.method === 'initialize') {
      if (state.slowInitializeMs) await new Promise((resolve) => setTimeout(resolve, state.slowInitializeMs));
      state.initializations += 1;
      response.writeHead(200, { 'content-type': 'application/json', 'mcp-session-id': `sess-${state.initializations}` });
      response.end(JSON.stringify({
        jsonrpc: '2.0', id: message.id,
        result: {
          protocolVersion: PROTOCOL,
          capabilities: { tools: {} },
          serverInfo: { name: 'fake-ov', version: '1.0' },
        },
      }));
      return;
    }
    if (message.id === undefined || message.id === null) { response.writeHead(202); response.end(); return; }
    if (message.method === 'tools/list') {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(JSON.stringify({ jsonrpc: '2.0', id: message.id, result: { tools: state.tools } }));
      return;
    }
    if (message.method === 'tools/call') {
      const { name, arguments: args } = message.params ?? {};
      if (state.hang) return; // never answer: the client times out
      if (state.httpFail) {
        state.httpFail = false;
        response.writeHead(500, { 'content-type': 'text/plain' });
        response.end('gateway exploded');
        return;
      }
      if (state.rpcFail) {
        state.rpcFail = false;
        response.writeHead(200, { 'content-type': 'application/json' });
        response.end(JSON.stringify({ jsonrpc: '2.0', id: message.id, error: { code: -32000, message: 'tool exploded' } }));
        return;
      }
      state.calls.push({ name, args });
      response.writeHead(200, { 'content-type': 'application/json' });
      response.end(JSON.stringify({
        jsonrpc: '2.0', id: message.id,
        result: { content: [{ type: 'text', text: `called ${name}` }] },
      }));
      return;
    }
    response.writeHead(404); response.end();
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  return {
    state,
    url: `http://127.0.0.1:${port}`,
    async close() {
      server.closeAllConnections?.();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

const HEALTH_TOOL = {
  name: 'health',
  title: 'Check the server',
  description: 'Check that the OpenViking server is healthy.',
  inputSchema: { type: 'object', properties: {} },
  annotations: { readOnlyHint: true },
};
const REMEMBER_TOOL = {
  name: 'remember',
  description: 'Submit messages for long-term memory extraction.',
  inputSchema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] },
};

// The tests build sources with their own readConfig closures, so the mutable
// config scenarios (rotation, dead server, close) stay explicit.

describe('OpenViking tool source configuration', () => {
  it('resolves nothing without OpenViking environment', () => {
    assert.deepEqual(resolveOvConfig({}), {});
  });

  it('reads the instance config file named by OPENVIKING_CLI_CONFIG_FILE', () => {
    const dir = tempDir();
    const file = path.join(dir, 'ov-connection.json');
    fs.writeFileSync(file, JSON.stringify({
      url: 'http://127.0.0.1:1933', api_key: 'k1', account: 'acc', user: 'usr', actor_peer_id: 'inst-1',
    }));
    try {
      const { config, error } = resolveOvConfig({ OPENVIKING_CLI_CONFIG_FILE: file });
      assert.equal(error, undefined);
      assert.deepEqual(config, {
        url: 'http://127.0.0.1:1933', api_key: 'k1', account: 'acc', user: 'usr', actor_peer_id: 'inst-1',
      });
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it('never falls back to another identity when the named config is broken', () => {
    const dir = tempDir();
    const file = path.join(dir, 'broken.json');
    fs.writeFileSync(file, '{not json');
    try {
      const resolved = resolveOvConfig({
        OPENVIKING_CLI_CONFIG_FILE: file,
        OPENVIKING_URL: 'http://127.0.0.1:1933',
      });
      assert.equal(resolved.config, null);
      assert.ok(resolved.error.includes('missing or invalid'));
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it('builds a local dev config from OPENVIKING_URL', () => {
    const { config, error } = resolveOvConfig({
      OPENVIKING_URL: 'http://127.0.0.1:1933',
      OPENVIKING_API_KEY: 'k2',
      OPENVIKING_ACCOUNT: 'acc2',
      OPENVIKING_USER: 'usr2',
    });
    assert.equal(error, undefined);
    assert.deepEqual(config, { url: 'http://127.0.0.1:1933', api_key: 'k2', account: 'acc2', user: 'usr2' });
  });

  it('sends the daemon identity headers plus the actor peer, and no content type', () => {
    const { url, headers } = mcpEndpoint({
      url: 'http://127.0.0.1:1933', api_key: 'k1', account: 'acc', user: 'usr', agent: 'ag', actor_peer_id: 'inst-1',
    }, {});
    assert.equal(url, 'http://127.0.0.1:1933/mcp');
    assert.deepEqual(headers, {
      'X-API-Key': 'k1',
      'X-OpenViking-Account': 'acc',
      'X-OpenViking-User': 'usr',
      'X-OpenViking-Agent': 'ag',
      'X-OpenViking-Actor-Peer': 'inst-1',
    });
  });

  it('prefers OPENVIKING_PEER_ID over the config peer', () => {
    const { headers } = mcpEndpoint({ url: 'http://127.0.0.1:1933', actor_peer_id: 'inst-1' }, {
      OPENVIKING_PEER_ID: 'override',
    });
    assert.equal(headers['X-OpenViking-Actor-Peer'], 'override');
  });

  it('rejects an invalid URL', () => {
    assert.throws(() => mcpEndpoint({ url: 'ftp://127.0.0.1/x' }, {}), /OpenViking URL/);
  });
});

describe('OpenViking tool catalogue conversion', () => {
  it('names tools openviking_ + the server name, sanitized and capped', () => {
    assert.equal(ovToolName('find'), 'openviking_find');
    assert.equal(ovToolName('add-resource'), 'openviking_add-resource');
    assert.equal(ovToolName('we!rd name'), 'openviking_we_rd_name');
    assert.equal(ovToolName('x'.repeat(80)).length, 64);
    assert.match(ovToolName('x'.repeat(80)), /^openviking_[A-Za-z0-9_-]+$/);
  });

  it('passes the server schema through and defaults missing properties', () => {
    const stub = { callTool: async () => ({ content: [] }) };
    const withSchema = toDurableTools(stub, [REMEMBER_TOOL])[0];
    assert.equal(withSchema.name, 'openviking_remember');
    assert.deepEqual(withSchema.parameters, {
      type: 'object', properties: { text: { type: 'string' } }, required: ['text'],
    });
    const withoutSchema = toDurableTools(stub, [{ name: 'bare', description: 'd' }])[0];
    assert.deepEqual(withoutSchema.parameters, { type: 'object', properties: {} });
  });

  it('marks server-read-only tools replay-safe and leaves writes unsafe', () => {
    const stub = { callTool: async () => ({ content: [] }) };
    const [health, remember] = toDurableTools(stub, [HEALTH_TOOL, REMEMBER_TOOL]);
    assert.equal(health.replay, 'safe');
    assert.equal(remember.replay, undefined);
  });

  it('describes a tool from its title when the description is empty', () => {
    const stub = { callTool: async () => ({ content: [] }) };
    const [tool] = toDurableTools(stub, [{ name: 'health', title: 'Check the server', description: '   ' }]);
    assert.equal(tool.description, 'Check the server');
  });

  it('skips nameless tools and colliding sanitized names', () => {
    const stub = { callTool: async () => ({ content: [] }) };
    const tools = toDurableTools(stub, [
      { name: 'fi nd', description: 'a' },
      { name: 'fi!nd', description: 'b' },
      { title: 'no name' },
    ]);
    assert.deepEqual(tools.map((tool) => tool.name), ['openviking_fi_nd']);
    assert.equal(tools[0].description, 'a', 'the first descriptor wins a collision');
  });
});

describe('OpenViking tool source connection', () => {
  it('connects and lists the catalogue with the configured headers', async () => {
    const fake = await fakeMcpServer({ tools: [HEALTH_TOOL, REMEMBER_TOOL] });
    try {
      const instance = new OVToolSource({
        readConfig: () => ({ url: fake.url, api_key: 'k1', account: 'acc', user: 'usr' }),
        environment: { OPENVIKING_PEER_ID: 'peer-1' },
        handshakeMs: 2_000,
      });
      const tools = await instance.connect();
      assert.equal(tools.length, 2);
      const initialize = fake.state.requests.find((request) => request.method === 'initialize');
      assert.ok(initialize, 'the server saw the handshake');
      assert.equal(initialize.headers['x-api-key'], 'k1');
      assert.equal(initialize.headers['x-openviking-account'], 'acc');
      assert.equal(initialize.headers['x-openviking-user'], 'usr');
      assert.equal(initialize.headers['x-openviking-actor-peer'], 'peer-1');
      await instance.close();
    } finally {
      await fake.close();
    }
  });

  it('executes a converted tool and converts the result for the model', async () => {
    const fake = await fakeMcpServer({ tools: [HEALTH_TOOL] });
    try {
      const instance = new OVToolSource({ readConfig: () => ({ url: fake.url }), handshakeMs: 2_000 });
      const catalogue = await instance.connect();
      const health = toDurableTools(instance, catalogue)[0];
      assert.equal(health.name, 'openviking_health');
      const result = await health.execute({});
      assert.deepEqual(result.output, [{ type: 'text', text: 'called health' }]);
      assert.equal(result.isError, false);
      assert.deepEqual(fake.state.calls, [{ name: 'health', args: {} }]);
      await instance.close();
    } finally {
      await fake.close();
    }
  });

  it('keeps the connection after a server-side tool error', async () => {
    const fake = await fakeMcpServer({ tools: [HEALTH_TOOL], rpcFail: true });
    try {
      const instance = new OVToolSource({ readConfig: () => ({ url: fake.url }), handshakeMs: 2_000 });
      await instance.connect();
      await assert.rejects(() => instance.callTool('health', {}), /tool exploded/);
      fake.state.rpcFail = false;
      const result = await instance.callTool('health', {});
      assert.deepEqual(result.content, [{ type: 'text', text: 'called health' }]);
      assert.equal(fake.state.initializations, 1, 'a JSON-RPC error does not rebuild the connection');
      await instance.close();
    } finally {
      await fake.close();
    }
  });

  it('drops the connection after an HTTP failure and reconnects on the next call', async () => {
    const fake = await fakeMcpServer({ tools: [HEALTH_TOOL], httpFail: true });
    try {
      const instance = new OVToolSource({ readConfig: () => ({ url: fake.url }), handshakeMs: 2_000 });
      await instance.connect();
      await assert.rejects(() => instance.callTool('health', {}), /500/);
      const result = await instance.callTool('health', {});
      assert.deepEqual(result.content, [{ type: 'text', text: 'called health' }]);
      assert.equal(fake.state.initializations, 2, 'a transport failure is rebuilt by the next call');
      await instance.close();
    } finally {
      await fake.close();
    }
  });

  it('times out a hanging call and reconnects on the next call', async () => {
    const fake = await fakeMcpServer({ tools: [HEALTH_TOOL], hang: true });
    try {
      const instance = new OVToolSource({
        readConfig: () => ({ url: fake.url }), handshakeMs: 2_000, callTimeoutMs: 300,
      });
      await instance.connect();
      await assert.rejects(() => instance.callTool('health', {}), /timed out|timeout/i);
      fake.state.hang = false;
      const result = await instance.callTool('health', {});
      assert.deepEqual(result.content, [{ type: 'text', text: 'called health' }]);
      assert.equal(fake.state.initializations, 2, 'a timed-out call rebuilds the connection');
      await instance.close();
    } finally {
      await fake.close();
    }
  });

  it('replaces the connection when the config changes', async () => {
    const first = await fakeMcpServer({ tools: [HEALTH_TOOL] });
    const second = await fakeMcpServer({ tools: [HEALTH_TOOL] });
    try {
      let url = first.url;
      const instance = new OVToolSource({ readConfig: () => ({ url }), handshakeMs: 2_000 });
      await instance.connect();
      await instance.callTool('health', {});
      url = second.url;
      const result = await instance.callTool('health', {});
      assert.deepEqual(result.content, [{ type: 'text', text: 'called health' }]);
      assert.equal(second.state.initializations, 1);
      assert.deepEqual(second.state.calls.map((call) => call.name), ['health']);
      assert.equal(first.state.calls.length, 1, 'the old connection no longer receives calls');
      await waitFor(() => first.state.requests.some((request) => request.method === 'DELETE'), 2_000);
      await instance.close();
    } finally {
      await first.close();
      await second.close();
    }
  });

  it('refuses to call without a config', async () => {
    const instance = new OVToolSource({ readConfig: () => null });
    await assert.rejects(() => instance.connect(), /OpenViking configuration is unavailable/);
    await assert.rejects(() => instance.callTool('health', {}), /OpenViking configuration is unavailable/);
  });

  it('fails the handshake against a dead server', async () => {
    const instance = new OVToolSource({
      readConfig: () => ({ url: 'http://127.0.0.1:1' }), handshakeMs: 1_000,
    });
    await assert.rejects(() => instance.connect());
    await instance.close();
  });

  it('closes cleanly and refuses further calls', async () => {
    const fake = await fakeMcpServer({ tools: [HEALTH_TOOL] });
    try {
      const instance = new OVToolSource({ readConfig: () => ({ url: fake.url }), handshakeMs: 2_000 });
      await instance.connect();
      await instance.close();
      await assert.rejects(() => instance.callTool('health', {}), /closed/);
      await assert.rejects(() => instance.connect(), /closed/);
    } finally {
      await fake.close();
    }
  });
});

// The host end to end: a faux model drives the durable harness, and the
// OpenViking catalogue the source registered answers a real tool call.

function recordingLogger() {
  const records = [];
  const sink = (level) => (event, meta) => { records.push({ level, event, meta }); };
  return {
    records,
    debug: sink('debug'), info: sink('info'), warn: sink('warn'), error: sink('error'),
    child() { return this; },
  };
}

function hostLogEntries(logger, type) {
  return logger.records
    .filter((record) => record.level === 'debug' && record.event === 'durable_stderr')
    .map((record) => record.meta?.detail)
    .map((detail) => {
      try { return JSON.parse(String(detail)); } catch { return null; }
    })
    .filter((entry) => entry?.type === type);
}

function baseConfig(overrides = {}) {
  const dataDir = overrides.dataDir ?? tempDir();
  return {
    durableHostPath: DURABLE_HOST,
    durableModel: '',
    durableEffort: 'medium',
    workspace: overrides.workspace ?? tempDir(),
    dataDir,
    databasePath: path.join(dataDir, 'test.sqlite3'),
    profile: 'test-profile',
    connectors: ['lark'],
    actionSocketPath: path.join(dataDir, 'actions.sock'),
    sendTimeoutMs: 5_000,
    maxReplyChars: 1_000,
    dryRun: true,
    ...overrides,
  };
}

function makeStore() {
  let global = { sessionId: null, policyHash: null, runtime: null, epoch: 0, syncedThroughSeq: 0, sessionReady: false };
  const activities = [];
  return {
    activities,
    getCognitionState: () => ({ runtime: 'durable', epoch: 1, authorityToken: 'token-durable' }),
    getGlobalSession: () => global,
    setGlobalSession(sessionId, policyHash, fields) {
      global = { sessionId, policyHash, ...fields };
    },
    publishOwnedSession({ runtime: owner, sessionId, policyHash, epoch, acceptedThroughSeq, sessionReady, nativeSessionId = null }) {
      global = { sessionId, policyHash, runtime: owner, epoch, syncedThroughSeq: acceptedThroughSeq, sessionReady, nativeSessionId };
      return true;
    },
    advanceOwnedSessionAcceptance: () => true,
    global: () => global,
    getNativeSessionId: () => (global.sessionId ? global.nativeSessionId ?? null : null),
    getSetting: () => null,
    setSetting() {},
    recordActivity(activity) { activities.push(activity); },
    getOVContext: () => ({ sessionId: 'ov-session', messages: [] }),
    cognitionJournal: () => [],
    cognitionJournalBeforeInbound: () => 0,
    cognitionJournalSeqForInbound: () => 0,
    prepareOVContext: async () => {},
  };
}

describe('durable host OpenViking tools', () => {
  it('serves the catalogue to the model and keeps it across a restart', async () => {
    const fake = await fakeMcpServer({ tools: [HEALTH_TOOL, REMEMBER_TOOL] });
    const dataDir = tempDir();
    const config = baseConfig({ dataDir });
    const store = makeStore();
    const logger = recordingLogger();
    const successes = [];
    process.env.DURABLE_FAUX = '1';
    process.env.DURABLE_OV = '1';
    process.env.DURABLE_FAUX_RESPONSES = JSON.stringify([
      { toolCall: { name: 'openviking_health', arguments: {} } },
      { text: 'ov done' },
    ]);
    process.env.OPENVIKING_URL = fake.url;
    process.env.OPENVIKING_PEER_ID = 'instance-e2e';
    let agent;
    try {
      agent = new DurableAgent(config, store, logger, { onTurnSucceeded: () => successes.push(1) });
      await agent.start();
      await waitFor(() => hostLogEntries(logger, 'ov_tools_ready').length >= 1, 5_000);
      const ready = hostLogEntries(logger, 'ov_tools_ready').at(-1);
      assert.equal(ready.tools, 2);

      agent.deliver('check openviking', 7);
      await waitFor(() => successes.length === 1, 20_000);
      const called = store.activities.filter((activity) => activity.kind === 'agent.tool_called');
      assert.deepEqual(called.map((activity) => activity.summary), ['openviking_health']);
      const completed = store.activities.find((activity) => activity.kind === 'agent.tool_completed');
      assert.equal(completed.status, 'COMPLETED');
      assert.ok(store.activities.some((activity) => activity.kind === 'agent.note' && activity.summary === 'ov done'));
      const initialize = fake.state.requests.find((request) => request.method === 'initialize');
      assert.equal(initialize.headers['x-openviking-actor-peer'], 'instance-e2e');
      assert.deepEqual(fake.state.calls.map((call) => call.name), ['health']);
      const conversation = store.global().nativeSessionId;
      assert.ok(Number.isSafeInteger(conversation));
      await agent.stop();

      // A fresh host process reopens the same conversation and re-registers
      // the OpenViking tools from the same config.
      const secondLogger = recordingLogger();
      const secondSuccesses = [];
      const second = new DurableAgent(config, store, secondLogger, {
        onTurnSucceeded: () => secondSuccesses.push(1),
      });
      await second.start();
      assert.equal(second.nativeSessionId, conversation);
      await waitFor(() => hostLogEntries(secondLogger, 'ov_tools_ready').length >= 1, 5_000);
      second.deliver('check openviking again', 8);
      await waitFor(() => secondSuccesses.length === 1, 20_000);
      assert.deepEqual(fake.state.calls.map((call) => call.name), ['health', 'health']);
      await second.stop();
    } finally {
      await agent?.stop().catch(() => {});
      delete process.env.DURABLE_FAUX;
      delete process.env.DURABLE_OV;
      delete process.env.DURABLE_FAUX_RESPONSES;
      delete process.env.OPENVIKING_URL;
      delete process.env.OPENVIKING_PEER_ID;
      await fake.close();
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it('starts without OpenViking tools and stays quiet when nothing is configured', async () => {
    const dataDir = tempDir();
    const config = baseConfig({ dataDir });
    const store = makeStore();
    const logger = recordingLogger();
    const successes = [];
    process.env.DURABLE_FAUX = '1';
    process.env.DURABLE_FAUX_RESPONSES = JSON.stringify([{ text: 'no ov answer' }]);
    delete process.env.OPENVIKING_URL;
    delete process.env.OPENVIKING_CLI_CONFIG_FILE;
    let agent;
    try {
      agent = new DurableAgent(config, store, logger, { onTurnSucceeded: () => successes.push(1) });
      await agent.start();
      agent.deliver('hello without openviking', 7);
      await waitFor(() => successes.length === 1, 20_000);
      assert.equal(hostLogEntries(logger, 'ov_tools_ready').length, 0);
      assert.equal(hostLogEntries(logger, 'ov_tools_unavailable').length, 0);
    } finally {
      await agent?.stop().catch(() => {});
      delete process.env.DURABLE_FAUX;
      delete process.env.DURABLE_FAUX_RESPONSES;
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it('fails open when the OpenViking server is unreachable', async () => {
    const dataDir = tempDir();
    const config = baseConfig({ dataDir });
    const store = makeStore();
    const logger = recordingLogger();
    const successes = [];
    process.env.DURABLE_FAUX = '1';
    process.env.DURABLE_OV = '1';
    process.env.DURABLE_FAUX_RESPONSES = JSON.stringify([{ text: 'offline answer' }]);
    process.env.OPENVIKING_URL = 'http://127.0.0.1:1';
    let agent;
    try {
      agent = new DurableAgent(config, store, logger, { onTurnSucceeded: () => successes.push(1) });
      await agent.start();
      await waitFor(() => hostLogEntries(logger, 'ov_tools_unavailable').length >= 1, 10_000);
      agent.deliver('hello while openviking is down', 7);
      await waitFor(() => successes.length === 1, 20_000);
      assert.equal(hostLogEntries(logger, 'ov_tools_ready').length, 0);
    } finally {
      await agent?.stop().catch(() => {});
      delete process.env.DURABLE_FAUX;
      delete process.env.DURABLE_OV;
      delete process.env.DURABLE_FAUX_RESPONSES;
      delete process.env.OPENVIKING_URL;
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });

  it('replays a crashed OpenViking tool call after the host restarts', async () => {
    const fake = await fakeMcpServer({ tools: [HEALTH_TOOL], hang: true });
    const dataDir = tempDir();
    const config = baseConfig({ dataDir });
    const store = makeStore();
    const logger = recordingLogger();
    const successes = [];
    const failures = [];
    process.env.DURABLE_FAUX = '1';
    process.env.DURABLE_OV = '1';
    process.env.DURABLE_FAUX_RESPONSES = JSON.stringify([
      { toolCall: { name: 'openviking_health', arguments: {} } },
      { text: 'recovered' },
    ]);
    process.env.OPENVIKING_URL = fake.url;
    let agent;
    try {
      agent = new DurableAgent(config, store, logger, {
        reconnectBaseMs: 50,
        onTurnFailed: (failure) => failures.push(failure),
        onTurnSucceeded: () => successes.push(1),
      });
      await agent.start();
      await waitFor(() => hostLogEntries(logger, 'ov_tools_ready').length >= 1, 5_000);
      agent.deliver('crash inside the tool call', 7);
      // The call hangs on the fake server, so the SIGKILL lands inside the
      // tool's execution, past its recorded intent: the restarted host must
      // register the catalogue before resuming, then replay the call.
      await waitFor(() => fake.state.requests.some((request) => request.method === 'tools/call'), 10_000);
      fake.state.hang = false;
      // The restarted host's handshake answers slowly, so a resume that does
      // not wait for the catalogue would find the tool missing.
      fake.state.slowInitializeMs = 1_000;
      agent.child.kill('SIGKILL');
      await waitFor(() => successes.length === 1, 30_000);
      assert.deepEqual(failures, []);
      const completed = store.activities.filter((activity) => activity.kind === 'agent.tool_completed');
      assert.ok(completed.length >= 1);
      assert.ok(completed.every((activity) => activity.status === 'COMPLETED'),
        'a replayed call is never reported as an unavailable tool');
      const toolCalls = fake.state.requests.filter((request) => request.method === 'tools/call');
      assert.ok(toolCalls.length >= 2, 'the interrupted call was replayed after the restart');
      assert.ok(store.activities.some((activity) => activity.kind === 'agent.note' && activity.summary === 'recovered'));
    } finally {
      await agent?.stop().catch(() => {});
      delete process.env.DURABLE_FAUX;
      delete process.env.DURABLE_OV;
      delete process.env.DURABLE_FAUX_RESPONSES;
      delete process.env.OPENVIKING_URL;
      await fake.close();
      fs.rmSync(dataDir, { recursive: true, force: true });
    }
  });
});