import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement, createRef } from 'react';
import { CommandConsole } from '@openviking/react/components/command-console';
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
const unexpectedRequest = async () =>
  assert.fail('Controlled chat must not request data');
const commandInput = (host) =>
  [
    ...host.container.querySelectorAll(
      'textarea.ov-command-console__input[name="command"]',
    ),
  ].at(-1);
async function removeFocusedModes(host, eligible) {
  await act(() => host.button('VikingBot').focus());
  await act(() => eligible.resolve(false));
  await tick();
}

for (const example of ['hidden textarea', 'complete CommandConsole']) {
  test(`focus recovery ignores a help slot containing ${example}`, async () => {
    const eligible = deferred();
    const sample = createRef();
    const host = await mount(
      unexpectedRequest,
      propsFor(eligible, {
        slots: {
          commandHelp:
            example === 'hidden textarea'
              ? createElement('textarea', {
                  hidden: true,
                  'data-testid': 'help-input',
                })
              : createElement(CommandConsole, {
                  records: [],
                  value: '',
                  onValueChange() {},
                  inputRef: sample,
                }),
        },
      }),
    );
    try {
      const help =
        example === 'hidden textarea'
          ? host.container.querySelector('[data-testid="help-input"]')
          : sample.current;
      const input = commandInput(host);
      assert.equal(host.container.querySelector('textarea'), help);
      assert.ok(input);
      await removeFocusedModes(host, eligible);
      assert.equal(document.activeElement, input);
      assert.notEqual(document.activeElement, help);
    } finally {
      await host.dispose();
    }
  });
}

test('an input that refuses focus falls back to the visible command panel', async () => {
  const eligible = deferred();
  const host = await mount(unexpectedRequest, propsFor(eligible));
  try {
    const input = commandInput(host);
    const panel = input.closest('.ov-console__panel');
    let attempts = 0;
    input.focus = () => {
      attempts++;
    };
    await removeFocusedModes(host, eligible);
    assert.equal(attempts, 1);
    assert.equal(panel.hidden, false);
    assert.equal(panel.tabIndex, -1);
    assert.equal(document.activeElement, panel);
  } finally {
    await host.dispose();
  }
});

test('a focus handler moving to an outside control prevents the panel fallback', async () => {
  const eligible = deferred();
  const outside = document.createElement('button');
  document.body.append(outside);
  const host = await mount(unexpectedRequest, propsFor(eligible));
  try {
    const input = commandInput(host);
    const panel = input.closest('.ov-console__panel');
    let fallbackAttempts = 0;
    input.focus = () => outside.focus();
    panel.focus = () => {
      fallbackAttempts++;
    };
    await removeFocusedModes(host, eligible);
    assert.equal(document.activeElement, outside);
    assert.equal(fallbackAttempts, 0);
  } finally {
    await host.dispose();
    outside.remove();
  }
});

for (const property of ['readOnly', 'disabled']) {
  test(`a ${property} command input falls back to its panel`, async () => {
    const eligible = deferred();
    const host = await mount(unexpectedRequest, propsFor(eligible));
    try {
      const input = commandInput(host);
      const panel = input.closest('.ov-console__panel');
      let attempts = 0;
      input[property] = true;
      input.focus = () => {
        attempts++;
      };
      await removeFocusedModes(host, eligible);
      assert.equal(attempts, 0);
      assert.equal(document.activeElement, panel);
    } finally {
      await host.dispose();
    }
  });
}
