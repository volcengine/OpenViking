import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { VikingBotSessionActions } from '@openviking/react/components/viking-bot';
import { Console } from '@openviking/react/modules/console';
import { act, click } from './helpers/command-console-dom.mjs';
import './helpers/console-dom.mjs';

const token = '--ov-ui-color-bg-1';
const red = 'rgb(255, 0, 0)';
const blue = 'rgb(0, 0, 255)';

for (const [name, component, props, selector] of [
  [
    'VikingBot',
    VikingBotSessionActions,
    { sessions: { items: [], onSelect() {}, onNew() {} } },
    '.ov-viking-bot__popover-anchor',
  ],
  ['Console', Console, { mode: 'command' }, '.ov-console__history'],
]) {
  test(`${name} popovers reopen with the current host theme after an override is removed`, async () => {
    const container = document.createElement('div');
    document.body.append(container);
    const root = createRoot(container);
    try {
      await act(() =>
        root.render(
          createElement(StrictMode, null, createElement(component, props)),
        ),
      );
      const anchor = container.querySelector(selector);
      const button = anchor.querySelector('button');
      const surface = document.getElementById(
        button.getAttribute('aria-controls'),
      );
      surface.style.setProperty('--host-surface-only', 'retained');
      for (const color of [red, blue]) {
        await act(() => anchor.style.setProperty(token, color));
        await click(button);
        assert.equal(button.getAttribute('aria-expanded'), 'true');
        assert.equal(surface.style.getPropertyValue(token), color);
        await click(button);
        assert.equal(button.getAttribute('aria-expanded'), 'false');
        await act(() => anchor.style.removeProperty(token));
        await click(button);
        assert.equal(
          surface.style.getPropertyValue(token),
          '',
          'a removed override must not survive reopening the same surface',
        );
        assert.equal(
          surface.style.getPropertyValue('--host-surface-only'),
          'retained',
          'theme cleanup must preserve unrelated surface styles',
        );
        await click(button);
      }
      await click(button);
      await act(() => anchor.style.setProperty(token, red));
      assert.equal(surface.style.getPropertyValue(token), red);
      await act(() => anchor.style.removeProperty(token));
      assert.equal(surface.style.getPropertyValue(token), '');
    } finally {
      await act(() => root.unmount());
      container.remove();
    }
  });
}
