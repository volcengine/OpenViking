import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { mount } from './helpers/console-dom.mjs';

const announcement = (host) =>
  host.container.querySelector('.ov-console-activation__announcement');
const unexpected = async () =>
  assert.fail('Unexpected network or start request');
const propsFor = (activation, extra = {}) => ({
  locale: 'en-US',
  defaultMode: 'conversation',
  conversation: { messages: [], composer: false },
  chatOptions: { activation },
  ...extra,
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

test('StrictMode commits an empty live region before checking and reannounces new requests', async () => {
  const frames = queuedFrames();
  const initialText = [];
  const queries = [];
  const activation = {
    async isEligible() {
      initialText.push(
        document.querySelector('.ov-console-activation__announcement')
          ?.textContent,
      );
      return true;
    },
    getStatus() {
      return new Promise((resolve) => queries.push(resolve));
    },
    start: unexpected,
  };
  const props = propsFor(activation);
  const host = await mount(unexpected, props);
  try {
    const status = announcement(host);
    assert.ok(status);
    assert.ok(initialText.length > 0);
    assert.ok(initialText.every((text) => text === ''));
    assert.equal(status.textContent, '');
    await frames.flush();
    assert.equal(status.textContent, 'Checking VikingBot status…');
    await act(() => queries.at(-1)('ready'));
    assert.equal(announcement(host), status);
    assert.equal(status.textContent, '');
    await frames.flush();
    assert.equal(status.textContent, 'VikingBot is ready.');
    await host.render({ ...props, scopeKey: 'second' });
    assert.equal(status.textContent, '');
    await frames.flush();
    assert.equal(status.textContent, 'Checking VikingBot status…');
    await host.render({ ...props, scopeKey: 'third' });
    assert.equal(announcement(host), status);
    assert.equal(status.textContent, '');
    await frames.flush();
    assert.equal(status.textContent, 'Checking VikingBot status…');
    assert.equal(status.closest('[aria-busy="true"]'), null);
  } finally {
    await host.dispose();
    frames.restore();
  }
});

test('opening Bot from CLI announces after exposure and locale changes replace pending text', async () => {
  const frames = queuedFrames();
  let reads = 0;
  const activation = {
    async isEligible() {
      reads++;
      return true;
    },
    getStatus: () => new Promise(() => {}),
    start: unexpected,
  };
  const props = propsFor(activation, { defaultMode: 'command' });
  const host = await mount(unexpected, props);
  try {
    const status = announcement(host);
    await frames.flush();
    assert.equal(status.textContent, '');
    assert.ok(status.closest('[hidden]'));
    await host.press('VikingBot');
    assert.equal(status.closest('[hidden]'), null);
    assert.equal(status.textContent, '');
    const requests = reads;
    await host.render({ ...props, locale: 'zh-CN' });
    await frames.flush();
    assert.equal(status.textContent, '正在检查 VikingBot 状态…');
    assert.equal(reads, requests);
    await host.render({ ...props, locale: 'en-US' });
    status.closest('.ov-console__panel').hidden = true;
    await frames.flush();
    assert.equal(status.textContent, '');
  } finally {
    await host.dispose();
    frames.restore();
  }
});

test('an initially aborted scope announces cancellation and unmount cancels pending frames', async () => {
  const frames = queuedFrames();
  const scope = new AbortController();
  scope.abort();
  const host = await mount(
    unexpected,
    propsFor(
      { isEligible: unexpected, getStatus: unexpected, start: unexpected },
      { requestOptions: { signal: scope.signal } },
    ),
  );
  try {
    const status = announcement(host);
    assert.equal(status.textContent, '');
    assert.equal(host.container.querySelector('.ov-loading-icon'), null);
    await frames.flush();
    assert.equal(status.textContent, 'VikingBot status check cancelled.');
    await host.render(
      propsFor(
        { isEligible: unexpected, getStatus: unexpected, start: unexpected },
        { requestOptions: { signal: scope.signal }, locale: 'zh-CN' },
      ),
    );
    assert.ok(frames.size > 0);
    await host.dispose();
    assert.equal(frames.size, 0);
    await frames.flush();
    assert.equal(status.textContent, '');
  } finally {
    await host.dispose();
    frames.restore();
  }
});
