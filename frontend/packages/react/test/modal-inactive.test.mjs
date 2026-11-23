import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { act, createElement as h, useRef, StrictMode } from 'react';
import { mount, settle } from './helpers/module-dom.mjs';

const load = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, name + '.js')));
const { Modal } = await load('modal');
const { ViewActivity } = await load('console-overlays');
for (const name of ['MutationObserver', 'HTMLIFrameElement', 'SVGElement'])
  globalThis[name] = window[name];
globalThis.getComputedStyle = window.getComputedStyle.bind(window);

function Host({ active, open, afterClose }) {
  const trigger = useRef(null);
  return h(
    'div',
    null,
    h('button', { 'aria-label': 'Terminal input' }, 'Terminal'),
    h(
      ViewActivity,
      { active },
      h(
        'div',
        { hidden: !active },
        h('button', { ref: trigger }, 'Modal trigger'),
        h(
          Modal,
          {
            open,
            title: 'Settings',
            triggerRef: trigger,
            afterClose,
            onClose() {},
          },
          h('input', { 'aria-label': 'Modal input', 'data-autofocus': '' }),
        ),
      ),
    ),
  );
}
const finish = () =>
  act(() => new Promise((resolve) => setTimeout(resolve, 350)));
const overlay = () => document.querySelector('.ov-modal');

test('an interrupted exit completes once and reactivation keeps a closed modal absent', async () => {
  const overflow = document.body.style.overflowY;
  let closed = 0;
  const frame = (active, open) =>
    h(StrictMode, null, h(Host, { active, open, afterClose: () => closed++ }));
  const view = await mount(frame(true, true));
  try {
    assert.ok(overlay()?.querySelector('[role=dialog]'));
    assert.equal(document.body.style.overflowY, 'hidden');
    await view.render(frame(true, false));
    assert.ok(overlay(), 'the active view retains its exit animation');
    assert.equal(closed, 0);
    await view.render(frame(false, false));
    assert.equal(overlay(), null);
    assert.equal(document.body.style.overflowY, overflow);
    assert.equal(closed, 1);

    const terminal = view.container.querySelector(
      '[aria-label="Terminal input"]',
    );
    await act(() => terminal.focus());
    await view.render(frame(false, false));
    await view.render(frame(true, false));
    await settle();
    assert.equal(overlay(), null);
    assert.equal(document.body.style.overflowY, overflow);
    assert.equal(document.activeElement, terminal);
    await finish();
    assert.equal(overlay(), null);
    assert.equal(closed, 1);

    await view.render(frame(true, true));
    assert.ok(overlay()?.querySelector('[role=dialog]'));
    assert.equal(document.body.style.overflowY, 'hidden');
    await view.render(frame(true, false));
    assert.ok(overlay());
    await finish();
    assert.equal(overlay(), null);
    assert.equal(document.body.style.overflowY, overflow);
    assert.equal(document.activeElement, terminal);
    assert.equal(closed, 2);
  } finally {
    await view.unmount();
  }
});

test('hiding an open modal clears its presence without reporting an unrequested close', async () => {
  const overflow = document.body.style.overflowY;
  let closed = 0;
  const frame = (active, open) =>
    h(Host, { active, open, afterClose: () => closed++ });
  const view = await mount(frame(true, true));
  try {
    await view.render(frame(false, true));
    assert.equal(overlay(), null);
    assert.equal(document.body.style.overflowY, overflow);
    assert.equal(closed, 0);
    await view.render(frame(false, false));
    await view.render(frame(true, false));
    await finish();
    assert.equal(overlay(), null);
    assert.equal(closed, 0);
    await view.render(frame(true, true));
    await view.render(frame(true, false));
    await view.render(frame(true, true));
    await finish();
    assert.ok(overlay()?.querySelector('[role=dialog]'));
    assert.equal(closed, 0, 'reopening cancels the preceding exit');
    await view.render(frame(true, false));
    await finish();
    assert.equal(overlay(), null);
    assert.equal(document.body.style.overflowY, overflow);
    assert.equal(closed, 1);
  } finally {
    await view.unmount();
  }
});
