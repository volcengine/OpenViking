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

const requestOptions = () => ({
  baseURL: 'https://alice.invalid',
  apiKey: 'alice-fixture',
  accountId: 'alice-account',
  userId: 'alice',
  resourceId: 'alice-library',
  signal: new AbortController().signal,
});
const service = {
  async listSessions() {
    return [];
  },
  async createSession() {
    return { id: 'fixture', title: 'Fixture' };
  },
  async loadMessages() {
    return [];
  },
  async removeSession() {},
  async *stream() {
    yield { type: 'complete' };
  },
};
const propsFor = (chatOptions, changed) => ({
  chatOptions: { activation, ...chatOptions },
  conversation: {
    messages: [],
    composer: false,
    quickInputs: [
      { id: 'a', label: 'First draft', prompt: 'Private draft from Alice' },
      { id: 'b', label: 'Second draft', prompt: 'New draft from Bob' },
    ],
    onDraftChange: (value) => changed.push(value),
  },
});

for (const field of [
  'baseURL',
  'apiKey',
  'accountId',
  'userId',
  'resourceId',
  'signal',
  'service',
  'userId and signal',
]) {
  test(`effective chat ${field} replacement isolates its draft and preserves terminal state`, async () => {
    const changed = [];
    const first = { requestOptions: requestOptions() };
    const next =
      field === 'service'
        ? { ...first, service }
        : {
            requestOptions: {
              ...first.requestOptions,
              [field]:
                field === 'signal' ? new AbortController().signal : 'bob',
            },
          };
    if (field === 'baseURL')
      next.requestOptions.baseURL = 'https://bob.invalid';
    if (field === 'userId and signal') {
      next.requestOptions = {
        ...first.requestOptions,
        userId: 'bob',
        signal: new AbortController().signal,
      };
    }
    const host = await mount(async () => ok({}), propsFor(first, changed));
    try {
      await settle();
      await host.execute('ov help');
      await type(host.input, 'ov read terminal-draft');
      const commandRecord = host.records[0];
      await host.press('VikingBot');
      await host.press('First draft');
      assert.equal(changed.at(-1), 'Private draft from Alice');

      await host.render(propsFor(next, changed));
      await settle();
      await host.press('Second draft');
      assert.equal(changed.at(-1), 'New draft from Bob');
      await host.press('Terminal');
      assert.equal(host.input.value, 'ov read terminal-draft');
      assert.equal(host.records[0], commandRecord);
      assert.equal(host.records.length, 1);
    } finally {
      await host.dispose();
    }
  });
}

test('equivalent chat request options preserve unsent text', async () => {
  const changed = [];
  const first = { requestOptions: requestOptions() };
  const host = await mount(async () => ok({}), propsFor(first, changed));
  try {
    await settle();
    await host.press('VikingBot');
    await host.press('First draft');
    await host.render(
      propsFor({ requestOptions: { ...first.requestOptions } }, changed),
    );
    await settle();
    // Remount the shortcut UI so its prompt replacement tracker no longer
    // owns Alice's text, while the Console retains the same chat identity.
    await host.render({ ...propsFor(first, changed), modes: ['command'] });
    await host.render(propsFor(first, changed));
    await settle();
    await host.press('Second draft');
    assert.equal(
      changed.at(-1),
      'Private draft from Alice\nNew draft from Bob',
    );
  } finally {
    await host.dispose();
  }
});
