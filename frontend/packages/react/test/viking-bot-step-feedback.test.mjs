import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount } from './helpers/module-dom.mjs';
import { notificationContent } from './helpers/feedback-dom.mjs';

const click = (node) => act(async () => node.click());
const button = (view, name) =>
  [...view.container.querySelectorAll('[data-active-step] button')].find(
    (node) => node.textContent === name,
  );

for (const first of [
  { kind: 'text', initialValue: 'First', multiline: false },
  {
    kind: 'target',
    initialUri: 'viking://resources/output',
    directoryPicker: { rows: [] },
  },
  {
    kind: 'custom',
    render: (context) =>
      h('button', { onClick: () => context.submit('First') }, 'Next'),
  },
]) {
  test(`host error belongs only to the active step after visiting a ${first.kind} question`, async () => {
    const request = {
      kind: 'steps',
      steps: [
        first,
        { kind: 'text', initialValue: 'Second', multiline: false },
      ],
    };
    const render = (error) => h(VikingBotInteraction, { request, error });
    const view = await mount(render());
    try {
      await click(button(view, 'Next'));
      await view.render(render('Host failure'));
      assert.equal(
        document.querySelectorAll('.ov-notification--error').length,
        1,
      );
      assert.equal(notificationContent().textContent, 'Host failure');
      await click(button(view, 'Previous'));
      assert.equal(
        document.querySelectorAll('.ov-notification--error').length,
        1,
      );
    } finally {
      await view.unmount();
    }
  });
}
