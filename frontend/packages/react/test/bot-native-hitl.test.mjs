import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const load = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
const { nativeChatEvents } = await load('bot-events');
const { createChatAccumulator } = await load('bot-aggregate');
const { pendingGroup } = await load('console-hitl-protocol');

test('native prefixed MCP requests activate HITL and keep unique call identities across turns', async () => {
  const ids = [];
  for (const title of ['First request', 'Second request']) {
    const cards = [{ card_type: 'compile_name', title, payload: {} }];
    async function* frames() {
      for (const [event, data] of [
        [
          'tool_call',
          `mcp_openviking-mcp_request_user_input(${JSON.stringify({ cards })})`,
        ],
        [
          'tool_result',
          { structuredContent: { schema_version: '1.0', cards } },
        ],
        ['response', 'Confirm the settings'],
      ])
        yield { data: JSON.stringify({ event, data }) };
    }
    const accumulator = createChatAccumulator(title);
    for await (const event of nativeChatEvents(frames()))
      accumulator.apply(event);
    const group = pendingGroup([
      { id: title, role: 'assistant', parts: accumulator.finish() },
    ]);
    assert.equal(group.cards[0].request.question, title);
    ids.push(group.id);
  }
  assert.notEqual(ids[0], ids[1]);
});
