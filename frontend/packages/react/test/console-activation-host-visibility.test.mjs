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
const mutate = (update) =>
  act(async () => {
    update();
    await Promise.resolve();
  });
function queuedFrames() {
  const request = window.requestAnimationFrame;
  const cancel = window.cancelAnimationFrame;
  const pending = new Map();
  let id = 0;
  window.requestAnimationFrame = (callback) => {
    pending.set(++id, callback);
    return id;
  };
  window.cancelAnimationFrame = (frame) => pending.delete(frame);
  return {
    get size() {
      return pending.size;
    },
    flush: () =>
      act(() => {
        const callbacks = [...pending.values()];
        pending.clear();
        callbacks.forEach((callback) => callback(0));
      }),
    restore() {
      window.requestAnimationFrame = request;
      window.cancelAnimationFrame = cancel;
    },
  };
}

const hiding = [
  ['hidden', (node) => (node.hidden = true), (node) => (node.hidden = false)],
  [
    'display',
    (node) => (node.style.display = 'none'),
    (node) => (node.style.display = ''),
  ],
  [
    'visibility',
    (node) => (node.style.visibility = 'hidden'),
    (node) => (node.style.visibility = ''),
  ],
  [
    'class',
    (node) => node.classList.add('host-hidden'),
    (node) => node.classList.remove('host-hidden'),
  ],
  [
    'inert',
    (node) => node.setAttribute('inert', ''),
    (node) => node.removeAttribute('inert'),
  ],
  [
    'aria-hidden',
    (node) => node.setAttribute('aria-hidden', 'true'),
    (node) => node.removeAttribute('aria-hidden'),
  ],
];

test('showing a public hidden Console announces its pending check without requesting again', async () => {
  const frames = queuedFrames();
  let reads = 0;
  const props = propsFor({
    async isEligible() {
      reads++;
      return true;
    },
    getStatus: () => new Promise(() => {}),
    start: unexpected,
  });
  const host = await mount(unexpected, { ...props, hidden: true });
  try {
    const status = host.container.querySelector(
      '.ov-console-activation__announcement',
    );
    const initialReads = reads;
    await frames.flush();
    assert.equal(status.textContent, '');
    await host.render({ ...props, hidden: false });
    await frames.flush();
    assert.equal(status.textContent, 'Checking VikingBot status…');
    assert.equal(reads, initialReads);
  } finally {
    await host.dispose();
    frames.restore();
  }
});

for (const [name, hide, show] of hiding) {
  test(`host ${name} hiding defers the first announcement until exposure without new requests`, async () => {
    const frames = queuedFrames();
    const style = document.createElement('style');
    style.textContent = '.host-hidden { display: none; }';
    document.head.append(style);
    let reads = 0;
    const host = await mount(
      unexpected,
      propsFor({
        async isEligible() {
          reads++;
          return true;
        },
        getStatus: () => new Promise(() => {}),
        start: unexpected,
      }),
    );
    try {
      const status = host.container.querySelector(
        '.ov-console-activation__announcement',
      );
      const initialReads = reads;
      await mutate(() => hide(host.container));
      await frames.flush();
      assert.equal(status.textContent, '');
      assert.equal(frames.size, 0);
      await mutate(() => show(host.container));
      assert.equal(status.textContent, '');
      await frames.flush();
      assert.equal(status.textContent, 'Checking VikingBot status…');
      assert.equal(reads, initialReads);
      await mutate(() => host.container.classList.add('unrelated'));
      assert.equal(frames.size, 0);
      assert.equal(status.textContent, 'Checking VikingBot status…');
    } finally {
      await host.dispose();
      style.remove();
      frames.restore();
    }
  });
}

test('hidden status and locale changes announce only the latest message and dispose pending work', async () => {
  const frames = queuedFrames();
  let complete;
  let reads = 0;
  const props = propsFor({
    async isEligible() {
      reads++;
      return true;
    },
    getStatus: () => new Promise((resolve) => (complete = resolve)),
    start: unexpected,
  });
  const host = await mount(unexpected, props);
  try {
    const status = host.container.querySelector(
      '.ov-console-activation__announcement',
    );
    const initialReads = reads;
    await mutate(() => (host.container.style.display = 'none'));
    await host.render({ ...props, locale: 'zh-CN' });
    await act(() => complete('ready'));
    await frames.flush();
    assert.equal(status.textContent, '');
    await mutate(() => (host.container.style.display = ''));
    await frames.flush();
    assert.equal(status.textContent, 'VikingBot 已就绪。');
    assert.equal(reads, initialReads);
    await mutate(() => (host.container.hidden = true));
    await host.render({ ...props, locale: 'zh-CN', scopeKey: 'replacement' });
    await mutate(() => (host.container.hidden = false));
    await frames.flush();
    assert.equal(status.textContent, '正在检查 VikingBot 状态…');
    await mutate(() => (host.container.hidden = true));
    await mutate(() => (host.container.hidden = false));
    assert.ok(frames.size > 0);
    await host.dispose();
    assert.equal(frames.size, 0);
    await mutate(() => (host.container.hidden = true));
    await mutate(() => (host.container.hidden = false));
    await frames.flush();
    assert.equal(status.textContent, '');
    assert.equal(frames.size, 0);
  } finally {
    await host.dispose();
    frames.restore();
  }
});
