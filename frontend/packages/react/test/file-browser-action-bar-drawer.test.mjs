import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { test } from 'node:test';
import { act, createElement, StrictMode } from 'react';
import { mount, ok } from './helpers/module-dom.mjs';
import { FileBrowserActionBar } from '@openviking/react/components/file-browser-action-bar';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';

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
const assertScrollLocked = () => {
  assert.equal(document.body.style.overflowX, 'hidden');
  assert.equal(document.body.style.overflowY, 'hidden');
};
const entry = (props = {}) =>
  createElement(FileBrowserActionBar, {
    locale: 'en-US',
    agentConnect: { apiKey: 'drawer-demo-key', locale: 'en-US', ...props },
  });
const moduleEntry = () =>
  createElement(
    OpenVikingProvider,
    { fetch: () => ok([]) },
    createElement(FileBrowser, {
      locale: 'en-US',
      agentConnect: { apiKey: 'drawer-demo-key', locale: 'en-US' },
    }),
  );

test('the connection drawer exits before releasing scroll and follows scoped host themes', async () => {
  const host = await mount(createElement(StrictMode, null, entry()));
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
    assertScrollLocked();
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
    assertScrollLocked();
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

for (const first of [0, 1]) {
  for (const firstAction of ['close', 'unmount']) {
    for (const lastAction of ['close', 'unmount']) {
      test(`overlapping drawers: release ${first} by ${firstAction}, then ${1 - first} by ${lastAction}`, async () => {
        const body = document.body;
        const original = body.style.cssText;
        body.style.overflow = 'scroll';
        body.style.paddingRight = '7px';
        const hosts = [await mount(entry()), await mount(moduleEntry())];
        const panels = [];
        const unmounted = new Set();
        const release = async (index, action) => {
          if (action === 'unmount') {
            await hosts[index].unmount();
            unmounted.add(index);
          } else {
            await click(panels[index].querySelector('.ov-drawer__close'));
            assertScrollLocked();
            await finishTransition();
          }
        };
        try {
          await click(hosts[0].container.querySelector('button'));
          panels.push(drawer());
          const lockedPadding = body.style.paddingRight;
          await click(hosts[1].container.querySelector('button'));
          panels.push(document.querySelectorAll('[role="dialog"]')[1]);
          assert.equal(body.style.paddingRight, lockedPadding);
          await release(first, firstAction);
          assert.equal(document.querySelectorAll('[role="dialog"]').length, 1);
          assertScrollLocked();
          assert.equal(body.style.paddingRight, lockedPadding);
          await release(1 - first, lastAction);
          assert.equal(drawer(), null);
          assert.equal(body.style.overflow, 'scroll');
          assert.equal(body.style.paddingRight, '7px');
        } finally {
          for (const index of [0, 1]) {
            if (!unmounted.has(index)) await hosts[index].unmount();
          }
          body.style.cssText = original;
        }
      });
    }
  }
}

for (const handling of ['preventDefault', 'stopPropagation', 'unhandled']) {
  test(`descendant Escape handler runs before the drawer: ${handling}`, async () => {
    let calls = 0;
    const host = await mount(
      entry({
        onKeyDown(event) {
          calls++;
          if (handling !== 'unhandled') event[handling]();
        },
      }),
    );
    try {
      await click(host.container.querySelector('button'));
      await act(() =>
        document.querySelector('.ov-agent-connect button').dispatchEvent(
          new window.KeyboardEvent('keydown', {
            key: 'Escape',
            bubbles: true,
            cancelable: true,
          }),
        ),
      );
      await finishTransition();
      assert.equal(calls, 1);
      assert.equal(Boolean(drawer()), handling !== 'unhandled');
    } finally {
      await host.unmount();
    }
  });
}

test('a pre-existing host scroll lock is preserved', async () => {
  const original = document.body.style.cssText;
  document.body.style.overflow = 'hidden';
  document.body.style.paddingRight = '13px';
  const host = await mount(entry());
  try {
    await click(host.container.querySelector('button'));
    assert.equal(document.body.style.paddingRight, '13px');
  } finally {
    await host.unmount();
  }
  assert.equal(document.body.style.overflow, 'hidden');
  assert.equal(document.body.style.paddingRight, '13px');
  document.body.style.cssText = original;
});
