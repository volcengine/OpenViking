import assert from 'node:assert/strict';
import test from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';
import { act, type } from './helpers/command-console-dom.mjs';

const activation = {
  async isEligible() {
    return true;
  },
  async getStatus() {
    return 'ready';
  },
  async start() {
    assert.fail('A ready Bot must not be started again');
  },
};
const settle = () =>
  act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
const conversation = (changed) => ({
  messages: [],
  composer: false,
  quickInputs: [
    { id: 'a', label: 'First draft', prompt: 'Unsent chat text' },
    { id: 'b', label: 'Second draft', prompt: 'More chat text' },
  ],
  onDraftChange: (value) => changed.push(value),
});

for (const field of [
  'baseURL',
  'apiKey',
  'accountId',
  'userId',
  'resourceId',
  'signal',
]) {
  test(`changing Terminal ${field} preserves a separately configured chat draft`, async () => {
    const changed = [];
    const chatOptions = {
      activation,
      requestOptions: { userId: 'fixed-chat-user' },
    };
    const rootOptions = {
      [field]: field === 'signal' ? new AbortController().signal : 'first',
    };
    if (field === 'baseURL') rootOptions.baseURL = 'https://first.invalid';
    const nextOptions = {
      [field]: field === 'signal' ? new AbortController().signal : 'second',
    };
    if (field === 'baseURL') nextOptions.baseURL = 'https://second.invalid';
    const props = {
      chatOptions,
      conversation: conversation(changed),
      requestOptions: rootOptions,
    };
    const host = await mount(async () => ok({}), props);
    try {
      await settle();
      await host.execute('ov help');
      await type(host.input, 'ov read terminal-draft');
      await host.press('VikingBot');
      await host.press('First draft');
      assert.equal(changed.at(-1), 'Unsent chat text');

      const next = { ...props, requestOptions: nextOptions };
      await host.render(next);
      await settle();
      await host.press('Terminal');
      assert.equal(host.input.value, '');
      assert.equal(host.records.length, 0);
      // Remount shortcuts to release their old prompt ownership, exposing the
      // full retained text when the next prompt is appended.
      await host.render({ ...next, modes: ['command'] });
      await host.render(next);
      await settle();
      await host.press('VikingBot');
      await host.press('Second draft');
      assert.equal(changed.at(-1), 'Unsent chat text\nMore chat text');
    } finally {
      await host.dispose();
    }
  });
}

test('inherited chat identity clears its draft when the root request user changes', async () => {
  const changed = [];
  const props = {
    chatOptions: { activation },
    conversation: conversation(changed),
    requestOptions: { userId: 'alice' },
  };
  const host = await mount(async () => ok({}), props);
  try {
    await settle();
    await host.press('VikingBot');
    await host.press('First draft');
    await host.render({ ...props, requestOptions: { userId: 'bob' } });
    await settle();
    await host.press('Second draft');
    assert.equal(changed.at(-1), 'More chat text');
  } finally {
    await host.dispose();
  }
});
