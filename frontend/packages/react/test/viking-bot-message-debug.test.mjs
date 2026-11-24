import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const { groupMessages, toRenderItems, createMessageDebugText } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-message-debug.js'))
);
const early = '2026-11-19T14:02:00.000Z';
const late = '2026-11-19T14:03:00.000Z';
const assistant = (id, createdAt, extra = {}) => ({
  id,
  role: 'assistant',
  turnId: 'turn-1',
  createdAt,
  parts: [{ type: 'text', text: `Private body: ${id}` }],
  ...extra,
});
const debug = (message, messages, extra = {}) =>
  JSON.parse(
    createMessageDebugText({
      message,
      messages,
      isStreaming: false,
      ...extra,
    }),
  );

test('merged reply diagnostics use the displayed timestamp source without changing row or action ids', () => {
  for (const turnId of ['turn-1', undefined]) {
    const messages = [
      assistant('tool-step', early, {
        turnId,
        action: { key: 'step-action' },
        parts: [
          {
            type: 'tool',
            id: 'call-1',
            name: 'list',
            status: 'completed',
            output: 'Private tool output',
          },
        ],
      }),
      assistant('reply', late, { turnId, action: { key: 'reply-action' } }),
    ];
    const snapshot = structuredClone(messages);
    const [item] = toRenderItems(messages, undefined, groupMessages);
    assert.equal(item.id, 'tool-step');
    assert.equal(item.message.createdAt, late);
    assert.deepEqual(
      item.message.actions.map(({ messageId }) => messageId),
      ['tool-step', 'reply'],
    );
    const copied = debug(item.message, messages);
    assert.equal(copied.message.message_id, 'reply');
    assert.equal(copied.message.created_at, item.message.createdAt);
    assert.equal(copied.message.turn_id, turnId);
    assert.equal(copied.type, 'vikingbot_message_debug');
    assert.equal(copied.messages, undefined);
    assert.doesNotMatch(JSON.stringify(copied), /Private/);
    assert.deepEqual(messages, snapshot);
  }
});

test('missing segment dates retain the original date and its matching message id', () => {
  for (const [firstDate, lastDate, expected] of [
    [early, undefined, 'step'],
    [undefined, late, 'reply'],
  ]) {
    const messages = [
      assistant('step', firstDate),
      assistant('reply', lastDate),
    ];
    const [grouped] = groupMessages(messages);
    const copied = debug(grouped, messages);
    assert.equal(copied.message.message_id, expected);
    assert.equal(copied.message.created_at, grouped.createdAt);
    const [regrouped] = groupMessages([grouped]);
    assert.deepEqual(debug(regrouped, messages).message, copied.message);
  }
});

test('stream insertion keeps timestamp sources on their own side even with repeated dates and turn ids', () => {
  const messages = [
    assistant('step-a', early),
    assistant('reply-a', late),
    assistant('step-b', early),
    assistant('reply-b', late),
  ];
  const items = toRenderItems(
    messages,
    { parts: [], insertIndex: 2 },
    groupMessages,
  );
  assert.deepEqual(
    items.map(({ id }) => id),
    ['step-a', '__streaming__', 'step-b'],
  );
  for (const [index, expected] of [
    [0, 'reply-a'],
    [2, 'reply-b'],
  ]) {
    const copied = debug(items[index].message, messages, { isStreaming: true });
    assert.equal(copied.message.message_id, expected);
    assert.equal(copied.message.created_at, items[index].message.createdAt);
    assert.equal(copied.session.is_streaming, true);
  }
});

test('user dates still copy all currently loaded original message metadata', () => {
  const user = {
    id: 'user',
    role: 'user',
    createdAt: early,
    parts: [{ type: 'text', text: 'Private question' }],
  };
  const messages = [user, assistant('step', early), assistant('reply', late)];
  const [item] = toRenderItems(messages, undefined, groupMessages);
  const copied = debug(item.message, messages, {
    context: { sessionId: 'session' },
  });
  assert.equal(copied.type, 'vikingbot_loaded_session_debug');
  assert.equal(copied.message.message_id, 'user');
  assert.equal(copied.scope, 'loaded_messages');
  assert.equal(copied.session.session_id, 'session');
  assert.deepEqual(
    copied.messages.map(({ message_id, created_at }) => [
      message_id,
      created_at,
    ]),
    [
      ['user', early],
      ['step', early],
      ['reply', late],
    ],
  );
  assert.doesNotMatch(JSON.stringify(copied), /Private/);
});
