import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import {
  ScopeSelector,
  ScopeSwitcher,
} from '@openviking/react/components/scope-switcher';
import { mount } from './helpers/module-dom.mjs';

test('wrapped scope panels stay inside desktop viewport edges and preserve the mobile sheet', async (t) => {
  let left = -143.3;
  const width = 272;
  const viewport = window.innerWidth;
  const original = window.HTMLElement.prototype.getBoundingClientRect;
  t.mock.method(
    window.HTMLElement.prototype,
    'getBoundingClientRect',
    function () {
      if (!this.classList.contains('ov-scope-switcher__panel'))
        return original.call(this);
      const shift =
        parseFloat(
          this.style.getPropertyValue('--ov-ui-scope-switcher-panel-shift'),
        ) || 0;
      return new window.DOMRect(left + shift, 180, width, 120);
    },
  );
  const observed = new Map();
  let disconnects = 0;
  t.mock.method(globalThis, 'ResizeObserver', function (update) {
    return {
      observe(node) {
        observed.set(node, update);
      },
      disconnect() {
        disconnects++;
      },
    };
  });
  const css = document.createElement('style');
  css.textContent = '.ov-scope-switcher__panel { position: absolute; }';
  document.head.append(css);
  const view = await mount(
    h(
      ScopeSwitcher,
      null,
      h(ScopeSelector, {
        kind: 'fixed',
        identity: { key: 'library' },
        trigger: { label: 'Research library title' },
      }),
      h(ScopeSelector, {
        kind: 'selectable',
        identity: { key: 'user', ariaLabel: 'Switch user' },
        data: {
          selectedId: 'alice',
          options: [{ id: 'alice', label: 'Alice' }],
        },
        state: { visible: true },
      }),
    ),
  );
  try {
    const panel = view.container.querySelector('[role="dialog"]');
    const preceding = view.container.querySelector('.ov-scope-switcher__level');
    assert.ok(
      observed.has(preceding),
      'preceding levels are observed when they resize without changing row count',
    );
    assert.ok(
      panel.getBoundingClientRect().left >= 8,
      'wrapped final level is visible near the left edge',
    );
    left = viewport - 80;
    window.dispatchEvent(new window.Event('resize'));
    assert.ok(
      panel.getBoundingClientRect().right <= viewport - 8,
      'right-edge collision is corrected',
    );
    left = -20;
    observed.get(preceding)();
    assert.equal(
      panel.getBoundingClientRect().left,
      8,
      'a preceding label resize recomputes the correction even at the same row count',
    );
    left = 100;
    window.dispatchEvent(new window.Event('scroll'));
    assert.equal(
      panel.getBoundingClientRect().left,
      100,
      'scrolling back releases the correction',
    );
    panel.style.position = 'fixed';
    window.dispatchEvent(new window.Event('resize'));
    assert.equal(
      panel.style.getPropertyValue('--ov-ui-scope-switcher-panel-shift'),
      '',
      'the fixed mobile sheet keeps its CSS positioning',
    );
  } finally {
    await view.unmount();
    css.remove();
    assert.equal(disconnects, 1, 'the observer is released on unmount');
  }
});
