import assert from 'node:assert/strict';
import { test } from 'node:test';
import { sdkEventEnvelope } from '../src/lark-sdk-events.mjs';
import { normalizeLarkEvent } from '../src/connectors/lark-events.mjs';
import { SUBSCRIBED_EVENT_TYPES } from '../src/lark.mjs';

test('SDK message envelope retains authenticated identity and strips provider verification token', () => {
  const raw = { schema: '2.0', event_id: 'event1', create_time: String(Date.now()), token: 'never-forward',
    app_id: 'app', tenant_key: 'tenant', message: { message_id: 'om_message12345678', chat_id: 'oc_chat12345678',
      chat_type: 'p2p', message_type: 'text', content: JSON.stringify({ text: 'hello' }), create_time: String(Date.now()) },
    sender: { sender_id: { open_id: 'ou_sender12345678' }, sender_type: 'user', tenant_key: 'tenant' } };
  const envelope = sdkEventEnvelope('im.message.receive_v1', raw);
  assert.doesNotMatch(JSON.stringify(envelope), /never-forward/);
  const event = normalizeLarkEvent(envelope);
  assert.equal(event.id, 'event1');
  assert.equal(event.content.text, 'hello');
  assert.equal(sdkEventEnvelope('im.message.receive_v1', raw, new Set(['other'])), null);
  assert.ok(sdkEventEnvelope('im.message.receive_v1', raw, new Set(['oc_chat12345678'])));
});

test('SDK retains card callback and every existing document/meeting event type', () => {
  const raw = { event_id: 'card-event', create_time: String(Date.now()), operator: { open_id: 'ou_sender12345678' },
    context: { open_message_id: 'om_card12345678', open_chat_id: 'oc_chat12345678' }, action: { tag: 'button', value: { key: 'a' } } };
  const event = normalizeLarkEvent(sdkEventEnvelope('card.action.trigger', raw));
  assert.equal(event.kind, 'message.action');
  assert.equal(event.data.interaction.message_id, 'om_card12345678');
  assert.ok(SUBSCRIBED_EVENT_TYPES.includes('card.action.trigger'));
  assert.ok(SUBSCRIBED_EVENT_TYPES.some((type) => type.startsWith('drive.')));
  assert.ok(SUBSCRIBED_EVENT_TYPES.some((type) => type.startsWith('vc.')));
  for (const type of SUBSCRIBED_EVENT_TYPES) assert.equal(sdkEventEnvelope(type, { event_id: 'event' }).header.event_type, type);
});
