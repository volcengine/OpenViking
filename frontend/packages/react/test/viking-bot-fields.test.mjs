import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount, deferred, settle } from './helpers/module-dom.mjs';

const submit = (view) =>
  act(async () => {
    view.container
      .querySelector('form')
      .dispatchEvent(
        new window.Event('submit', { bubbles: true, cancelable: true }),
      );
  });
const input = (node, value) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(node, value);
    node.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
const render = ({ value, onChange, disabled, controlProps }) =>
  h('input', {
    ...controlProps,
    disabled,
    value,
    onChange: (event) => onChange(event.target.value),
  });

test('separate schedule validators and custom controls retain errors on unchanged fields', async () => {
  let sends = 0;
  const view = await mount(
    h(VikingBotInteraction, {
      request: {
        kind: 'schedule',
        initialValue: { mode: 'weekly', weekday: 3, time: '12:00' },
      },
      fields: {
        time: {
          label: 'Run time',
          render,
          validate: async (time) =>
            time === '12:00' ? 'Time unavailable' : undefined,
        },
        weekday: {
          label: 'Day',
          render: (ctx) =>
            render({
              ...ctx,
              onChange: (value) => ctx.onChange(Number(value)),
            }),
          validate: (day) => (day === 3 ? 'Day unavailable' : undefined),
        },
      },
      onSubmit: () => {
        sends++;
      },
    }),
  );
  try {
    await submit(view);
    const time = view.container.querySelector(
      'input[aria-labelledby$="time-label"]',
    );
    const day = view.container.querySelector(
      'input[aria-labelledby$="weekday-label"]',
    );
    assert.equal(time.getAttribute('aria-invalid'), 'true');
    assert.equal(day.getAttribute('aria-invalid'), 'true');
    await input(time, '13:00');
    assert.equal(time.hasAttribute('aria-invalid'), false);
    assert.equal(day.getAttribute('aria-invalid'), 'true');
    await input(day, '4');
    await submit(view);
    assert.equal(sends, 1);
  } finally {
    await view.unmount();
  }
});

test('async validation prevents duplicate sends and ignores results after request replacement', async () => {
  const gate = deferred();
  let sends = 0;
  let signal;
  const props = {
    requestKey: 'a',
    request: { kind: 'text', initialValue: 'A' },
    fields: {
      value: {
        validate: (_, __, context) => {
          signal = context.signal;
          return gate.promise;
        },
      },
    },
    onSubmit: () => {
      sends++;
    },
  };
  const view = await mount(h(VikingBotInteraction, props));
  try {
    await submit(view);
    await submit(view);
    assert.equal(sends, 0);
    assert.equal(view.container.querySelector('textarea').disabled, true);
    await view.render(
      h(VikingBotInteraction, {
        ...props,
        requestKey: 'b',
        request: { kind: 'text', initialValue: 'B' },
      }),
    );
    await act(async () => gate.resolve(undefined));
    await settle();
    assert.equal(sends, 0);
    assert.equal(signal.aborted, true);
    assert.equal(view.container.querySelector('textarea').value, 'B');
  } finally {
    await view.unmount();
  }
});

test('target field renderers validate and submit without mounting the default picker trigger', async () => {
  const answers = [];
  const view = await mount(
    h(VikingBotInteraction, {
      request: {
        kind: 'target',
        initialUri: 'viking://resources/a',
        directoryPicker: { rows: [] },
      },
      fields: {
        value: {
          render,
          validate: (uri) => (uri.endsWith('/a') ? 'Read only' : undefined),
        },
      },
      onSubmit: (answer) => answers.push(answer),
    }),
  );
  try {
    await submit(view);
    assert.match(view.container.textContent, /Read only/);
    await input(view.container.querySelector('input'), 'viking://resources/b');
    assert.doesNotMatch(view.container.textContent, /Read only/);
    await submit(view);
    assert.deepEqual(answers, [
      { kind: 'target', value: 'viking://resources/b' },
    ]);
  } finally {
    await view.unmount();
  }
});

test('frequency errors identify the radio group and hidden fields skip validation', async () => {
  let times = 0;
  let weekdays = 0;
  let answer;
  const view = await mount(
    h(VikingBotInteraction, {
      request: {
        kind: 'schedule',
        initialValue: { mode: 'daily', time: '12:00' },
      },
      fields: {
        mode: {
          validate: (mode) =>
            mode === 'daily' ? 'Frequency blocked' : undefined,
        },
        time: {
          validate: () => {
            times++;
          },
        },
        weekday: {
          validate: () => {
            weekdays++;
          },
        },
      },
      onSubmit: (value) => {
        answer = value;
      },
    }),
  );
  try {
    await submit(view);
    const group = view.container.querySelector('[role=radiogroup]');
    assert.equal(group.getAttribute('aria-invalid'), 'true');
    assert.ok(document.getElementById(group.getAttribute('aria-describedby')));
    await act(async () =>
      view.container.querySelector('input[value=none]').click(),
    );
    assert.equal(group.hasAttribute('aria-invalid'), false);
    await submit(view);
    assert.equal(times, 1);
    assert.equal(weekdays, 0);
    assert.deepEqual(answer, { kind: 'schedule', value: { mode: 'none' } });
  } finally {
    await view.unmount();
  }
});
