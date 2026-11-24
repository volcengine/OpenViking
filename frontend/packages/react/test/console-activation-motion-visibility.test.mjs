import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { mount } from './helpers/console-dom.mjs';
import {
  motionEnvironment,
  motionEvents,
  settledMotionEvents,
} from './helpers/console-announcement-motion.mjs';

const unexpected = async () =>
  assert.fail('Unexpected network or start request');
const propsFor = (activation, extra = {}) => ({
  locale: 'en-US',
  defaultMode: 'conversation',
  conversation: { messages: [], composer: false },
  chatOptions: { activation },
  ...extra,
});
for (const event of settledMotionEvents) {
  test(`${event} announces delayed visibility once and ignores unrelated descendants`, async () => {
    const env = motionEnvironment();
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
      const root = host.container.querySelector('.ov-console');
      const status = root.querySelector('.ov-console-activation__announcement');
      const unrelated = document.createElement('span');
      root.append(unrelated);
      const initialReads = reads;
      await act(async () => {
        root.dataset.state = 'open';
      });
      await env.flush();
      assert.equal(status.textContent, '');
      await act(() => {
        env.setVisible(true);
        env.emit(unrelated, event);
      });
      assert.equal(env.frames.size, 0);
      await act(() => env.emit(root, event));
      assert.equal(status.textContent, '');
      await env.flush();
      assert.equal(status.textContent, 'VikingBot is ready.');
      await act(() => {
        settledMotionEvents.forEach((type) => env.emit(root, type));
      });
      assert.equal(env.frames.size, 0);
      assert.equal(reads, initialReads);
    } finally {
      await host.dispose();
      assert.equal(env.listeners.size, 0);
      env.restore();
    }
  });
}

test('identity, locale and cancellation replace pending transition text and dispose listeners', async () => {
  const env = motionEnvironment();
  const scope = new AbortController();
  const props = propsFor(
    {
      isEligible: async () => true,
      getStatus: async () => 'ready',
      start: unexpected,
    },
    { requestOptions: { signal: scope.signal } },
  );
  const host = await mount(unexpected, props);
  try {
    const root = host.container.querySelector('.ov-console');
    const status = root.querySelector('.ov-console-activation__announcement');
    await act(() => {
      env.setVisible(true);
      env.emit(root, 'transitionend');
    });
    assert.ok(env.frames.size > 0);
    await host.render({ ...props, locale: 'zh-CN', scopeKey: 'replacement' });
    await act(() => scope.abort());
    await env.flush();
    assert.equal(status.textContent, 'VikingBot 状态检查已取消。');
    assert.equal(env.listeners.size, motionEvents.length);
    await act(() => {
      env.setVisible(false);
      env.emit(root, 'transitioncancel');
    });
    assert.equal(status.textContent, '');
    await act(() => {
      env.setVisible(true);
      env.emit(root, 'animationend');
    });
    assert.ok(env.frames.size > 0);
    await host.dispose();
    assert.equal(env.frames.size, 0);
    assert.equal(env.listeners.size, 0);
    await act(() => {
      motionEvents.forEach((type) => env.emit(root, type));
    });
    await env.flush();
    assert.equal(status.textContent, '');
  } finally {
    await host.dispose();
    env.restore();
  }
});
