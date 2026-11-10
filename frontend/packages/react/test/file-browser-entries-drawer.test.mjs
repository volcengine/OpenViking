import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { test } from 'node:test';
import { act, createElement, StrictMode } from 'react';
import { mount } from './helpers/module-dom.mjs';
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries';

for (const name of ['MutationObserver', 'HTMLIFrameElement', 'SVGElement'])
  globalThis[name] = window[name];
globalThis.getComputedStyle = window.getComputedStyle.bind(window);

const drawer = () =>
  document.querySelector('[role="dialog"][aria-modal="true"]');
const click = async (element) => {
  await act(() => {
    element.focus();
    element.click();
  });
};
const finishTransition = () => act(() => delay(350));

test('the connection drawer exits before releasing scroll and follows scoped host themes', async () => {
  const host = await mount(
    createElement(
      StrictMode,
      null,
      createElement(FileBrowserEntries, {
        agentConnect: { apiKey: 'drawer-demo-key', locale: 'en' },
        labels: { close: 'Close connection' },
      }),
    ),
  );
  const trigger = host.container.querySelector('button');
  const initialOverflow = document.body.style.overflow;
  try {
    assert.equal(drawer(), null);
    // jsdom does not inherit CSS variables, so provide the trigger's computed token.
    trigger.style.setProperty('--ov-ui-color-bg-1', 'rgb(20, 30, 40)');
    await click(trigger);
    const panel = drawer();
    assert.ok(
      panel && !host.container.contains(panel),
      'the panel escapes clipped hosts',
    );
    const layer = panel.closest('.ov-drawer');
    assert.equal(
      document.querySelector('dialog'),
      null,
      'no native browser dialog',
    );
    assert.equal(document.body.style.overflow, 'hidden');
    assert.equal(
      layer.style.getPropertyValue('--ov-ui-color-bg-1'),
      'rgb(20, 30, 40)',
    );
    assert.ok(!panel.textContent.includes('drawer-demo-key'));
    await act(() => trigger.style.removeProperty('--ov-ui-color-bg-1'));
    assert.equal(layer.style.getPropertyValue('--ov-ui-color-bg-1'), '');
    await click(panel.querySelector('button'));
    assert.equal(
      drawer(),
      panel,
      'closing content survives until the slide-out ends',
    );
    assert.equal(document.body.style.overflow, 'hidden');
    await finishTransition();
    assert.equal(drawer(), null);
    assert.equal(document.body.style.overflow, initialOverflow);
    assert.equal(document.activeElement, trigger);
    await click(trigger);
    await click(document.querySelector('.ov-drawer__mask'));
    await finishTransition();
    assert.equal(drawer(), null);
    await click(trigger);
    await act(() =>
      drawer()
        .querySelector('button')
        .dispatchEvent(
          new window.KeyboardEvent('keydown', {
            key: 'Escape',
            bubbles: true,
            cancelable: true,
          }),
        ),
    );
    await finishTransition();
    assert.equal(drawer(), null);
    await click(trigger);
  } finally {
    await host.unmount();
  }
  assert.equal(drawer(), null);
  assert.equal(document.body.style.overflow, initialOverflow);
});
