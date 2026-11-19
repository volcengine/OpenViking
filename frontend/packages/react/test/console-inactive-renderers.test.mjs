import assert from 'node:assert/strict';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { act, createElement as h } from 'react';
import { createPortal } from 'react-dom';
import { mount, ok, settle } from './helpers/module-dom.mjs';

const { Console, OpenVikingProvider } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'console-overlays.js'))
);
const fetch = async () => ok([]);
const frame = (mode, interaction) =>
  h(
    OpenVikingProvider,
    { fetch },
    h(Console, {
      mode,
      conversation: { messages: [], composer: false, interaction },
    }),
  );
const portal = (value) =>
  createPortal(
    h('output', { 'data-testid': 'custom-portal' }, String(value ?? 'custom')),
    document.body,
  );
const field = ({ value }) => portal(value);
const schedule = {
  kind: 'schedule',
  question: 'Schedule',
  initialValue: { mode: 'weekly', time: '09:30', weekday: 2 },
};
const cases = [
  [
    'custom request',
    { request: { kind: 'custom', render: () => portal('custom') } },
  ],
  [
    'text value',
    {
      request: { kind: 'text', initialValue: 'Wiki name' },
      fields: { value: { render: field } },
    },
  ],
  [
    'choice value',
    {
      request: { kind: 'choice', options: [{ value: 'wiki', label: 'Wiki' }] },
      fields: { value: { render: field } },
    },
  ],
  [
    'repair value',
    {
      request: { kind: 'repair', options: [{ id: 'fix', label: 'Fix' }] },
      fields: { value: { render: field } },
    },
  ],
  ['schedule mode', { request: schedule, fields: { mode: { render: field } } }],
  ['schedule time', { request: schedule, fields: { time: { render: field } } }],
  [
    'schedule weekday',
    { request: schedule, fields: { weekday: { render: field } } },
  ],
  [
    'target value',
    {
      request: { kind: 'target', directoryPicker: { rows: [] } },
      fields: { value: { render: field } },
    },
  ],
];

for (const [name, interaction] of cases) {
  test(`inactive ${name} renderer removes its body portal`, async () => {
    const host = await mount(frame('conversation', interaction));
    try {
      const output = document.querySelector('[data-testid="custom-portal"]');
      assert.ok(output);
      const text = output.textContent;
      await host.render(frame('command', interaction));
      assert.equal(
        document.querySelector('[data-testid="custom-portal"]'),
        null,
      );
      const terminal = host.container.querySelector('textarea');
      await act(async () => terminal.focus());
      await settle();
      assert.equal(document.activeElement === terminal, true);
      await host.render(frame('conversation', interaction));
      assert.equal(
        document.querySelector('[data-testid="custom-portal"]').textContent,
        text,
      );
    } finally {
      await host.unmount();
    }
  });
}

test('a retained inactive step also removes its custom portal while keeping the sequence answer', async () => {
  const interaction = {
    request: {
      kind: 'steps',
      steps: [
        {
          kind: 'custom',
          render: ({ submit }) =>
            createPortal(
              h(
                'button',
                {
                  'data-testid': 'step-portal',
                  onClick: () => submit('first answer'),
                },
                'Next',
              ),
              document.body,
            ),
        },
        { kind: 'text', question: 'Second question', multiline: false },
      ],
    },
  };
  const host = await mount(frame('conversation', interaction));
  try {
    await act(async () =>
      document.querySelector('[data-testid="step-portal"]').click(),
    );
    assert.equal(document.querySelector('[data-testid="step-portal"]'), null);
    const input = host.container.querySelector('input[name="answer"]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(input, 'Second answer');
      input.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await host.render(frame('command', interaction));
    assert.equal(document.querySelector('[data-testid="step-portal"]'), null);
    await host.render(frame('conversation', interaction));
    assert.equal(host.container.querySelector('input[name="answer"]'), input);
    assert.equal(input.value, 'Second answer');
  } finally {
    await host.unmount();
  }
});
