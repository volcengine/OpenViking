// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Harness } from '../src/harness.mjs';
import { ovAttribution } from '../src/ov-session-context.mjs';
import { peerForEvent } from '../src/ov-peer.mjs';
import { Store } from '../src/store.mjs';
import { chatEvent, mailEvent, subjectEvent } from './event-fixture.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
// What OV accepts as a peer id (openviking/core/identifiers.py).
const OV_PEER_ID = /^(?!\.{1,2}$)[A-Za-z0-9_.-]*@?[A-Za-z0-9_.-]*$/;

test('a peer is named after the channel identity of whoever sent the event', () => {
  assert.equal(peerForEvent(chatEvent({ senderId: 'ou_abc' })), 'lark-ou_abc');
  // Another bot is a peer like a person.
  assert.equal(peerForEvent(chatEvent({ senderId: 'cli_a942', senderType: 'app' })), 'lark-cli_a942');
  // A Slack user id is unique within its workspace only.
  assert.equal(peerForEvent(chatEvent({ source: 'slack', senderId: 'U024BE7LH', tenantKey: 'T061EG9R6' })), 'slack-T061EG9R6-U024BE7LH');
  assert.equal(peerForEvent(chatEvent({ source: 'weixin', senderId: 'o9cq80x@im.wechat' })), 'weixin-o9cq80x@im.wechat');
  // A mail sender by address, in one spelling.
  assert.equal(peerForEvent(mailEvent({ from: 'Alice@Example.com' })), 'email-alice@example.com');
  assert.equal(peerForEvent(subjectEvent()), null, 'no sender, no peer');
});

test('an identity OV would not take as a path segment still names one peer of its own', () => {
  const odd = peerForEvent(chatEvent({ source: 'weixin', senderId: 'a@b@c/../d' }));
  const similar = peerForEvent(chatEvent({ source: 'weixin', senderId: 'a@b_c/../d' }));
  assert.match(odd, OV_PEER_ID);
  assert.notEqual(odd, similar, 'cleaning up two ids never makes them one');
  const long = peerForEvent(chatEvent({ senderId: 'x'.repeat(400) }));
  assert.ok(long.length <= 255);
  assert.match(long, OV_PEER_ID);
});

test('the owner speaks as the OV user; anyone else is recorded as their peer', async (t) => {
  const store = new Store(':memory:', logger);
  store.initializeCognition('codex', 'owner');
  const harness = new Harness({
    config: { maxEventBytes: 100_000, maxEventAgeMs: 60_000, ignoredSenderIds: new Set() }, store, logger, recordActivity() {},
    runtime: { snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }), submit: () => ({ accepted: true }) },
    roster: { warm: async () => {} },
    isOwner: (event) => event.senderId === 'ou_owner',
    mentionTarget: () => 'me',
  });
  t.after(() => { harness.stop(); store.close(); });
  harness.handleConnectorEvent(chatEvent({ senderId: 'ou_owner', messageId: 'om-1', chatType: 'group' }).envelope);
  harness.handleConnectorEvent(chatEvent({ senderId: 'ou_zhiheng', messageId: 'om-2', chatType: 'group' }).envelope);
  await harness.ingressBarrier;
  const lines = store.cognitionJournal().filter(({ kind }) => kind === 'inbound');
  assert.deepEqual(lines.map(({ metadata }) => metadata.peer), [null, 'lark-ou_zhiheng']);
  assert.deepEqual(lines.map(ovAttribution), [{ role: 'user' }, { role: 'user', peer_id: 'lark-ou_zhiheng' }]);
});

test('what the agent and the daemon wrote is never the owner speaking', () => {
  const event = (actor) => ({ envelope: { actor } });
  assert.deepEqual(ovAttribution({ kind: 'outbound', metadata: {} }), { role: 'assistant' });
  assert.deepEqual(ovAttribution({ kind: 'control', actor: 'ou_owner', metadata: {} }), { role: 'assistant' });
  // A reminder the agent set has no sender.
  assert.deepEqual(ovAttribution({ kind: 'inbound', metadata: { event: event(null).envelope } }), { role: 'assistant' });
  // A line recorded before peers were is left as it was.
  assert.deepEqual(ovAttribution({ kind: 'inbound', metadata: { event: event({ id: 'ou_x', type: 'user' }).envelope } }), { role: 'user' });
});
