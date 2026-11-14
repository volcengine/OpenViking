import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { VikingBotInteraction } from '@openviking/react/components/viking-bot/interaction';
import { mount } from './helpers/module-dom.mjs';

for (const kind of ['text', 'target', 'custom']) {
  test(`${kind} HITL preserves submit/cancel analytics without leaking answers`, async () => {
    let context;
    const events = [];
    const answers = [];
    let cancelled = 0;
    const request =
      kind === 'text'
        ? { kind, initialValue: 'private answer' }
        : kind === 'target'
          ? {
              kind,
              initialUri: 'viking://resources/private-target',
              directoryPicker: { rows: [] },
            }
          : {
              kind,
              render: (next) => {
                context = next;
                return null;
              },
            };
    const analytics = { onEvent: (event) => events.push(event) };
    const render = (disabled = false) =>
      h(
        OpenVikingProvider,
        { analytics },
        h(VikingBotInteraction, {
          request,
          disabled,
          onSubmit: (answer) => answers.push(answer),
          onCancel: () => {
            cancelled += 1;
          },
        }),
      );
    const view = await mount(render());
    const submit = () =>
      act(async () => {
        if (context) await context.submit('private answer');
        else
          view.container
            .querySelector('form')
            .dispatchEvent(
              new window.Event('submit', { bubbles: true, cancelable: true }),
            );
      });
    const cancel = () =>
      act(async () => {
        if (context) context.cancel();
        else
          [...view.container.querySelectorAll('button')]
            .find((button) => button.textContent === 'Cancel')
            .click();
      });
    try {
      await submit();
      await cancel();
      assert.deepEqual(
        events.map((event) => event.name),
        [
          'openviking_viking_bot_interaction_submit',
          'openviking_viking_bot_interaction_cancel',
        ],
      );
      assert.equal(answers.length, 1);
      assert.equal(answers[0].kind, kind);
      assert.equal(cancelled, 1);
      assert.equal(JSON.stringify(events).includes('private'), false);
      await view.render(render(true));
      await submit();
      await cancel();
      assert.equal(events.length, 2);
      assert.equal(answers.length, 1);
      assert.equal(cancelled, 1);
    } finally {
      await view.unmount();
    }
  });
}
