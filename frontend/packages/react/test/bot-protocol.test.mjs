import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const load = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
const { createChatAccumulator } = await load('bot-aggregate');
const { nativeChatEvents } = await load('bot-events');
const collect = async (source) => {
  const values = [];
  for await (const value of source) values.push(value);
  return values;
};
async function* frames(events) {
  for (const [event, data] of events)
    yield { data: JSON.stringify({ event, data }) };
}

test('native reasoning snapshot does not duplicate deltas; whitespace and tool order survive', async () => {
  const events = [
    ...(await collect(
      nativeChatEvents(
        frames([
          ['iteration', '1'],
          ['reasoning_delta', 'check'],
          ['reasoning_delta', ' '],
          ['reasoning', 'check '],
          ['tool_call', 'read({"uri":"demo"})'],
          ['tool_call', 'read({"uri":"demo"})'],
          ['tool_call', 'read({"uri":"other"})'],
          ['tool_result', 'found'],
          ['tool_result', 'same again'],
          ['tool_result', 'other result'],
          ['iteration', '2'],
          ['reasoning_delta', 'done'],
          ['content_delta', 'ans'],
          ['content_delta', 'wer'],
          ['response', 'answer'],
        ]),
      ),
    )),
  ];
  const accumulator = createChatAccumulator('turn');
  events.forEach(accumulator.apply);
  const parts = accumulator.finish();
  assert.deepEqual(
    parts.map((part) => part.type),
    ['reasoning', 'tool', 'tool', 'tool', 'reasoning', 'text'],
  );
  assert.equal(parts[0].text, 'check ');
  assert.equal(parts[1].output, 'found');
  assert.equal(parts[1].status, 'completed');
  assert.deepEqual(
    parts.slice(1, 4).map((p) => p.output),
    ['found', 'same again', 'other result'],
  );
  assert.equal(new Set(parts.slice(1, 4).map((p) => p.id)).size, 3);
  assert.equal(parts[4].groupId, parts[0].groupId);
  assert.equal(parts[5].text, 'answer');
});

test('commercial adapters can replace reasoning by stable event ID and update paired tools', () => {
  const acc = createChatAccumulator('turn');
  acc.apply({ type: 'reasoning', id: 'r1', text: 'old', delta: true });
  acc.apply({
    type: 'tool',
    part: { type: 'tool', id: 't1', name: 'read', status: 'running' },
  });
  acc.apply({ type: 'reasoning', id: 'r1', text: 'snapshot', delta: false });
  acc.apply({ type: 'tool-result', id: 't1', output: 'failed', isError: true });
  acc.apply({ type: 'reasoning', id: 'r2', text: 'new', delta: false });
  acc.apply({ type: 'text', text: 'explanation', delta: true });
  acc.apply({ type: 'reasoning', id: 'r3', text: 'next block', delta: true });
  const parts = acc.finish();
  assert.equal(parts[0].text, 'snapshot');
  assert.equal(parts[1].status, 'error');
  assert.notEqual(parts[4].groupId, parts[0].groupId);
});

test('native response errors reject instead of producing a completed answer', async () => {
  await assert.rejects(
    collect(nativeChatEvents(frames([['response', { error: 'timeout' }]]))),
    /timeout/,
  );
});
