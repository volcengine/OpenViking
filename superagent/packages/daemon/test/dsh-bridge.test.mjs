// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// The vika/dsh JSON-RPC bridge: LF framing, method dispatch, error classification.

import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { PassThrough } from 'node:stream';
import { test } from './dsh-node-gate.mjs';
import {
  createBridgeHandlers, DshBridgeFramer, MAX_FRAME_BYTES, wireDshBridge,
} from '../src/dsh-bridge/protocol.mjs';
import { bridgeErrorFrame } from '../src/dsh-bridge/handlers.mjs';

function createUserMessage({ content, source }) {
  return Object.freeze({ id: randomUUID(), role: 'user', content: structuredClone(content), source });
}

function fixture({ services = {} } = {}) {
  const agents = new Map();
  const agent = {
    steered: [], cancels: [], selections: [], status: 'running',
    session: { header: { origin: 'root' } },
    steer(message) { this.steered.push(message); },
    // 0.2.2-rc.1 declares `cancel(cause, options): void`; returning nothing
    // proves the bridge does not depend on a return value.
    cancel(...args) { this.cancels.push(args); },
  };
  agent.model = { select(selection) { agent.selections.push(selection); } };
  agents.set('session', agent);
  // Services are resolved per request, exactly as in a live DSH process.
  const ctx = { agents, get: (name) => services[name] };
  return { agents, agent, ctx, handlers: createBridgeHandlers({ ctx, agents, createUserMessage }) };
}

function captureConsoleError(t) {
  const original = console.error;
  const lines = [];
  console.error = (...args) => { lines.push(args.map(String).join(' ')); };
  t.after(() => { console.error = original; });
  return lines;
}

async function waitUntil(predicate, label = 'condition') {
  const deadline = Date.now() + 3000;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${label}`);
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}

// Wires the real bridge over a PassThrough stdin/stdout pair, with a stock
// server stub that only records what the bridge forwards to it.
function harness(t, { agents = new Map(), services = {} } = {}) {
  const input = new PassThrough();
  const output = new PassThrough();
  const forwarded = [];
  let outputText = '';
  output.on('data', chunk => { outputText += chunk.toString(); });
  const ctx = { agents, get: name => services[name] };
  let applied = null;
  const stock = {
    apply(gotCtx, config) {
      assert.equal(gotCtx, ctx);
      applied = config;
      config.input.on('data', chunk => forwarded.push(chunk));
      return config;
    },
  };
  wireDshBridge({ ctx, stock, createUserMessage, config: { maxTokensAsSuccess: true }, input, output });
  t.after(() => { input.destroy(); output.destroy(); });
  return {
    input, output, ctx, stock,
    applied: () => applied,
    frames: () => outputText.trim().split('\n').filter(Boolean).map(JSON.parse),
    outputText: () => outputText,
    forwardedText: () => Buffer.concat(forwarded).toString('utf8'),
    forwardedBytes: () => Buffer.concat(forwarded),
  };
}

function request(method, params, id = randomUUID()) {
  return `${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`;
}

test('bridge hello, steer and cancel use native agent operations', () => {
  const { handlers, agent } = fixture();
  const hello = handlers['vika/hello']();
  assert.equal(hello.bridge, 'vika-dsh-bridge');
  assert.equal(hello.version, 2);
  // The advertised list IS the routing surface, so the two can never drift.
  assert.deepEqual(
    [...hello.methods].sort(),
    Object.keys(handlers).filter(name => name !== 'vika/hello').sort(),
  );
  assert.ok(hello.methods.includes('session/steer'));
  assert.ok(hello.methods.includes('session/cancel'));
  const result = handlers['session/steer']({ sessionId: 'session', contentBlocks: [{ type: 'text', text: 'continue' }] });
  assert.equal(agent.steered.length, 1);
  assert.equal(agent.steered[0].id, result.messageId);
  assert.equal(agent.steered[0].role, 'user');
  assert.deepEqual(agent.steered[0].source, { kind: 'user' });
  assert.deepEqual(handlers['session/cancel']({ sessionId: 'session' }), { cancelled: true });
  assert.deepEqual(agent.cancels, [[{ kind: 'user' }, { keepInbox: false }]]);
  handlers['session/cancel']({ sessionId: 'session', keepInbox: true });
  assert.equal(agent.cancels[1][1].keepInbox, true);
});

test('cancel reports whether work was live from the agent status property', () => {
  const { handlers, agent } = fixture();
  agent.status = 'idle';
  assert.deepEqual(handlers['session/cancel']({ sessionId: 'session' }), { cancelled: false });
  assert.equal(agent.cancels.length, 1);
  agent.status = 'running';
  assert.deepEqual(handlers['session/cancel']({ sessionId: 'session' }), { cancelled: true });
});

test('bridge rejects unloaded sessions and unsupported prompt blocks', () => {
  const { handlers, agent } = fixture();
  for (const params of [
    { sessionId: 'missing', contentBlocks: [{ type: 'text', text: 'x' }] },
    { sessionId: 'session', contentBlocks: [] },
    { sessionId: 'session' },
    { sessionId: 'session', contentBlocks: [{ type: 'image', data: 'x' }] },
    { sessionId: 'session', contentBlocks: [{ type: 'text' }] },
  ]) {
    assert.throws(() => handlers['session/steer'](params), error => error.code === -32602);
  }
  assert.throws(() => handlers['session/cancel']({ sessionId: 'missing' }), error => error.code === -32602);
  assert.throws(() => handlers['session/cancel']({ sessionId: 'session', keepInbox: 'no' }), error => error.code === -32602);
  assert.equal(agent.steered.length, 0);
});

test('bridge wraps native agent errors as internal JSON-RPC failures', () => {
  const { handlers, agent } = fixture();
  agent.steer = () => { throw new Error('agent failed'); };
  assert.throws(() => handlers['session/steer']({ sessionId: 'session', contentBlocks: [{ type: 'text', text: 'x' }] }), error => error.message === 'agent failed' && !error.code);
});

test('bridge forwards stock frames byte-for-byte and propagates EOF', async t => {
  const h = harness(t);
  h.input.write('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n');
  h.input.write('not-json\n');
  h.input.end();
  await waitUntil(() => h.forwardedText() === '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\nnot-json\n', 'forwarded input');
  await waitUntil(() => h.applied()?.input.writableEnded === true, 'forwarded EOF');
  assert.equal(h.applied().maxTokensAsSuccess, true);
  assert.equal(h.applied().output, h.output);
});

test('bridge handles its own requests without forwarding them', async t => {
  const { agents, agent } = fixture();
  const h = harness(t, { agents });
  h.input.write('{"jsonrpc":"2.0","id":"a","method":"vika/hello","params":{}}\n');
  h.input.write('{"jsonrpc":"2.0","id":"b","method":"session/steer","params":{"sessionId":"session","contentBlocks":[{"type":"text","text":"go"}]}}\n');
  h.input.write('{"jsonrpc":"2.0","id":"c","method":"session/steer","params":{"sessionId":"absent","contentBlocks":[{"type":"text","text":"go"}]}}\n');
  h.input.write('{"jsonrpc":"2.0","id":"d","method":"session/cancel","params":{"sessionId":"session"}}\n');
  h.input.write('{"method":"vika/hello"}\n');
  h.input.end();
  await waitUntil(() => h.frames().length === 4, 'four bridge responses');
  const frames = h.frames();
  assert.equal(frames.find(frame => frame.id === 'a').result.version, 2);
  assert.equal(frames.find(frame => frame.id === 'b').result.messageId, agent.steered[0].id);
  assert.equal(frames.find(frame => frame.id === 'c').error.code, -32602);
  assert.deepEqual(frames.find(frame => frame.id === 'd').result, { cancelled: true });
  assert.equal(h.forwardedText(), '{"method":"vika/hello"}\n');
});

test('a steer text with U+2028 and U+2029 stays one frame and reaches the agent', async t => {
  const { agents, agent } = fixture();
  const h = harness(t, { agents });
  const text = 'first line\u2028second line\u2029third';
  h.input.write(request('session/steer', { sessionId: 'session', contentBlocks: [{ type: 'text', text }] }));
  h.input.end();
  await waitUntil(() => h.frames().length === 1, 'steer response');
  assert.equal(h.frames()[0].error, undefined, h.outputText());
  assert.equal(agent.steered.length, 1);
  assert.equal(agent.steered[0].content[0].text, text);
  assert.equal(h.forwardedBytes().length, 0, 'the frame is the bridge\'s own');
});

test('U+2028 and U+2029 in a forwarded frame keep their bytes and stay one frame', async t => {
  const h = harness(t);
  const frame = JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'session/prompt', params: { contentBlocks: [{ type: 'text', text: 'a\u2028b\u2029c' }] } });
  h.input.write(`${frame}\n`);
  h.input.end();
  await waitUntil(() => h.forwardedBytes().length > 0, 'forwarded frame');
  assert.equal(h.forwardedText(), `${frame}\n`);
  assert.equal(h.outputText(), '', 'nothing on stdout for a forwarded request');
});

test('a chunk boundary inside a multi-byte character does not corrupt the frame', async t => {
  const { agents, agent } = fixture();
  const h = harness(t, { agents });
  const text = '你好，世界';
  const payload = Buffer.from(request('session/steer', { sessionId: 'session', contentBlocks: [{ type: 'text', text }] }), 'utf8');
  const split = payload.indexOf(Buffer.from('你', 'utf8')) + 1;
  assert.ok(split > 1 && split < payload.length, 'the split point is inside the first character');
  h.input.write(payload.subarray(0, split));
  h.input.write(payload.subarray(split));
  h.input.end();
  await waitUntil(() => h.frames().length === 1, 'steer response');
  assert.equal(agent.steered[0].content[0].text, text);
});

test('CRLF frames parse and their carriage return is not content', async t => {
  const { agents, agent } = fixture();
  const h = harness(t, { agents });
  h.input.write('{"jsonrpc":"2.0","id":"crlf","method":"session/steer","params":{"sessionId":"session","contentBlocks":[{"type":"text","text":"line"}]}}\r\n');
  h.input.end();
  await waitUntil(() => h.frames().length === 1, 'steer response');
  assert.equal(h.frames()[0].id, 'crlf');
  assert.equal(agent.steered[0].content[0].text, 'line');
  assert.equal(h.forwardedBytes().length, 0);
});

test('a whole frame in one oversized line is dropped, warned and survived', async t => {
  const warnings = captureConsoleError(t);
  const { agents, agent } = fixture();
  const h = harness(t, { agents });
  const oversized = `{"jsonrpc":"2.0","id":"big","method":"session/steer","params":${JSON.stringify({ sessionId: 'session', contentBlocks: [{ type: 'text', text: 'x'.repeat(MAX_FRAME_BYTES) }] })}}\n`;
  h.input.write(oversized);
  h.input.write(request('vika/hello', {}, 'after'));
  h.input.end();
  await waitUntil(() => h.frames().length === 1, 'the next frame is answered');
  assert.equal(h.frames()[0].id, 'after', 'the oversized frame produced no response');
  assert.equal(agent.steered.length, 0);
  assert.equal(h.forwardedBytes().length, 0, 'the oversized line is not forwarded either');
  assert.ok(warnings.some(line => line.includes(`over ${MAX_FRAME_BYTES} bytes`)), warnings.join('\n'));
});

test('a malformed frame is forwarded unchanged', async t => {
  const h = harness(t);
  const malformed = Buffer.concat([
    Buffer.from('{"jsonrpc":"2.0","id":9,"broken":', 'utf8'),
    Buffer.from('\u2028', 'utf8'),
    Buffer.from('"tail"', 'utf8'),
  ]);
  h.input.write(malformed);
  h.input.write(Buffer.from('\n', 'utf8'));
  h.input.write('{"jsonrpc":"2.0","id":"ok","method":"vika/hello","params":{}}\n');
  h.input.end();
  await waitUntil(() => h.frames().length === 1, 'hello response');
  assert.equal(h.frames()[0].id, 'ok');
  assert.deepEqual(h.forwardedBytes(), Buffer.concat([malformed, Buffer.from('\n', 'utf8')]));
});

test('a final frame without a trailing newline is delivered at EOF', async t => {
  const h = harness(t);
  h.input.write('{"jsonrpc":"2.0","id":"tail","method":"vika/hello","params":{}}');
  h.input.end();
  await waitUntil(() => h.frames().length === 1, 'tail frame response');
  assert.equal(h.frames()[0].id, 'tail');
  assert.equal(h.frames()[0].result.version, 2);
  await waitUntil(() => h.applied()?.input.writableEnded === true, 'forwarded EOF');
});

test('malformed params are -32602 and never reach a handler', async t => {
  const { agents, agent } = fixture();
  const h = harness(t, { agents });
  const cases = [
    ['hello', 'vika/hello', 'not-an-object'],
    ['hello-array', 'vika/hello', ['not', 'an', 'object']],
    ['hello-number', 'vika/hello', 7],
    ['cancel-null', 'session/cancel', null],
  ];
  for (const [id, method, params] of cases) h.input.write(request(method, params, id));
  await waitUntil(() => h.frames().length === cases.length, 'one response per request');
  const frames = h.frames();
  for (const [id] of cases) {
    const frame = frames.find(candidate => candidate.id === id);
    assert.equal(frame.error?.code, -32602, `${id}: ${JSON.stringify(frame)}`);
    assert.equal(frame.error?.message, 'params must be an object');
    assert.equal(frame.result, undefined);
  }
  assert.equal(agent.cancels.length, 0);
});

test('bridge validation errors are -32602 and handler failures are -32603', async t => {
  const { agents, agent } = fixture();
  const h = harness(t, { agents });
  const cases = [
    ['steer-no-session', 'session/steer', { contentBlocks: [{ type: 'text', text: 'x' }] }],
    ['steer-blocks-not-array', 'session/steer', { sessionId: 'session', contentBlocks: 'text' }],
    ['steer-empty', 'session/steer', { sessionId: 'session', contentBlocks: [] }],
    ['steer-non-text', 'session/steer', { sessionId: 'session', contentBlocks: [{ type: 'image', data: 'x' }] }],
    ['steer-text-not-string', 'session/steer', { sessionId: 'session', contentBlocks: [{ type: 'text', text: 5 }] }],
    ['steer-unknown-session', 'session/steer', { sessionId: 'absent', contentBlocks: [{ type: 'text', text: 'x' }] }],
    ['cancel-keep-inbox', 'session/cancel', { sessionId: 'session', keepInbox: 'no' }],
    ['cancel-unknown-session', 'session/cancel', { sessionId: 'absent' }],
  ];
  for (const [id, method, params] of cases) h.input.write(request(method, params, id));
  await waitUntil(() => h.frames().length === cases.length, 'one response per request');
  const frames = h.frames();
  for (const [id] of cases) assert.equal(frames.find(frame => frame.id === id).error?.code, -32602, id);
  assert.equal(agent.steered.length, 0);

  agent.steer = () => { throw new Error('agent failed'); };
  h.input.write(request('session/steer', { sessionId: 'session', contentBlocks: [{ type: 'text', text: 'x' }] }, 'boom'));
  await waitUntil(() => h.frames().some(frame => frame.id === 'boom'), 'handler failure response');
  const failure = h.frames().find(frame => frame.id === 'boom');
  assert.deepEqual(failure.error, { code: -32603, message: 'agent failed' });
  // Everything written to stdout still parses as a JSON-RPC frame.
  for (const line of h.outputText().trim().split('\n')) assert.ok(JSON.parse(line), line);
});

test('a request the bridge does not handle is forwarded untouched', async t => {
  const { agents } = fixture();
  const h = harness(t, { agents });
  const frame = request('session/prompt', { sessionId: 'session', contentBlocks: [{ type: 'text', text: 'go' }] }, 'fwd');
  h.input.write(frame);
  h.input.end();
  await waitUntil(() => h.forwardedText() === frame, 'forwarded prompt');
  assert.equal(h.outputText(), '');
});

test('the framer drops only the line over the limit and keeps the stream', () => {
  const frames = [];
  const oversized = [];
  const framer = new DshBridgeFramer({
    onFrame: ({ text }) => frames.push(text),
    onOversized: prefix => oversized.push(prefix),
    maxFrameBytes: 8,
  });
  framer.push('123456789');
  framer.push('abcdefgh\n');
  framer.push('ok\n');
  framer.end();
  assert.deepEqual(frames, ['ok']);
  assert.equal(oversized.length, 1);
  assert.equal(oversized[0], '123456789');
});

test('the framer keeps U+2028 and U+2029 inside a frame and splits only on LF', () => {
  const frames = [];
  const framer = new DshBridgeFramer({ onFrame: ({ text }) => frames.push(text) });
  framer.push('a\u2028b\u2029c\nd\n');
  framer.end();
  assert.deepEqual(frames, ['a\u2028b\u2029c', 'd']);
});

test('a frame exactly at the limit is kept, one byte over is dropped', async () => {
  // The parity with the daemon's line bound is asserted in dsh-turn.test.mjs,
  // where the transport module lives: this suite is the bridge alone.
  const frames = [];
  const oversized = [];
  const framer = new DshBridgeFramer({
    onFrame: ({ text }) => frames.push(text),
    onOversized: prefix => oversized.push(prefix),
  });
  // Exactly at the limit is still a frame; one byte over is dropped.
  framer.push(`${'a'.repeat(MAX_FRAME_BYTES)}\n`);
  framer.push(`${'b'.repeat(MAX_FRAME_BYTES + 1)}\n`);
  framer.push('after\n');
  framer.end();
  assert.deepEqual(frames, ['a'.repeat(MAX_FRAME_BYTES), 'after']);
  assert.equal(oversized.length, 1);
  assert.equal(oversized[0], 'b'.repeat(512));
});

test('a stdin that already ended is closed for the stock server too', async t => {
  const input = new PassThrough();
  const output = new PassThrough();
  let config = null;
  const ctx = { agents: new Map() };
  input.end();
  // A Readable reports readableEnded only after its buffer is drained.
  await new Promise(resolve => { input.on('end', resolve); input.resume(); });
  wireDshBridge({ ctx, stock: { apply(_ctx, got) { config = got; } }, createUserMessage, input, output });
  t.after(() => { output.destroy(); });
  assert.ok(config, 'the stock server is applied');
  await waitUntil(() => config.input.writableEnded === true, 'forwarded EOF');
});

test('the protocol module exports what the smoke check constructs', async () => {
  // `check:dsh` builds its own frames with the framer from protocol.mjs, so that
  // export is a contract: the assertion is on the symbol the smoke check names,
  // not on "loading the module did or did not throw".
  const { DshBridgeFramer, MAX_FRAME_BYTES } = await import('../src/dsh-bridge/protocol.mjs');
  assert.equal(typeof DshBridgeFramer, 'function');
  assert.equal(typeof MAX_FRAME_BYTES, 'number');
  const smoke = await readFile(new URL('../scripts/check-dsh.mjs', import.meta.url), 'utf8');
  assert.match(smoke, /from '\.\.\/src\/dsh-bridge\/protocol\.mjs'/,
    'the smoke check takes the framer from the protocol module');
  assert.match(smoke, /new DshBridgeFramer\(/, 'and constructs it');
});

// --- v2: model catalogue, selection, settings and credentials ---------------

function modelFixture({ llm, settings, credentials, modelsTimeoutMs, modelInfoTimeoutMs } = {}) {
  const agents = new Map();
  const agent = {
    steered: [], cancels: [], selections: [], status: 'running',
    session: { header: { origin: 'root' } },
    steer(message) { this.steered.push(message); },
    cancel() {},
  };
  agent.model = { select(selection) { agent.selections.push(selection); } };
  agents.set('session', agent);
  const services = { llm, settings, credentials };
  const ctx = { agents, get: (name) => services[name] };
  return {
    agents, agent,
    handlers: createBridgeHandlers({
      ctx, agents, createUserMessage,
      ...(modelsTimeoutMs === undefined ? {} : { modelsTimeoutMs }),
      ...(modelInfoTimeoutMs === undefined ? {} : { modelInfoTimeoutMs }),
    }),
  };
}

test('vika/llm/models isolates one route and reports its configured credential', async () => {
  const llm = {
    listProviders: () => [{ id: 'good', name: 'Good' }, { id: 'bad', name: 'Bad' }],
    listModels: async (provider) => {
      if (provider === 'bad') throw Object.assign(new Error('no credential for bad'), { code: 'MISSING_CREDENTIAL' });
      return [{ id: 'deepseek-chat', name: 'DeepSeek Chat', inputModalities: ['text', 'image'] }];
    },
  };
  const settings = {
    describe: () => [{ ns: 'llm-pi-ai', value: { providers: { good: { apiKeyEnv: 'GOOD_KEY' } } } }],
  };
  const credentials = { describe: async (ref) => ({ configured: ref === 'GOOD_KEY' }) };
  const { handlers } = modelFixture({ llm, settings, credentials });

  const result = await handlers['vika/llm/models']();
  assert.deepEqual(result.providers.map(provider => provider.provider), ['good', 'bad']);
  assert.deepEqual(result.providers[0], {
    provider: 'good',
    name: 'Good',
    models: [{ id: 'deepseek-chat', name: 'DeepSeek Chat', inputModalities: ['text', 'image'] }],
    error: null,
    credential: { ref: 'GOOD_KEY', present: true, known: true },
  });
  // The failing route is reported, not fatal, and carries no credential block.
  assert.equal(result.providers[1].error.code, 'MISSING_CREDENTIAL');
  assert.deepEqual(result.providers[1].models, []);
  assert.equal(result.providers[1].credential, null);
});

test('vika/llm/models distinguishes an unread credential from a missing one', async () => {
  const llm = { listProviders: () => [{ id: 'seeded', name: 'Seeded' }], listModels: async () => [] };
  const settings = {
    describe: () => [{ ns: 'llm-pi-ai', value: { providers: { seeded: { apiKeyEnv: 'SEEDED_KEY' } } } }],
  };
  // credentials is the only service that activates late; the catalogue still
  // answers and says the state is unknown instead of claiming it is absent.
  const unactivated = modelFixture({ llm, settings });
  const [pending] = (await unactivated.handlers['vika/llm/models']()).providers;
  assert.deepEqual(pending.credential, { ref: 'SEEDED_KEY', present: null, known: false });

  const unreadable = { describe: async () => { throw new Error('credential store is unreadable'); } };
  const { handlers } = modelFixture({ llm, settings, credentials: unreadable });
  const [failed] = (await handlers['vika/llm/models']()).providers;
  assert.deepEqual(failed.credential, { ref: 'SEEDED_KEY', present: null, known: false });

  const absent = { describe: async () => ({ configured: false }) };
  const known = modelFixture({ llm, settings, credentials: absent });
  const [configured] = (await known.handlers['vika/llm/models']()).providers;
  assert.deepEqual(configured.credential, { ref: 'SEEDED_KEY', present: false, known: true });
});

test('vika/llm/models keeps the answer when a provider hangs', async () => {
  const llm = {
    listProviders: () => [{ id: 'fast', name: 'Fast' }, { id: 'hung', name: 'Hung' }],
    listModels: (provider) => provider === 'hung'
      ? new Promise(() => {})
      : Promise.resolve([{ id: 'm', name: 'M' }]),
  };
  const { handlers } = modelFixture({ llm, modelsTimeoutMs: 20 });
  const result = await handlers['vika/llm/models']();
  assert.deepEqual(result.providers[0].models, [{ id: 'm', name: 'M', inputModalities: null }]);
  assert.equal(result.providers[1].error.code, 'BRIDGE_TIMEOUT');
});

test('vika/llm/models reports NOT_READY before the llm service activates', async () => {
  const { handlers } = modelFixture({});
  await assert.rejects(
    () => handlers['vika/llm/models'](),
    (error) => error.code === -32002
      && error.data?.code === 'NOT_READY'
      && error.data?.service === 'llm',
  );
});

test('vika/llm/model/info returns DSH model metadata unchanged', async () => {
  const info = {
    provider: 'deepseek', id: 'deepseek-chat', name: 'DeepSeek Chat',
    inputModalities: ['text'], context: { contextWindow: 1000 },
    reasoning: { efforts: [{ id: 'low', name: 'Low' }], defaultEffort: 'low' },
  };
  const llm = { resolveModelInfo: async (provider, model) => ({ ...info, provider, id: model }) };
  const { handlers } = modelFixture({ llm });
  assert.deepEqual(await handlers['vika/llm/model/info']({ provider: 'deepseek', model: 'deepseek-chat' }), info);
  await assert.rejects(
    () => handlers['vika/llm/model/info']({ provider: '', model: 'x' }),
    (error) => error.code === -32602,
  );
});

test('vika/llm/model/info answers a hung adapter with BRIDGE_TIMEOUT instead of waiting', async () => {
  // B9c: the metadata lookup is local in DSH, but the adapter behind it is not.
  // Unbounded, this handler would hold the request until the caller's own timeout
  // and then write a late response onto the wire.
  const llm = { resolveModelInfo: () => new Promise(() => {}) };
  const { handlers } = modelFixture({ llm, modelInfoTimeoutMs: 20 });
  await assert.rejects(
    () => handlers['vika/llm/model/info']({ provider: 'deepseek', model: 'deepseek-chat' }),
    (error) => error.code === 'BRIDGE_TIMEOUT'
      && /resolveModelInfo\(deepseek\/deepseek-chat\) timed out after 20ms/.test(error.message),
  );
});

test('vika/session/model/select records the request, not the resolved default effort', async () => {
  const calls = [];
  const llm = {
    resolveCallConfig: async (selection) => {
      calls.push(selection);
      // resolveCallConfig materializes the adapter default and maxTokens; neither
      // may be recorded as the caller's choice.
      return { ...selection, reasoningEffort: selection.reasoningEffort ?? 'high', maxTokens: 256000 };
    },
  };
  const { handlers, agent } = modelFixture({ llm });
  const selected = await handlers['vika/session/model/select']({ sessionId: 'session', provider: 'deepseek', model: 'deepseek-chat' });
  assert.deepEqual(calls, [{ provider: 'deepseek', model: 'deepseek-chat' }]);
  assert.deepEqual(selected, { provider: 'deepseek', model: 'deepseek-chat', reasoningEffort: null });
  assert.deepEqual(agent.selections, [{ provider: 'deepseek', model: 'deepseek-chat' }]);
});

test('vika/session/model/select records an explicit effort and returns only the request', async () => {
  const calls = [];
  const llm = {
    resolveCallConfig: async (selection) => {
      calls.push(selection);
      return { ...selection, reasoningEffort: 'max', maxTokens: 1 };
    },
  };
  const { handlers, agent } = modelFixture({ llm });
  const selected = await handlers['vika/session/model/select']({
    sessionId: 'session', provider: 'deepseek', model: 'deepseek-chat', reasoningEffort: 'low',
  });
  assert.deepEqual(calls, [{ provider: 'deepseek', model: 'deepseek-chat', reasoningEffort: 'low' }]);
  assert.deepEqual(selected, { provider: 'deepseek', model: 'deepseek-chat', reasoningEffort: 'low' });
  assert.deepEqual(agent.selections, [{ provider: 'deepseek', model: 'deepseek-chat', reasoningEffort: 'low' }]);
  assert.equal('maxTokens' in selected, false);
});

test('vika/session/model/select never selects when validation fails', async () => {
  const llm = {
    resolveCallConfig: async () => {
      throw Object.assign(new Error('does not support reasoning effort "xhigh"'), { code: 'UNSUPPORTED_REASONING_EFFORT' });
    },
  };
  const { handlers, agent } = modelFixture({ llm });
  await assert.rejects(
    () => handlers['vika/session/model/select']({ sessionId: 'session', provider: 'deepseek', model: 'm', reasoningEffort: 'xhigh' }),
    (error) => error.code === 'UNSUPPORTED_REASONING_EFFORT',
  );
  assert.deepEqual(agent.selections, []);
});

test('vika/session/model/select rejects a subagent session', async () => {
  const llm = { resolveCallConfig: async (selection) => selection };
  const { handlers, agents } = modelFixture({ llm });
  agents.get('session').session.header.origin = 'subagent';
  await assert.rejects(
    () => handlers['vika/session/model/select']({ sessionId: 'session', provider: 'p', model: 'm' }),
    (error) => error.code === -32602 && /subagent/.test(error.message),
  );
});

test('vika/session/model/select requires a loaded session id', async () => {
  const { handlers } = modelFixture({ llm: {} });
  await assert.rejects(
    () => handlers['vika/session/model/select']({ provider: 'p', model: 'm' }),
    (error) => error.code === -32602 && /sessionId/.test(error.message),
  );
});

test('vika/settings rejects every namespace but llm-pi-ai', async () => {
  const settings = {
    describe: () => [{ ns: 'llm-pi-ai', revision: 3, value: { providers: {} } }],
    mutate: async () => {},
  };
  const { handlers } = modelFixture({ settings });
  assert.deepEqual(handlers['vika/settings/describe']({ ns: 'llm-pi-ai' }), {
    ns: 'llm-pi-ai', descriptor: { ns: 'llm-pi-ai', revision: 3, value: { providers: {} } },
  });
  await assert.rejects(
    () => handlers['vika/settings/mutate']({ ns: 'llm', ops: [{ op: 'set', path: ['x'], value: 1 }] }),
    (error) => error.code === -32602 && /namespace/.test(error.message),
  );
  await assert.rejects(
    () => handlers['vika/settings/mutate']({ ns: 'llm-pi-ai', ops: [] }),
    (error) => error.code === -32602 && /ops/.test(error.message),
  );
});

test('vika/credentials validates the reference and never echoes the value', async () => {
  const stored = [];
  const credentials = {
    set: async (ref, value) => { stored.push([ref, value]); },
    unset: async () => {},
  };
  const { handlers } = modelFixture({ credentials });
  await assert.rejects(
    () => handlers['vika/credentials/set']({ ref: 'not a ref', value: 'secret-value' }),
    (error) => error.code === -32602 && !error.message.includes('secret-value'),
  );
  await assert.rejects(
    () => handlers['vika/credentials/set']({ ref: 'DEEPSEEK_KEY', value: '' }),
    (error) => error.code === -32602,
  );
  assert.deepEqual(await handlers['vika/credentials/set']({ ref: 'DEEPSEEK_KEY', value: 'k' }), { ok: true });
  assert.deepEqual(stored, [['DEEPSEEK_KEY', 'k']]);
});

// --- v2.1: error classification on the wire --------------------------------

test('bridge keeps DSH string error codes in data.code and translates settings failures', async t => {
  const agents = new Map();
  agents.set('session', { session: { header: { origin: 'root' } }, model: { select() {} } });
  const overridden = 'Configuration for "llm-pi-ai" is overridden by a home patch or command-line overlay';
  const settingsFailures = {
    overridden: new Error(overridden),
    'not-configurable': new Error('Plugin entry "llm-pi-ai" is no longer configurable'),
    conflict: Object.assign(
      new Error('settings namespace "llm-pi-ai" changed since it was read (expected revision 0, now 2)'),
      { code: 'SETTINGS_CONFLICT', expected: 0, actual: 2 },
    ),
    invalid: new Error('invalid config: $.providers.deepseek.api expected "openai-completions" | "openai-responses" | "anthropic-messages"'),
    unrecognized: Object.assign(new Error('settings refused the write'), { code: 'SOME_DSH_CODE', data: { reason: 'kept' } }),
    // A non-object detail cannot be merged with a code, so it is preserved
    // beside it instead of being dropped.
    scalar: Object.assign(new Error('settings refused the write'), { data: 'a bare string' }),
  };
  const services = {
    llm: {
      resolveCallConfig: async () => {
        throw Object.assign(
          new Error('provider "deepseek-official" model "deepseek-flash" does not support reasoning effort "xhigh"'),
          { code: 'UNSUPPORTED_REASONING_EFFORT' },
        );
      },
      resolveModelInfo: async () => { throw new Error(overridden); },
    },
    settings: {
      describe: () => [],
      mutate: async (_ns, ops) => { throw settingsFailures[ops[0].path[0]]; },
    },
  };
  const h = harness(t, { agents, services });
  const cases = [
    ['effort', 'vika/session/model/select', {
      sessionId: 'session', provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'xhigh',
    }],
    ...Object.keys(settingsFailures).map(key => [`settings-${key}`, 'vika/settings/mutate', {
      ops: [{ op: 'set', path: [key], value: 1 }],
    }]),
    ['not-ready', 'vika/credentials/set', { ref: 'DEEPSEEK_KEY', value: 'k' }],
    ['invalid-params', 'vika/session/model/select', { sessionId: 'session', provider: '', model: 'm' }],
    // The settings translation is scoped to settings methods: the same message
    // from another surface stays an unclassified internal error.
    ['scoped', 'vika/llm/model/info', { provider: 'deepseek', model: 'm' }],
  ];
  for (const [id, method, params] of cases) h.input.write(request(method, params, id));
  await waitUntil(() => h.frames().length === cases.length, 'one error frame per failing request');
  const errors = new Map(h.frames().map(frame => [frame.id, frame.error]));
  assert.deepEqual(errors.get('effort'), {
    code: -32603,
    message: 'provider "deepseek-official" model "deepseek-flash" does not support reasoning effort "xhigh"',
    data: { code: 'UNSUPPORTED_REASONING_EFFORT' },
  });
  // The four settings classes are stable codes, never message matching.
  assert.deepEqual(errors.get('settings-overridden').data, { code: 'SETTINGS_OVERRIDDEN' });
  assert.deepEqual(errors.get('settings-not-configurable').data, { code: 'SETTINGS_NOT_CONFIGURABLE' });
  assert.deepEqual(errors.get('settings-conflict').data, { code: 'SETTINGS_CONFLICT', expected: 0, actual: 2 });
  assert.deepEqual(errors.get('settings-invalid').data, { code: 'SETTINGS_INVALID' });
  // A code the bridge does not know is preserved, and error.data survives.
  assert.deepEqual(errors.get('settings-unrecognized').data, { reason: 'kept', code: 'SOME_DSH_CODE' });
  assert.deepEqual(errors.get('settings-scalar').data, { code: 'SETTINGS_INVALID', detail: 'a bare string' });
  // Numeric JSON-RPC codes and their data pass through unchanged.
  assert.deepEqual(errors.get('not-ready'), {
    code: -32002, message: 'credentials is not ready', data: { code: 'NOT_READY', service: 'credentials' },
  });
  assert.deepEqual(errors.get('invalid-params'), {
    code: -32602, message: 'provider and model must be non-empty strings',
  });
  assert.deepEqual(errors.get('scoped'), { code: -32603, message: overridden });
});

test('the fake dsh answers settings failures with the same wire taxonomy as the bridge', async () => {
  // fake-dsh.mjs cannot import the classifier (it is copied out of the tree and
  // run as a stand-alone `dsh`), so it mirrors it. This is the pin: the fake is
  // loaded as source text in a sandbox that stops before its `main()`, and the
  // same failures go through both implementations. Any drift fails here instead
  // of quietly making every fake-driven test agree with itself.
  const source = await readFile(new URL('./fake-dsh.mjs', import.meta.url), 'utf8');
  const body = source.slice(0, source.lastIndexOf('main();'));
  const fake = await import(`data:text/javascript;base64,${Buffer.from(body).toString('base64')}`);
  const failures = [
    // The three message classes the bridge maps, one it must not, a non-settings
    // method (which keeps the string code), and a numeric code.
    [Object.assign(new Error('llm-pi-ai is overridden by a home patch or command-line overlay'), { code: 'X' }), 'vika/settings/mutate'],
    [Object.assign(new Error('no configurable plugin entry for llm-pi-ai'), { code: 'X' }), 'vika/settings/describe'],
    [Object.assign(new Error('the document changed since it was read'), { code: 'X' }), 'vika/settings/mutate'],
    [Object.assign(new Error('something else entirely'), { code: 'SETTINGS_INVALID' }), 'vika/settings/mutate'],
    [Object.assign(new Error('overridden by a home patch'), { code: 'X' }), 'vika/llm/models'],
    [{ code: -32602, message: 'bad params' }, 'vika/settings/mutate'],
  ];
  for (const [error, method] of failures) {
    assert.deepEqual(fake.bridgeErrorFrame(error, method), bridgeErrorFrame(error, method),
      `${method}: ${error.message}`);
  }
});
