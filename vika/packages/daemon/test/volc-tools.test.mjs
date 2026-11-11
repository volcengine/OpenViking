import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { Store } from '../src/store.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { TOOLS, publicTool } from '../src/actions/tools.mjs';
import { MAToolExecutor, maTools, maToolsHash, maInputSchema } from '../src/volc-tools.mjs';
import { MAFiles, outputRelative } from '../src/volc-files.mjs';
import { VolcAgent } from '../src/volc.mjs';
import { InstanceLock, sameProcess } from '../src/lock.mjs';
import { chatEvent } from './event-fixture.mjs';
import { fakeConnectors } from './action-fixture.mjs';
const quiet = { info() {}, warn() {}, error() {}, debug() {} };
const noFiles = { prepareInput: async (input) => input, resultContent: async (result) => [{ type: 'text', text: JSON.stringify(result) }] };
function setup(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ma-core-'));
  const store = new Store(path.join(directory, 'state.sqlite3'), quiet);
  store.initializeCognition('volc', 'token');
  const config = { workspace: path.join(directory, 'workspace'), tempDir: path.join(directory, 'tmp'), maxReplyChars: 1000, channel: 'lark', explicitSilence: true };
  fs.mkdirSync(config.workspace, { mode: 0o700 });
  const connectors = fakeConnectors();
  const actions = new ActionService({ config, store, connectors, logger: quiet });
  const inbound = store.ingest(chatEvent({ messageId: 'om_input12345678', chatId: 'oc_target1234567' })).inbound;
  const turn = { sessionId: 'session-test', epoch: store.getCognitionState().epoch, authorityToken: 'token', fenceId: 'user-current',
    conversationKey: inbound.scopeKey ?? inbound.conversationKey, inboundSeqs: [inbound.seq] };
  const executor = new MAToolExecutor({ actions, store, files: noFiles });
  t.after(() => { store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  const event = (id = 'tool-1') => ({ id, type: 'agent.custom_tool_use', name: 'send_message',
    input: { chat_id: 'oc_target1234567', markdown: 'hello', decision_id: 'message:om_input12345678' } });
  return { directory, config, store, actions, connectors, inbound, turn, executor, event };
}

test('every available Core tool maps without dropping schema fields and round-trips through executor', async (t) => {
  const { store, turn } = setup(t);
  const calls = [];
  const actions = { tools: () => TOOLS.map(publicTool), call: async (name, input, context) => {
    calls.push({ name, input, context }); return { text: name, data: input };
  } };
  const schemas = maTools(actions);
  assert.deepEqual(schemas.map((tool) => tool.name), TOOLS.map((tool) => tool.name));
  for (const [index, tool] of TOOLS.entries()) {
    assert.deepEqual(schemas[index].input_schema, maInputSchema(tool.inputSchema));
    const executor = new MAToolExecutor({ actions, store, files: noFiles });
    executor.see({ id: `tool-${index}`, name: tool.name, input: {} }, turn);
    const result = await executor.execute(`tool-${index}`, turn, () => true);
    assert.equal(result.is_error, false);
  }
  assert.equal(calls.length, TOOLS.length);
  assert.ok(calls.every((call) => call.context.authority === 'token' && call.context.via === 'ma'));
  assert.equal(maToolsHash(schemas), maToolsHash([...schemas].reverse()));
});

test('under the OpenViking gateway, MA leaves the OV read tools to it and keeps the daemon\'s own', () => {
  const actions = { tools: () => TOOLS.map(publicTool) };
  const names = (tools) => tools.map((tool) => tool.name);
  assert.deepEqual(names(maTools(actions, { viaGateway: false })), names(maTools(actions)));
  const gateway = names(maTools(actions, { viaGateway: true }));
  assert.deepEqual(names(maTools(actions)).filter((name) => !gateway.includes(name)),
    ['openviking_find', 'openviking_search', 'openviking_read', 'openviking_list']);
  for (const kept of ['openviking_remember', 'openviking_add_resource', 'read_openviking_session_context']) {
    assert.ok(gateway.includes(kept), kept);
  }
});

test('real Core outbox sends once; DONE result survives restart and POSTED skips execution', async (t) => {
  const { store, turn, executor, event, connectors, directory } = setup(t);
  executor.see(event(), turn);
  const first = await executor.execute('tool-1', turn, () => true);
  assert.equal(first.is_error, false, JSON.stringify(first));
  assert.equal(connectors.calls.length, 1);
  const secondStore = new Store(path.join(directory, 'state.sqlite3'), quiet);
  const recovered = new MAToolExecutor({ actions: { call: () => { throw new Error('must not replay'); } }, store: secondStore, files: noFiles });
  assert.deepEqual(await recovered.execute('tool-1', { ...turn, authorityToken: 'new-token' }, () => true), first);
  secondStore.updateMAToolCall(turn.sessionId, 'tool-1', 'POSTED');
  assert.equal(await recovered.execute('tool-1', turn, () => true), null);
  secondStore.close();
  assert.equal(store.db.prepare('SELECT COUNT(*) n FROM outbox').get().n, 1);
});

test('crashed RUNNING calls for all Core tools fail closed without replaying any side effect', async (t) => {
  const { store, turn } = setup(t);
  let called = 0;
  const executor = new MAToolExecutor({ actions: { call: () => { called++; } }, store, files: noFiles });
  for (const tool of TOOLS) {
    executor.see({ id: tool.name, name: tool.name, input: {} }, turn);
    store.updateMAToolCall(turn.sessionId, tool.name, 'RUNNING');
    const result = await executor.execute(tool.name, turn, () => true);
    assert.match(result.content[0].text, /uncertain_not_retried/);
  }
  assert.equal(called, 0);
});

test('old authority, foreign fences and changed event payload cannot execute', async (t) => {
  const { executor, event, turn, connectors } = setup(t);
  executor.see(event(), turn);
  const result = await executor.execute('tool-1', { ...turn, authorityToken: 'new-token' }, () => true);
  assert.match(result.content[0].text, /stale_authority/);
  assert.equal(connectors.calls.length, 0);
  await assert.rejects(executor.execute('tool-1', { ...turn, fenceId: 'other' }, () => true), /turn fence/);
  assert.throws(() => executor.see({ ...event(), input: { markdown: 'different' } }, turn), /identity changed/);
});

test('MA scope and admitted inbound seq fence Core decisions before a newer input is processed', async (t) => {
  const { actions, store, turn, event, connectors } = setup(t);
  const call = (input, scope = turn) => actions.call('send_message', input, { authority: 'token', via: 'ma', turn: scope });
  await assert.rejects(call(event().input, { ...turn, conversationKey: 'other' }), { code: 'decision_out_of_turn' });
  const newer = store.ingest(chatEvent({ messageId: 'om_newer12345678', chatId: 'oc_target1234567' })).inbound;
  await assert.rejects(call(event().input), { code: 'stale_decision', message: /newer authenticated input exists/ });
  const input = { ...event().input, decision_id: 'message:om_newer12345678' };
  await assert.rejects(call(input), { code: 'decision_out_of_turn' });
  await call(input, { ...turn, inboundSeqs: [newer.seq] });
  assert.equal(connectors.calls.length, 1);
});

test('adapter dispatches one requires_action batch, reconciles missing tool from history, ignores foreign session', async (t) => {
  const { actions, store, config, turn, event, connectors } = setup(t);
  const agent = new VolcAgent(config, store, quiet, { actions, files: noFiles });
  Object.assign(agent, { sessionId: turn.sessionId, turnActive: true, expectedUserEventId: turn.fenceId,
    activeConversationKey: turn.conversationKey, activeItems: [{ seq: turn.inboundSeqs[0] }], connected: true });
  const fence = { id: turn.fenceId, type: 'user.message' };
  const waiting = { id: 'wait', type: 'session.status_idle', stop_reason: { type: 'requires_action', event_ids: ['tool-1'] } };
  const posted = [];
  agent._history = async () => [{ id: 'old', type: 'agent.custom_tool_use', name: 'send_message', input: {} }, fence, event(), waiting];
  agent._postEvents = async (events) => { posted.push(...events); };
  agent._handleEvent(fence);
  agent._handleEvent({ ...event('foreign'), session_id: 'other-session' });
  agent._handleEvent(waiting);
  await agent.customWork;
  assert.equal(posted.length, 1);
  assert.equal(posted[0].is_error, false, JSON.stringify(posted));
  assert.equal(connectors.calls.length, 1);
  assert.equal(store.getMAToolCall(turn.sessionId, 'old'), null);
  assert.equal(store.getMAToolCall(turn.sessionId, 'foreign'), null);
  agent._handleEvent({ ...waiting, id: 'wait-again' });
  await agent.customWork;
  assert.equal(posted.length, 1);
});

test('ambiguous result POST is reconciled from history without another Core action', async (t) => {
  const { actions, store, config, turn, event, connectors } = setup(t);
  const agent = new VolcAgent(config, store, quiet, { actions, files: noFiles, reconnectBaseMs: 1 });
  Object.assign(agent, { sessionId: turn.sessionId, turnActive: true, expectedUserEventId: turn.fenceId,
    activeConversationKey: turn.conversationKey, activeItems: [{ seq: turn.inboundSeqs[0] }] });
  const fence = { id: turn.fenceId, type: 'user.message' };
  let posts = 0;
  agent._postEvents = async () => { posts++; throw new Error('connection lost after acceptance'); };
  agent._history = async () => [fence, event(), { id: 'result-echo', type: 'user.custom_tool_result', custom_tool_use_id: 'tool-1' }];
  agent._handleEvent(fence); agent._handleEvent(event());
  agent._handleEvent({ id: 'waiting', type: 'session.status_idle', stop_reason: { type: 'requires_action', event_ids: ['tool-1'] } });
  await agent.customWork;
  assert.equal(posts, 1); assert.equal(connectors.calls.length, 1);
  assert.equal(store.getMAToolCall(turn.sessionId, 'tool-1').status, 'POSTED');
});

test('schema validation refuses remote mismatch and protects non-Vika agent updates', async (t) => {
  const { actions, store, config } = setup(t);
  const agent = new VolcAgent(config, store, quiet, { actions });
  agent.template = { agent: { id: 'remote', version: 1 } };
  agent._json = async () => ({ name: 'video-knowledge', tools: [], version: 1 });
  await assert.rejects(agent._validateTools(), /schema mismatch/);
  config.volcAgentSync = 'update';
  await assert.rejects(agent._validateTools(), /not provisioned/);
  agent._json = async () => ({ name: 'vika-channel-test', tools: maTools(actions), version: 2 });
  await agent._validateTools(); assert.equal(agent.template.agent.version, 2);
});

test('media bridge preserves incoming files and stages outbound file/card/timer assets with stable paths', async (t) => {
  const { directory, config } = setup(t);
  const source = path.join(directory, 'photo.png'); fs.writeFileSync(source, 'test-image');
  const requests = [];
  let emptyRegistration = true;
  const files = new MAFiles({ config: { ...config, volcBaseUrl: 'https://ark.test', volcApiKey: 'private' },
    json: async (url, options) => {
      requests.push({ url, options });
      if (url.includes('/resources')) return { id: 'resource' };
      if (url === '/files/file-out') return { id: 'file-out', filename: 'report.txt', bytes: 7, download_url: 'https://storage.test/report', scope: { type: 'session', id: 'session' } };
      if (emptyRegistration) { emptyRegistration = false; return null; }
      return { data: [{ id: 'file-out', filename: 'report.txt', bytes: 7, download_url: 'https://storage.test/report' }] };
    }, fetch: async (url, options) => {
      if (String(url).endsWith('/files')) return Response.json({ id: 'file-in' });
      assert.equal(options.headers?.Authorization, undefined); return new Response('payload');
    } });
  const content = await files.resultContent({ text: source, data: { media: [{ path: source }] }, media: [{ path: source }] }, 'session', 'tool');
  assert.doesNotMatch(content[0].text, new RegExp(directory));
  assert.match(content[0].text, /\/mnt\/session\/uploads\//);
  const input = await files.prepareInput({ file_path: 'report.txt', card_images: { img: 'report.txt' },
    delivery: { input: { file_path: 'report.txt' } } }, 'session', 'tool');
  assert.equal(input.file_path, input.card_images.img);
  assert.equal(input.file_path, input.delivery.input.file_path);
  assert.equal(fs.readFileSync(path.join(config.workspace, input.file_path), 'utf8'), 'payload');
  const again = await files.prepareInput({ file_path: 'report.txt' }, 'session', 'tool');
  assert.equal(again.file_path, input.file_path);
  for (const value of ['../private', '/etc/passwd', 'a/../../secret', 'https://evil', 'a\\b']) assert.throws(() => outputRelative(value));
});

test('lock compares boot, process start and PID namespace, and refuses foreign host theft', (t) => {
  const { directory } = setup(t);
  const identity = { bootId: 'boot', startTicks: '10', pidNamespace: 'ns' };
  assert.equal(sameProcess({ identity }, identity), true);
  for (const key of Object.keys(identity)) assert.equal(sameProcess({ identity }, { ...identity, [key]: 'changed' }), false);
  const filename = path.join(directory, 'lock');
  const first = new InstanceLock(filename); first.acquire();
  assert.throws(() => new InstanceLock(filename).acquire(), /another vika/);
  first.release();
  fs.writeFileSync(filename, JSON.stringify({ pid: 1, hostname: 'different-host' }));
  assert.throws(() => new InstanceLock(filename).acquire(), /another host/);
});

test('uncertainty blocks send-to-reply and timer bypasses while leaving reads available', async (t) => {
  const { store, turn, executor, event, connectors } = setup(t);
  executor.see(event(), turn); store.updateMAToolCall(turn.sessionId, 'tool-1', 'RUNNING');
  await executor.execute('tool-1', turn, () => true);
  assert.equal(store.getMAToolCall(turn.sessionId, 'tool-1').uncertain, 1);
  for (const name of ['reply_message', 'notify_owner', 'finish_timer_occurrence', 'create_timer', 'read_meeting_inbox']) {
    executor.see({ id: name, name, input: {} }, turn);
    const result = await executor.execute(name, turn, () => true);
    assert.match(result.content[0].text, /uncertain_not_retried/);
  }
  let reads = 0;
  const reading = new MAToolExecutor({ store, files: noFiles, actions: { call: async () => { reads++; return { text: 'read' }; } } });
  reading.see({ id: 'read', name: 'list_chat_messages', input: {} }, turn);
  assert.equal((await reading.execute('read', turn, () => true)).is_error, false);
  assert.equal(reads, 1); assert.equal(connectors.calls.length, 0);
});

test('completed Core side effect remains successful when output rendering fails', async (t) => {
  const { actions, store, turn, event, connectors } = setup(t);
  const executor = new MAToolExecutor({ actions, store, files: { ...noFiles, resultContent: async () => { throw new Error('bridge failed'); } } });
  executor.see(event(), turn);
  const result = await executor.execute('tool-1', turn, () => true);
  assert.equal(result.is_error, false); assert.match(result.content[0].text, /"completed":true/);
  assert.equal(connectors.calls.length, 1);
  assert.equal(store.getMAToolCall(turn.sessionId, 'tool-1').outbox_id, '1');
  assert.deepEqual(await executor.execute('tool-1', turn, () => true), result);
});

test('same-scope running steer persists acceptance and enables decisions only after processed echo', async (t) => {
  const { store, config, actions, inbound, turn } = setup(t);
  const agent = new VolcAgent(config, store, quiet, { actions, files: noFiles });
  Object.assign(agent, { sessionId: turn.sessionId, connected: true, turnActive: true,
    expectedUserEventId: turn.fenceId, turnFenceSeen: true,
    activeConversationKey: turn.conversationKey, activeItems: [{ text: 'old', seq: inbound.seq }] });
  store.setGlobalSession(turn.sessionId, 'test-policy', { runtime: 'volc', epoch: turn.epoch });
  store.beginManagedTurn({ runtime: 'volc', authorityToken: 'token', sessionId: turn.sessionId,
    epoch: turn.epoch, inboundSeqs: [inbound.seq], conversationKey: turn.conversationKey });
  store.acceptManagedTurn('volc', turn.sessionId, turn.fenceId);
  const newer = store.ingest(chatEvent({ messageId: 'om_steering123456', chatId: inbound.chatId })).inbound;
  let accept;
  agent._postEvents = () => new Promise((resolve) => { accept = resolve; });
  agent._history = async () => [];
  agent._persistSession = () => {};
  agent._bodyFor = (items) => ({ body: items.map((item) => item.text).join('\n'), seqs: items.map((item) => item.seq), deliveredThroughSeq: 0 });
  assert.equal(agent.deliver('new input', newer.seq), true);
  assert.equal(store.getManagedTurn('volc').steers[0].status, 'POSTING');
  assert.deepEqual(agent._customTurn().inboundSeqs, [inbound.seq]);
  // An old end_turn while the POST is in flight must not release the turn.
  agent._handleEvent({ id: 'old-idle', type: 'session.status_idle', stop_reason: { type: 'end_turn' } });
  accept({ data: [{ id: 'user-steer', type: 'user.message', processed_at: null }] });
  await agent.steerWork;
  assert.equal(agent.turnActive, true);
  assert.equal(store.getManagedTurn('volc').steers[0].expectedUserEventId, 'user-steer');
  agent._handleEvent({ id: 'user-steer', type: 'user.message', processed_at: null });
  assert.deepEqual(agent._customTurn().inboundSeqs, [inbound.seq]);
  agent._history = async () => [
    { id: turn.fenceId, type: 'user.message' },
    { id: 'user-steer', type: 'user.message', processed_at: '2026-11-08T00:00:00Z' },
    { id: 'new-tool', type: 'agent.custom_tool_use', name: 'send_message', input: { decision_id: newer.eventId } },
  ];
  let executedSeqs;
  agent.customExecutor.execute = async (_id, scope) => { executedSeqs = scope.inboundSeqs; return null; };
  agent._queueCustomTools(['new-tool']);
  await agent.customWork;
  assert.deepEqual(executedSeqs, [inbound.seq, newer.seq], 'history catch-up must refresh the admitted steer seqs before dispatch');
  assert.deepEqual(agent._customTurn().inboundSeqs, [inbound.seq, newer.seq]);
  const foreign = store.ingest(chatEvent({ messageId: 'om_foreign123456', chatId: 'oc_different12345' })).inbound;
  assert.equal(agent.deliver('foreign scope', foreign.seq), true);
  assert.equal(agent.queue.length, 1);
  assert.equal(store.getManagedTurn('volc').steers.length, 1);
});

test('restart quarantines uncertain steer POST and resumes accepted steer with original root fence', (t) => {
  const { store, config, actions, inbound, turn } = setup(t);
  store.setGlobalSession(turn.sessionId, 'test-policy', { runtime: 'volc', epoch: turn.epoch });
  store.beginManagedTurn({ runtime: 'volc', authorityToken: 'token', sessionId: turn.sessionId,
    epoch: turn.epoch, inboundSeqs: [inbound.seq], conversationKey: turn.conversationKey });
  store.acceptManagedTurn('volc', turn.sessionId, turn.fenceId);
  const newer = store.ingest(chatEvent({ messageId: 'om_restart123456', chatId: inbound.chatId })).inbound;
  store.beginManagedSteer('volc', { sessionId: turn.sessionId, authorityToken: 'token', id: 'steer',
    inboundSeqs: [newer.seq], conversationKey: turn.conversationKey, deliveredThroughSeq: 0 });
  const durable = store.getManagedTurn('volc');
  const agent = new VolcAgent(config, store, quiet, { actions, files: noFiles });
  agent.sessionId = turn.sessionId; agent._failItems = () => {};
  agent._recoverManagedTurn();
  assert.equal(agent.sessionPoisoned, true);
  assert.equal(agent.turnActive, false);
  store.setMetaStatement.run('managed_turn:volc', JSON.stringify(durable));
  store.acceptManagedSteer('volc', turn.sessionId, 'steer', 'user-steer');
  agent.sessionPoisoned = false; agent._runTurn = async () => {};
  agent._recoverManagedTurn();
  assert.equal(agent.expectedUserEventId, turn.fenceId);
  assert.equal(agent.turnActive, true);
  assert.deepEqual(agent._customTurn().inboundSeqs, [inbound.seq]);
  agent._handleEvent({ id: turn.fenceId, type: 'user.message' });
  agent._handleEvent({ id: 'user-steer', type: 'user.message', processed_at: 'now' });
  assert.deepEqual(agent._customTurn().inboundSeqs, [inbound.seq, newer.seq]);
});

test('ambiguous steer HTTP failure quarantines the session and settles both admitted inputs without replay', async (t) => {
  const { store, config, actions, inbound, turn } = setup(t);
  const newer = store.ingest(chatEvent({ messageId: 'om_failure123456', chatId: inbound.chatId })).inbound;
  store.setGlobalSession(turn.sessionId, 'test-policy', { runtime: 'volc', epoch: turn.epoch });
  store.beginManagedTurn({ runtime: 'volc', authorityToken: 'token', sessionId: turn.sessionId,
    epoch: turn.epoch, inboundSeqs: [inbound.seq], conversationKey: turn.conversationKey });
  store.acceptManagedTurn('volc', turn.sessionId, turn.fenceId);
  let failed;
  const agent = new VolcAgent(config, store, quiet, { actions, files: noFiles, onTurnFailed: (event) => { failed = event; } });
  Object.assign(agent, { sessionId: turn.sessionId, connected: true, turnActive: true,
    expectedUserEventId: turn.fenceId, turnFenceSeen: true,
    activeConversationKey: turn.conversationKey, activeItems: [{ text: 'old', seq: inbound.seq }] });
  agent._bodyFor = () => ({ body: 'new', seqs: [newer.seq], deliveredThroughSeq: 0 });
  agent._ensureFreshSession = async () => {};
  let posts = 0;
  agent._postEvents = async () => { posts++; throw new Error('response lost after send'); };
  assert.equal(agent.deliver('new', newer.seq), true);
  await agent.steerWork;
  assert.equal(posts, 1);
  assert.equal(agent.sessionPoisoned, true);
  assert.equal(agent.turnActive, false);
  assert.deepEqual(failed.inboundSeqs, [inbound.seq, newer.seq]);
  assert.equal(store.getManagedTurn('volc'), null);
});

test('download preparation resumes under recovered authority before Core starts, but RUNNING does not', async (t) => {
  const { store, turn, event, executor } = setup(t);
  executor.see(event(), turn); store.prepareMAToolCall(turn.sessionId, 'tool-1');
  let prepared = 0; let called = 0;
  const recovered = new MAToolExecutor({ store, files: { ...noFiles, prepareInput: async (input) => { prepared++; return input; } },
    actions: { call: async (_name, _input, context) => {
      called++; assert.equal(context.authority, 'replacement-token');
      assert.equal(store.getMAToolCall(turn.sessionId, 'tool-1').status, 'RUNNING');
      return { text: 'done' };
    } } });
  const result = await recovered.execute('tool-1', { ...turn, authorityToken: 'replacement-token' }, () => true);
  assert.equal(result.is_error, false); assert.equal(prepared, 1); assert.equal(called, 1);
  assert.equal(store.getMAToolCall(turn.sessionId, 'tool-1').status, 'DONE');
  await recovered.execute('tool-1', { ...turn, authorityToken: 'replacement-token' }, () => true);
  assert.equal(called, 1);
});

test('preparation migration preserves an existing tool journal and defaults old calls to non-resumable', (t) => {
  const { store, directory, executor, event, turn } = setup(t);
  executor.see(event(), turn);
  store.db.exec('ALTER TABLE ma_tool_calls DROP COLUMN preparing');
  const migrated = new Store(path.join(directory, 'state.sqlite3'), quiet);
  try {
    const row = migrated.getMAToolCall(turn.sessionId, 'tool-1');
    assert.equal(row.status, 'SEEN'); assert.equal(row.preparing, 0);
    assert.equal(row.authority_token, turn.authorityToken);
  } finally { migrated.close(); }
});
