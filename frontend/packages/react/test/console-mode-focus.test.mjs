import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { act } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

const tick = () => act(() => new Promise((resolve) => setTimeout(resolve, 10)));
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const propsFor = (eligible, extra = {}) => ({
  locale: 'en-US',
  defaultMode: 'conversation',
  conversation: { messages: [], composer: false },
  chatOptions: {
    activation: {
      isEligible: () => eligible.promise,
      getStatus: async () =>
        assert.fail('An ineligible scope cannot read status'),
      start: async () => assert.fail('Eligibility must not start the Bot'),
    },
  },
  ...extra,
});
const focus = (element) => act(() => element.focus());
const unexpectedRequest = async () =>
  assert.fail('Controlled chat must not request data');

for (const mode of [undefined, 'conversation', 'command']) {
  test(`removing focused mode controls restores CLI input for ${mode ?? 'uncontrolled'} mode`, async () => {
    const eligible = deferred();
    const changes = [];
    const host = await mount(
      unexpectedRequest,
      propsFor(eligible, {
        mode,
        onModeChange: (next) => changes.push(next),
      }),
    );
    try {
      const input = host.input;
      await focus(host.button('VikingBot'));
      assert.equal(document.activeElement, host.button('VikingBot'));
      await act(() => eligible.resolve(false));
      await tick();
      assert.equal(host.container.querySelector('.ov-console__modes'), null);
      assert.equal(input.closest('.ov-console__panel').hidden, false);
      assert.equal(document.activeElement, input);
      assert.deepEqual(changes, []);
    } finally {
      await host.dispose();
    }
  });
}

test('eligibility updates preserve focus outside the mode controls', async () => {
  const eligible = deferred();
  const outside = document.createElement('button');
  document.body.append(outside);
  const host = await mount(unexpectedRequest, propsFor(eligible));
  try {
    await focus(outside);
    await act(() => eligible.resolve(false));
    await tick();
    assert.equal(host.container.querySelector('.ov-console__modes'), null);
    assert.equal(document.activeElement, outside);
  } finally {
    await host.dispose();
    outside.remove();
  }
});

test('mode reordering keeps its focused button and empty modes do not restore hidden input', async () => {
  const eligible = deferred();
  const props = propsFor(eligible);
  const host = await mount(unexpectedRequest, props);
  try {
    const button = host.button('VikingBot');
    await focus(button);
    await host.render({ ...props, modes: ['command', 'conversation'] });
    await tick();
    assert.equal(host.button('VikingBot'), button);
    assert.equal(document.activeElement, button);
    await host.render({ ...props, modes: [] });
    await tick();
    assert.equal(host.container.querySelector('.ov-console__panel'), null);
    assert.equal(document.activeElement, document.body);
  } finally {
    await host.dispose();
  }
});

test('a busy CLI restores focus to its panel while the input is disabled', async () => {
  const eligible = deferred();
  const response = deferred();
  const host = await mount(
    async () => response.promise,
    propsFor(eligible, { mode: 'command' }),
  );
  try {
    await host.execute('ov health');
    assert.equal(host.input.disabled, true);
    const panel = host.input.closest('.ov-console__panel');
    await focus(host.button('VikingBot'));
    await act(() => eligible.resolve(false));
    await tick();
    assert.equal(panel.hidden, false);
    assert.equal(panel.tabIndex, -1);
    assert.equal(document.activeElement, panel);
    await act(() => response.resolve(ok({ status: 'healthy' })));
    await tick();
  } finally {
    await host.dispose();
  }
});

for (const source of ['Bot body', 'outside']) {
  test(`removing the Bot panel restores only focus owned by ${source}`, async () => {
    const outside = document.createElement('button');
    document.body.append(outside);
    const props = {
      locale: 'en-US',
      defaultMode: 'conversation',
      conversation: {
        messages: [],
        composer: false,
        children: createElement(
          'button',
          { type: 'button' },
          'Bot body action',
        ),
      },
    };
    const host = await mount(unexpectedRequest, props);
    try {
      const button = host.button('Bot body action');
      assert.ok(button);
      await focus(source === 'Bot body' ? button : outside);
      await host.render({ ...props, modes: ['command'] });
      await tick();
      assert.equal(
        host.container.querySelector('.ov-console__panel--conversation'),
        null,
      );
      assert.equal(
        document.activeElement,
        source === 'Bot body' ? host.input : outside,
      );
    } finally {
      await host.dispose();
      outside.remove();
    }
  });
}
