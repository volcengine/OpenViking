import assert from 'node:assert/strict';
import test from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';
import { act, type } from './helpers/command-console-dom.mjs';

const readyActivation = () => ({
  async isEligible() {
    return true;
  },
  async getStatus() {
    return 'ready';
  },
  async start() {
    assert.fail('A ready Bot must not be started again');
  },
});
const settle = () =>
  act(async () => new Promise((resolve) => setTimeout(resolve, 0)));

for (const replacement of ['activation adapter', 'chat scope key']) {
  test(`replacing the ${replacement} clears chat text and preserves terminal state`, async () => {
    const first = readyActivation();
    const changed = [];
    const props = {
      chatOptions: { activation: first, scopeKey: 'chat-a' },
      conversation: {
        messages: [],
        composer: false,
        quickInputs: [
          { id: 'a', label: 'First draft', prompt: 'Private draft from A' },
          { id: 'b', label: 'Second draft', prompt: 'New draft from B' },
        ],
        onDraftChange: (value) => changed.push(value),
      },
    };
    const host = await mount(async () => ok({}), props);
    try {
      await settle();
      await host.execute('ov help');
      await type(host.input, 'ov read terminal-draft');
      const commandRecord = host.records[0];
      await host.press('VikingBot');
      await host.press('First draft');
      assert.equal(changed.at(-1), 'Private draft from A');

      await host.render({
        ...props,
        chatOptions: {
          activation:
            replacement === 'activation adapter' ? readyActivation() : first,
          scopeKey: replacement === 'chat scope key' ? 'chat-b' : 'chat-a',
        },
      });
      await settle();
      await host.press('Second draft');
      assert.equal(changed.at(-1), 'New draft from B');
      await host.press('Terminal');
      assert.equal(host.input.value, 'ov read terminal-draft');
      assert.equal(host.records[0], commandRecord);
      assert.equal(host.records.length, 1);
    } finally {
      await host.dispose();
    }
  });
}
