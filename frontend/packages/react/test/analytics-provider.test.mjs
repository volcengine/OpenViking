import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, StrictMode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileTree } from '@openviking/react/components/file-tree';
import { mount } from './helpers/module-dom.mjs';

const row = {
  kind: 'entry',
  id: 'secret-id',
  depth: 0,
  name: 'secret-name',
  uri: 'viking://secret/path',
  type: 'file',
};
const view = (
  analytics,
  children = h(FileTree, { rows: [row], onSelect() {} }),
) => h(OpenVikingProvider, { analytics }, children);
const click = async (container) =>
  act(async () => container.querySelector('[role="treeitem"]').click());

test('default-off and explicit false do not initialize observers or operation IDs, including SSR', async () => {
  let observers = 0,
    identifiers = 0;
  const originalId = Object.getOwnPropertyDescriptor(crypto, 'randomUUID');
  Object.defineProperty(crypto, 'randomUUID', {
    configurable: true,
    value() {
      identifiers++;
      return 'unexpected';
    },
  });
  const original = window.IntersectionObserver;
  const originalMutation = window.MutationObserver;
  let styleObservers = 0;
  window.MutationObserver = class extends originalMutation {
    constructor(...args) {
      super(...args);
      styleObservers++;
    }
  };
  window.IntersectionObserver = class {
    constructor() {
      observers++;
    }
  };
  let calls = 0;
  const onEvent = () => calls++;
  assert.match(renderToStaticMarkup(view({ onEvent })), /secret-name/);
  const mounted = await mount(view(undefined));
  try {
    await click(mounted.container);
    await mounted.render(view({ onEvent }, view(false)));
    await click(mounted.container);
    assert.equal(observers, 0);
    assert.equal(styleObservers, 0);
    assert.equal(identifiers, 0);
    assert.equal(calls, 0);
  } finally {
    await mounted.unmount();
    window.IntersectionObserver = original;
    window.MutationObserver = originalMutation;
    if (originalId) Object.defineProperty(crypto, 'randomUUID', originalId);
    else delete crypto.randomUUID;
  }
});

test('nearest Provider inherits, replaces, or disables without double delivery', async () => {
  const outer = [],
    inner = [];
  const first = { onEvent: (event) => outer.push(event) };
  const second = { onEvent: (event) => inner.push(event) };
  const mounted = await mount(view(first, view(undefined)));
  try {
    await click(mounted.container);
    assert.equal(outer.length, 1);
    await mounted.render(view(first, view(second)));
    await click(mounted.container);
    assert.equal(outer.length, 1);
    assert.equal(inner.length, 1);
    await mounted.render(view(first, view(false)));
    await click(mounted.container);
    assert.equal(inner.length, 1);
    assert.deepEqual(outer[0].params, {});
    assert.equal(JSON.stringify(outer).includes('secret'), false);
  } finally {
    await mounted.unmount();
  }
});

test('filtering, sync exceptions, and async receiver rejections preserve interactions', async () => {
  let selected = 0;
  const content = h(FileTree, { rows: [row], onSelect: () => selected++ });
  const received = [];
  const mounted = await mount(
    view({ events: [], onEvent: (event) => received.push(event) }, content),
  );
  try {
    await click(mounted.container);
    assert.equal(received.length, 0);
    for (const receiver of [
      () => {
        throw new Error('offline');
      },
      () => Promise.reject(new Error('offline')),
    ]) {
      await mounted.render(
        view(
          { events: ['openviking_file_tree_entry_select'], onEvent: receiver },
          content,
        ),
      );
      await click(mounted.container);
    }
    assert.equal(selected, 3);
  } finally {
    await mounted.unmount();
  }
});

test('exposure waits for viewport and foreground, deduplicates Strict Mode rerenders, and cleans up', async () => {
  const observers = [];
  const originalObserver = window.IntersectionObserver;
  const originalRects = HTMLElement.prototype.getClientRects;
  let visibility = 'hidden';
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => visibility,
  });
  HTMLElement.prototype.getClientRects = () => [{ width: 100, height: 100 }];
  window.IntersectionObserver = class {
    targets = new Set();
    constructor(callback) {
      this.callback = callback;
      observers.push(this);
    }
    observe(target) {
      this.targets.add(target);
    }
    disconnect() {
      this.targets.clear();
    }
    takeRecords() {
      return [];
    }
    intersect(visible) {
      this.callback(
        [...this.targets].map((target) => ({
          target,
          isIntersecting: visible,
          intersectionRect: {
            width: visible ? 100 : 0,
            height: visible ? 100 : 0,
          },
        })),
      );
    }
  };
  const events = [];
  const analytics = { onEvent: (event) => events.push(event) };
  const render = () => h(StrictMode, null, view(analytics));
  const mounted = await mount(render());
  try {
    for (const observer of observers) observer.intersect(false);
    assert.equal(events.length, 0);
    for (const observer of observers) observer.intersect(true);
    assert.equal(events.length, 0);
    visibility = 'visible';
    document.dispatchEvent(new window.Event('visibilitychange'));
    assert.deepEqual(
      events.map((event) => event.name),
      ['openviking_file_tree_show'],
    );
    await mounted.render(render());
    for (const observer of observers) {
      observer.intersect(false);
      observer.intersect(true);
    }
    assert.equal(events.length, 1);
    await mounted.render(view(false));
    assert.ok(observers.every((observer) => observer.targets.size === 0));
  } finally {
    await mounted.unmount();
    window.IntersectionObserver = originalObserver;
    HTMLElement.prototype.getClientRects = originalRects;
    delete document.visibilityState;
  }
});
