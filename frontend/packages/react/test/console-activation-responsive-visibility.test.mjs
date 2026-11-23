import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { mount } from './helpers/console-dom.mjs';

const unexpected = async () =>
  assert.fail('Unexpected network or start request');
const propsFor = (activation) => ({
  locale: 'en-US',
  defaultMode: 'conversation',
  conversation: { messages: [], composer: false },
  chatOptions: { activation },
});
function environment(responsive = false) {
  const original = {
    request: window.requestAnimationFrame,
    cancel: window.cancelAnimationFrame,
    resize: window.ResizeObserver,
    computed: window.getComputedStyle,
    viewport: Object.getOwnPropertyDescriptor(window, 'visualViewport'),
  };
  const frames = new Map();
  const observers = [];
  const viewport = new window.EventTarget();
  let id = 0;
  let visible = !responsive;
  window.requestAnimationFrame = (callback) => {
    frames.set(++id, callback);
    return id;
  };
  window.cancelAnimationFrame = (frame) => frames.delete(frame);
  window.ResizeObserver = class {
    nodes = new Set();
    constructor(callback) {
      this.callback = callback;
      observers.push(this);
    }
    observe(node) {
      this.nodes.add(node);
    }
    disconnect() {
      this.nodes.clear();
    }
  };
  Object.defineProperty(window, 'visualViewport', {
    configurable: true,
    value: viewport,
  });
  // jsdom does not evaluate viewport/container media queries. Change computed
  // display without touching DOM attributes, then deliver the browser's signal.
  window.getComputedStyle = (node, pseudo) => {
    const style = original.computed.call(window, node, pseudo);
    return responsive && node.classList.contains('ov-console')
      ? new Proxy(style, {
          get: (target, key) =>
            key === 'display'
              ? visible
                ? 'block'
                : 'none'
              : Reflect.get(target, key, target),
        })
      : style;
  };
  return {
    observers,
    frames,
    setVisible: (next) => (visible = next),
    signal(kind) {
      if (kind === 'observer')
        observers.forEach((observer) => observer.callback([]));
      else
        (kind === 'window' ? window : viewport).dispatchEvent(
          new window.Event('resize'),
        );
    },
    flush: () =>
      act(() => {
        const callbacks = [...frames.values()];
        frames.clear();
        callbacks.forEach((callback) => callback(0));
      }),
    restore() {
      window.requestAnimationFrame = original.request;
      window.cancelAnimationFrame = original.cancel;
      window.ResizeObserver = original.resize;
      window.getComputedStyle = original.computed;
      if (original.viewport)
        Object.defineProperty(window, 'visualViewport', original.viewport);
      else delete window.visualViewport;
    },
  };
}

test('an ancestor data-state change resumes the current ready announcement without requesting', async () => {
  const env = environment();
  const style = document.createElement('style');
  style.textContent = '[data-state="closed"] { display: none; }';
  document.head.append(style);
  let reads = 0;
  const host = await mount(
    unexpected,
    propsFor({
      async isEligible() {
        reads++;
        return true;
      },
      getStatus: async () => 'ready',
      start: unexpected,
    }),
  );
  try {
    const status = host.container.querySelector(
      '.ov-console-activation__announcement',
    );
    const initialReads = reads;
    await act(async () => {
      host.container.dataset.state = 'closed';
    });
    await env.flush();
    assert.equal(status.textContent, '');
    await act(async () => {
      host.container.dataset.state = 'open';
    });
    await env.flush();
    assert.equal(status.textContent, 'VikingBot is ready.');
    assert.equal(reads, initialReads);
  } finally {
    await host.dispose();
    style.remove();
    env.restore();
  }
});

for (const signal of ['observer', 'window', 'visualViewport']) {
  test(`${signal} layout changes announce the latest hidden state and clean subscriptions`, async () => {
    const env = environment(true);
    let reads = 0;
    const props = propsFor({
      async isEligible() {
        reads++;
        return true;
      },
      getStatus: async () => 'ready',
      start: unexpected,
    });
    const host = await mount(unexpected, props);
    try {
      const status = host.container.querySelector(
        '.ov-console-activation__announcement',
      );
      const root = host.container.querySelector('.ov-console');
      const initialReads = reads;
      await env.flush();
      assert.equal(status.textContent, '');
      assert.ok(env.observers.some((observer) => observer.nodes.has(root)));
      await host.render({ ...props, locale: 'zh-CN' });
      await act(() => {
        env.setVisible(true);
        env.signal(signal);
      });
      await env.flush();
      assert.equal(status.textContent, 'VikingBot 已就绪。');
      assert.equal(reads, initialReads);
      await act(() => {
        env.setVisible(false);
        env.signal(signal);
      });
      assert.equal(status.textContent, '');
      await host.render({ ...props, locale: 'en-US' });
      await act(() => {
        env.setVisible(true);
        env.signal(signal);
      });
      assert.ok(env.frames.size > 0);
      await host.dispose();
      assert.equal(env.frames.size, 0);
      assert.ok(env.observers.every((observer) => observer.nodes.size === 0));
      await act(() => env.signal(signal));
      await env.flush();
      assert.equal(status.textContent, '');
    } finally {
      await host.dispose();
      env.restore();
    }
  });
}
