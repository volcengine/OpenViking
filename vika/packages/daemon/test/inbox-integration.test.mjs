import assert from 'node:assert/strict';
import { it } from 'node:test';
import { Harness } from '../src/harness.mjs';
import { Store } from '../src/store.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { AgentRuntime } from '../src/agent-runtime.mjs';
import { OVSessionContext } from '../src/ov-session-context.mjs';
import { chatEvent, subjectEvent } from './event-fixture.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { registerNameResolver } from '../src/roster.mjs';
import { FailurePolicy } from '../src/notices.mjs';
import { DecisionPolicy } from '../src/decisions.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

function setup(t, scheduler = 'inbox', overrides = {}) {
  const config = { scheduler, connectors: ['lark'], maxEventBytes: 100000,
    maxEventAgeMs: 60000, ignoredSenderIds: new Set(), ...overrides };
  const store = new Store(':memory:', logger);
  store.initializeCognition('codex', 'owner');
  const submitted = [];
  const consumed = [];
  const runtime = {
    runtime: 'codex',
    snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }),
    submit: (items) => { submitted.push(...items); return { accepted: true }; },
    consumeInbox: (items) => consumed.push(...items),
  };
  const harness = new Harness({ config, store, logger, runtime, recordActivity() {},
    onSlashCommand: () => submitted.push({ text: 'local command' }) });
  const service = new ActionService({ config, store, logger, connectors: fakeConnectors(),
    inbox: harness.inbox, onInboxRead: (items) => runtime.consumeInbox(items) });
  t.after(() => { harness.stop(); store.close(); });
  return { config, store, runtime, submitted, consumed, harness, service };
}

it('persists and deduplicates ingress but provides bodies only through the authority-fenced Core tool', async (t) => {
  const { store, submitted, consumed, harness, service } = setup(t);
  const event = chatEvent({ content: 'private unread request' });
  harness.handleConnectorEvent(event.envelope);
  harness.handleConnectorEvent(event.envelope);
  await harness.ingressBarrier;
  assert.equal(store.stats().inbound.DONE, 1);
  assert.equal(store.cognitionJournal().length, 0);
  assert.equal(harness.inbox.pending, 1);
  assert.equal(submitted.length, 1);
  assert.doesNotMatch(submitted[0].text, /private unread request/);
  assert.equal(submitted[0].seq, null);
  assert.ok(service.tools().some((tool) => tool.name === 'message_check'));
  await assert.rejects(service.call('message_check', {}, { authority: 'retired' }), /no longer owns/);
  assert.equal(harness.inbox.pending, 1);
  const result = await service.call('message_check', {}, { authority: 'owner' });
  assert.match(result.text, /private unread request/);
  assert.match(result.text, /event=message:om-test/);
  assert.match(result.text, /msg=om-test/);
  assert.equal(result.data.pending, 0);
  assert.equal(consumed.length, 1);
  assert.equal(store.cognitionJournal().length, 1);
  assert.equal(harness.inbox.pending, 0);
  assert.equal((await service.call('message_check', {}, { authority: 'owner' })).data.items.length, 0);
});

it('keeps legacy delivery and does not offer message_check outside inbox mode', async (t) => {
  const { harness, service, submitted, store } = setup(t, 'legacy');
  harness.handleConnectorEvent(chatEvent({ content: 'legacy body' }).envelope);
  await harness.ingressBarrier;
  assert.match(submitted[0].text, /legacy body/);
  assert.equal(store.cognitionJournal().length, 1);
  assert.equal(service.tools().some((tool) => tool.name === 'message_check'), false);
  await assert.rejects(service.call('message_check', {}, { authority: 'owner' }), /unknown tool/);
});

it('leaves local commands and dedicated meeting handling outside inbox admission', async (t) => {
  const { harness, submitted } = setup(t);
  harness.handleConnectorEvent(chatEvent({ messageId: 'slash', content: '/help' }).envelope);
  const handled = [];
  harness.eventHandler = (event) => event.kind.startsWith('meeting.') ? () => handled.push(event.eventId) : null;
  harness.handleConnectorEvent(subjectEvent({ source: 'lark', kind: 'meeting.started', eventId: 'meeting-1' }).envelope);
  await harness.ingressBarrier;
  assert.deepEqual(handled, ['meeting-1']);
  assert.deepEqual(submitted.map((entry) => entry.text), ['local command']);
  assert.equal(harness.inbox.pending, 0);
});

it('supports scope reads and rejects invalid limits before removing messages', async (t) => {
  const { harness, service } = setup(t);
  for (const [messageId, chatId] of [['first', 'A'], ['second', 'B'], ['third', 'A']]) {
    harness.handleConnectorEvent(chatEvent({ messageId, chatId }).envelope);
  }
  await harness.ingressBarrier;
  await assert.rejects(service.call('message_check', { limit: 0 }, { authority: 'owner' }), /limit/);
  assert.equal(harness.inbox.pending, 3);
  const scope = chatEvent({ chatId: 'A' }).scopeKey;
  const first = await service.call('message_check', { scope_key: scope, limit: 1 }, { authority: 'owner' });
  assert.deepEqual(first.data.items.map((entry) => entry.event_id), ['message:first']);
  const rest = await service.call('message_check', {}, { authority: 'owner' });
  assert.deepEqual(rest.data.items.map((entry) => entry.event_id), ['message:second', 'message:third']);
});

it('never synchronizes unread bodies into OpenViking context and drops them at shutdown', async (t) => {
  const { harness, store, service } = setup(t);
  const messages = [];
  const ov = new OVSessionContext(store, {
    create: async (sessionId) => ({ session_id: sessionId, uri: `viking://session/${sessionId}` }),
    append: async (_sessionId, message) => messages.push({ ...message, parts: [{ type: 'text', text: message.content }] }),
    context: async () => ({ messages }),
  }, logger);
  t.after(() => ov.stop());
  harness.handleConnectorEvent(chatEvent({ content: 'not yet read' }).envelope);
  await harness.ingressBarrier;
  assert.equal((await store.prepareOVContext(1)).messages.length, 0);
  await service.call('message_check', {}, { authority: 'owner' });
  assert.match((await store.prepareOVContext(1)).messages[0].content, /not yet read/);
  harness.handleConnectorEvent(chatEvent({ messageId: 'leftover', content: 'lost on stop' }).envelope);
  await harness.ingressBarrier;
  harness.stop();
  assert.equal(harness.inbox.pending, 0);
  assert.doesNotMatch(JSON.stringify(await store.prepareOVContext(1)), /lost on stop/);
});

it('rejects Volc at startup and on switching when inbox requires Core tools', async () => {
  const config = { scheduler: 'inbox', connectors: ['lark'], initialModel: 'ark' };
  const store = new Store(':memory:', logger);
  try {
    const runtime = new AgentRuntime(config, store, logger);
    await assert.rejects(runtime.start(), /inbox.*Volc|Volc.*inbox/);
    config.initialModel = 'codex';
    await assert.rejects(runtime.transition({ kind: 'select-runtime', runtime: 'volc' }), /inbox.*Volc|Volc.*inbox/);
  } finally { store.close(); }
});

it('associates pulled inputs with the finishing turn without resubmitting their bodies', () => {
  const ended = [];
  const runtime = new AgentRuntime({}, {}, logger, { onTurnSucceeded: (info) => ended.push(info) });
  runtime.consumeInbox([{ seq: 5 }, { seq: 6 }]);
  runtime._turnEnded('succeeded', { inboundSeqs: [2] });
  runtime._turnEnded('succeeded', { inboundSeqs: [] });
  assert.deepEqual(ended.map((info) => info.inboundSeqs), [[2, 5, 6], []]);
});

it('does not automatically replay consumed inbox messages on model failure', async (t) => {
  const { config, store, runtime } = setup(t);
  store.initializeCognition('claude', 'owner');
  const row = store.ingest(chatEvent()).inbound;
  let transitions = 0;
  runtime.transition = async () => { transitions += 1; return { ok: false }; };
  const policy = new FailurePolicy({ config, store, runtime, logger, recordActivity() {} });
  policy.lastFallbackAtMs = -Infinity;
  assert.equal(await policy.tryModelFallback({ inboundSeqs: [row.seq], error: 'model failed' }), false);
  assert.equal(transitions, 0);
});

it('does not put pulled but undecided work back into the inbox after turn end', async (t) => {
  const { harness, store } = setup(t);
  const row = store.ingest(chatEvent()).inbound;
  const policy = new DecisionPolicy({ store, harness, logger, recordActivity() {}, explicitSilence: true });
  let redeliveries = 0;
  harness.redeliverUndecided = () => { redeliveries += 1; };
  policy.onTurnSucceeded({ inboundSeqs: [row.seq] });
  policy.onTurnStopped({ inboundSeqs: [row.seq] });
  await harness.ingressBarrier;
  assert.equal(harness.inbox.pending, 0);
  assert.equal(redeliveries, 0);
});

it('blocks a reply based on an older pulled event until the newer same-scope input is read', async (t) => {
  const { harness, service } = setup(t);
  harness.handleConnectorEvent(chatEvent({ messageId: 'om_old12345678', chatId: 'oc_chat12345678' }).envelope);
  await harness.ingressBarrier;
  await service.call('message_check', {}, { authority: 'owner' });
  harness.handleConnectorEvent(chatEvent({ messageId: 'om_new12345678', chatId: 'oc_chat12345678' }).envelope);
  await harness.ingressBarrier;
  await assert.rejects(service.call('reply_message', {
    decision_id: 'message:om_old12345678', message_id: 'om_old12345678', markdown: 'old answer',
  }, { authority: 'owner' }), /newer authenticated input/);
  assert.equal(harness.inbox.pending, 1);
  const read = await service.call('message_check', {}, { authority: 'owner' });
  assert.deepEqual(read.data.items.map((item) => item.event_id), ['message:om_new12345678']);
});

it('refuses to reply on a peeked event but lets silence settle on the peek', async (t) => {
  const { harness, store, consumed, service } = setup(t, 'inbox', { explicitSilence: true });
  harness.handleConnectorEvent(chatEvent({ messageId: 'om_peek12345678', content: 'peeked body' }).envelope);
  await harness.ingressBarrier;
  const peek = await service.call('message_check', { peek: true }, { authority: 'owner' });
  assert.match(peek.text, /peek=1 pending=1/);
  assert.match(peek.text, /peeked body/);
  assert.equal(consumed.length, 0);
  assert.equal(store.cognitionJournal().length, 0);
  assert.equal(harness.inbox.pending, 1);
  const decide = { decision_id: 'message:om_peek12345678', message_id: 'om_peek12345678', markdown: 'answer' };
  await assert.rejects(service.call('reply_message', decide, { authority: 'owner' }), /only announced or peeked/);
  assert.equal(harness.inbox.pending, 1);
  assert.equal((await service.call('message_check', { peek: true }, { authority: 'owner' })).data.items.length, 0);
  const read = await service.call('message_check', {}, { authority: 'owner' });
  assert.match(read.text, /read=1 pending=0/);
  assert.match(read.text, /peeked body/);
  assert.equal(consumed.length, 1);
  assert.equal(store.cognitionJournal().length, 1);
  await service.call('reply_message', decide, { authority: 'owner' });
});

it('records silence on a peeked input and drops it from the backlog', async (t) => {
  const { harness, service } = setup(t, 'inbox', { explicitSilence: true });
  harness.handleConnectorEvent(chatEvent({ messageId: 'om_sil1234567890', content: 'chatter between others' }).envelope);
  await harness.ingressBarrier;
  const input = { decision_id: 'message:om_sil1234567890' };
  await assert.rejects(service.call('stay_silent', input, { authority: 'owner' }), /never read or peeked/);
  assert.equal(harness.inbox.pending, 1);
  await service.call('message_check', { peek: true }, { authority: 'owner' });
  const silent = await service.call('stay_silent', input, { authority: 'owner' });
  assert.match(silent.text, /Recorded/);
  assert.equal(harness.inbox.pending, 0);
});

it('keeps the full resolved sender label in notification metadata', async (t) => {
  const { harness, submitted } = setup(t);
  registerNameResolver(() => '张三');
  t.after(() => registerNameResolver(null));
  harness.handleConnectorEvent(chatEvent().envelope);
  await harness.ingressBarrier;
  const detail = JSON.parse(submitted[0].text.split('\n')[1]);
  assert.equal(detail.latest_from, '张三');
});
