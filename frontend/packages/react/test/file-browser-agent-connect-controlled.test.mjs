import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { test } from 'node:test';
import { act, createElement } from 'react';
import { mount, ok } from './helpers/module-dom.mjs';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';

const drawer = () =>
  document.querySelector('[role="dialog"][aria-modal="true"]');
const click = (element) =>
  act(() => {
    element.focus();
    element.click();
  });
const finishTransition = () => act(() => delay(350));
const moduleEntry = (props) =>
  createElement(
    OpenVikingProvider,
    { fetch: () => ok([]) },
    createElement(FileBrowser, {
      locale: 'en-US',
      agentConnect: { apiKey: 'demo-key' },
      ...props,
    }),
  );

test('FileBrowser lets an external entry open the same drawer and handle close requests', async () => {
  const requests = [];
  const render = (open) =>
    moduleEntry({
      agentConnectOpen: open,
      onAgentConnectOpenChange: (next) => requests.push(next),
    });
  const host = await mount(render(false));
  const trigger = host.container.querySelector('[aria-haspopup="dialog"]');
  try {
    await click(trigger);
    assert.deepEqual(requests, [true]);
    assert.equal(drawer(), null, 'the host decides whether to open');

    await host.render(render(true));
    assert.equal(trigger.getAttribute('aria-expanded'), 'true');
    assert.ok(drawer());
    assert.deepEqual(requests, [true], 'prop updates do not echo events');

    await click(drawer().querySelector('.ov-drawer__close'));
    assert.deepEqual(requests, [true, false]);
    assert.equal(trigger.getAttribute('aria-expanded'), 'true');

    await host.render(render(false));
    await finishTransition();
    assert.equal(drawer(), null);
    assert.deepEqual(requests, [true, false]);

    await host.render(render(true));
    assert.ok(drawer());
    await click(document.querySelector('.ov-drawer__mask'));
    assert.deepEqual(requests, [true, false, false]);
  } finally {
    await host.unmount();
  }
});
