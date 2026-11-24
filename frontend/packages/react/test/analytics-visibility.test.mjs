import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileTree } from '@openviking/react/components/file-tree';
import { mount, settle } from './helpers/module-dom.mjs';
import { exposureObserver } from './helpers/exposure-observer.mjs';

async function setup(style, targetStyle) {
  const observer = exposureObserver();
  const events = [];
  const content = h(
    'div',
    { style },
    h(FileTree, {
      style: targetStyle,
      rows: [],
      onSelect() {},
    }),
  );
  const mounted = await mount(
    h(
      OpenVikingProvider,
      { analytics: { onEvent: (event) => events.push(event.name) } },
      content,
    ),
  );
  return {
    ...mounted,
    observer,
    events,
    owner: mounted.container.firstElementChild,
    disable: () =>
      mounted.render(h(OpenVikingProvider, { analytics: false }, content)),
    async cleanup() {
      await mounted.unmount();
      observer.restore();
    },
  };
}

test('explicitly visible target under a hidden ancestor exposes once', async () => {
  const mounted = await setup(
    { visibility: 'hidden' },
    { visibility: 'visible' },
  );
  try {
    assert.equal(window.getComputedStyle(mounted.owner).visibility, 'hidden');
    assert.equal(
      window.getComputedStyle(mounted.owner.firstElementChild).visibility,
      'visible',
    );
    await act(async () => mounted.observer.expose());
    assert.deepEqual(mounted.events, ['openviking_file_tree_show']);
    await act(async () => mounted.observer.expose());
    assert.equal(mounted.events.length, 1);
  } finally {
    await mounted.cleanup();
  }
});

test('zero-area edge contact followed by positive intersection exposes once', async () => {
  const mounted = await setup();
  try {
    await act(async () => mounted.observer.expose(0));
    assert.equal(mounted.events.length, 0);
    await act(async () => mounted.observer.expose(0.01));
    assert.deepEqual(mounted.events, ['openviking_file_tree_show']);
    mounted.observer.expose(0, false);
    mounted.observer.expose(1);
    assert.equal(mounted.events.length, 1);
  } finally {
    await mounted.cleanup();
  }
});

for (const [property, hidden, shown] of [
  ['opacity', '0', '1'],
  ['visibility', 'hidden', 'visible'],
]) {
  test(`ancestor ${property} becoming visible refreshes exposure and cleans up`, async () => {
    const mounted = await setup({ [property]: hidden });
    try {
      await act(async () => mounted.observer.expose());
      assert.equal(mounted.events.length, 0);
      mounted.owner.style[property] = shown;
      await settle();
      await act(async () => mounted.observer.expose());
      assert.deepEqual(mounted.events, ['openviking_file_tree_show']);
      assert.equal(mounted.observer.active, 0);
      mounted.owner.style[property] = hidden;
      await settle();
      mounted.observer.expose();
      assert.equal(mounted.observer.active, 0);
      assert.equal(mounted.events.length, 1);
    } finally {
      await mounted.cleanup();
    }
  });
}

test('closing analytics removes style listeners while an invisible target is pending', async () => {
  const mounted = await setup({ opacity: 0 });
  try {
    mounted.observer.expose();
    await mounted.disable();
    assert.equal(mounted.owner.isConnected, true);
    mounted.owner.style.opacity = '1';
    mounted.owner.dispatchEvent(
      new window.Event('transitionend', { bubbles: true }),
    );
    await settle();
    assert.equal(mounted.observer.active, 0);
    mounted.observer.expose();
    assert.equal(mounted.events.length, 0);
  } finally {
    await mounted.cleanup();
  }
});

test('CSS motion refreshes an intersecting target without an attribute change', async () => {
  const mounted = await setup();
  const originalStyle = window.getComputedStyle;
  let opacity = '0';
  window.getComputedStyle = (node) => {
    const style = originalStyle.call(window, node);
    return node === mounted.owner
      ? { display: style.display, visibility: style.visibility, opacity }
      : style;
  };
  try {
    mounted.observer.expose();
    assert.equal(mounted.events.length, 0);
    opacity = '1';
    mounted.owner.dispatchEvent(
      new window.Event('animationstart', { bubbles: true }),
    );
    mounted.observer.expose();
    assert.deepEqual(mounted.events, ['openviking_file_tree_show']);
    mounted.owner.dispatchEvent(
      new window.Event('animationend', { bubbles: true }),
    );
    mounted.observer.expose();
    assert.equal(mounted.events.length, 1);
    assert.equal(mounted.observer.active, 0);
  } finally {
    window.getComputedStyle = originalStyle;
    await mounted.cleanup();
  }
});
