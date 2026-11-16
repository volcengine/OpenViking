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
    '.ov-viking-bot__header-action[aria-haspopup="listbox"]',
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
      // jsdom does not inherit custom properties; use the measured trigger.
      const button = anchor.matches('button')
        ? anchor
        : anchor.querySelector('button');
      const surface = () => {
        const node = document.getElementById(
          button.getAttribute('aria-controls'),
        );
        return node?.closest('.ov-dropdown') ?? node;
      };
      for (const color of [red, blue]) {
        await act(() => anchor.style.setProperty(token, color));
        await click(button);
        assert.equal(button.getAttribute('aria-expanded'), 'true');
        assert.equal(surface().style.getPropertyValue(token), color);
        surface().style.setProperty('--host-surface-only', 'retained');
        await act(() => anchor.style.removeProperty(token));
        assert.equal(surface().style.getPropertyValue(token), '');
        assert.equal(
          surface().style.getPropertyValue('--host-surface-only'),
          'retained',
        );
        await click(button);
        assert.equal(button.getAttribute('aria-expanded'), 'false');
        await click(button);
        assert.equal(
          surface().style.getPropertyValue(token),
          '',
          'removed overrides must not survive reopening',
        );
        await click(button);
      }
      await click(button);
      await act(() => anchor.style.setProperty(token, red));
      assert.equal(surface().style.getPropertyValue(token), red);
      await act(() => anchor.style.removeProperty(token));
      assert.equal(surface().style.getPropertyValue(token), '');
    } finally {
      await act(() => root.unmount());
      container.remove();
    }
  });
}
