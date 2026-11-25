import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { mount } from './helpers/console-dom.mjs';

const tick = () => act(() => new Promise((resolve) => setTimeout(resolve, 10)));
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const commandVisible = (host) =>
  host.container
    .querySelector('.ov-command-console')
    ?.closest('.ov-console__panel')?.hidden === false;
function assertBotActive(host) {
  assert.equal(
    host.container.querySelector('.ov-console__panel--conversation')?.hidden,
    false,
  );
  assert.equal(commandVisible(host), false);
}
const mountActivation = (activation, props = {}) =>
  mount(
    async () => assert.fail('Controlled empty chat must not request data'),
    {
      locale: 'en-US',
      defaultMode: 'conversation',
      conversation: { messages: [], composer: false },
      chatOptions: { activation },
      ...props,
    },
  );

test('default Bot remains active throughout eligibility and readiness queries', async () => {
  const eligible = deferred();
  const ready = deferred();
  const host = await mountActivation({
    isEligible: () => eligible.promise,
    getStatus: () => ready.promise,
    start: async () => assert.fail('Loading must not start the Bot'),
  });
  try {
    assertBotActive(host);
    assert.match(host.text, /Checking VikingBot status/);
    await act(() => eligible.resolve(true));
    await tick();
    assertBotActive(host);
    assert.match(host.text, /Checking VikingBot status/);
    await act(() => ready.resolve('ready'));
    await tick();
    assertBotActive(host);
    assert.ok(host.container.querySelector('.ov-viking-bot'));
  } finally {
    await host.dispose();
  }
});

for (const stage of ['eligibility', 'status']) {
  test(`${stage} failure stays in Bot and retries without switching mode or starting`, async () => {
    let failing = true;
    const changes = [];
    const host = await mountActivation(
      {
        async isEligible() {
          if (failing && stage === 'eligibility')
            throw new Error('Eligibility unavailable');
          return true;
        },
        async getStatus() {
          if (failing && stage === 'status')
            throw new Error('Status unavailable');
          return 'ready';
        },
        start: async () => assert.fail('Query retry must not start the Bot'),
      },
      { onModeChange: (mode) => changes.push(mode) },
    );
    try {
      await tick();
      assertBotActive(host);
      assert.equal(
        host.container.querySelector('.ov-console-activation__action')
          ?.textContent,
        'Retry',
      );
      failing = false;
      await host.press('Retry');
      await tick();
      assertBotActive(host);
      assert.ok(host.container.querySelector('.ov-viking-bot'));
      assert.deepEqual(changes, []);
    } finally {
      await host.dispose();
    }
  });
}

test('a manual CLI selection during loading survives the ready response', async () => {
  const ready = deferred();
  const changes = [];
  const host = await mountActivation(
    {
      isEligible: async () => true,
      getStatus: () => ready.promise,
      start: async () => assert.fail('Loading must not start the Bot'),
    },
    { onModeChange: (mode) => changes.push(mode) },
  );
  try {
    await tick();
    assertBotActive(host);
    await host.press('Terminal');
    assert.equal(commandVisible(host), true);
    await act(() => ready.resolve('ready'));
    await tick();
    assert.equal(commandVisible(host), true);
    assert.deepEqual(changes, ['command']);
  } finally {
    await host.dispose();
  }
});

for (const preference of ['mode', 'defaultMode']) {
  test(`an explicit ${preference}=command stays in CLI while loading and ready`, async () => {
    const ready = deferred();
    const host = await mountActivation(
      {
        isEligible: async () => true,
        getStatus: () => ready.promise,
        start: async () => assert.fail('Loading must not start the Bot'),
      },
      { [preference]: 'command' },
    );
    try {
      assert.equal(commandVisible(host), true);
      await act(() => ready.resolve('ready'));
      await tick();
      assert.equal(commandVisible(host), true);
    } finally {
      await host.dispose();
    }
  });
}

for (const modes of [['command'], []]) {
  test(`${modes.length ? 'command-only' : 'empty modes'} does not query activation`, async () => {
    let calls = 0;
    const host = await mountActivation(
      {
        async isEligible() {
          calls++;
          return true;
        },
        async getStatus() {
          calls++;
          return 'ready';
        },
        async start() {
          calls++;
        },
      },
      { modes },
    );
    try {
      await tick();
      assert.equal(calls, 0);
      assert.equal(
        host.container.querySelector('.ov-console__panel--conversation'),
        null,
      );
      assert.equal(commandVisible(host), modes.length > 0);
      if (!modes.length)
        assert.equal(host.container.querySelector('.ov-console__panel'), null);
    } finally {
      await host.dispose();
    }
  });
}
