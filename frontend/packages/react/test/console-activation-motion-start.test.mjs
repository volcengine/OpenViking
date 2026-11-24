import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { mount } from './helpers/console-dom.mjs';
import {
  motionEnvironment,
  motionEvents,
  motionStartEvents,
} from './helpers/console-announcement-motion.mjs';

const unexpected = async () =>
  assert.fail('Unexpected network or start request');
const propsFor = (extra = {}) => ({
  locale: 'en-US',
  defaultMode: 'conversation',
  conversation: { messages: [], composer: false },
  chatOptions: {
    activation: {
      isEligible: async () => true,
      getStatus: async () => 'ready',
      start: unexpected,
    },
  },
  ...extra,
});

for (const event of motionStartEvents) {
  test(`${event} rechecks progress zero once before announcing without an end event`, async () => {
    const env = motionEnvironment();
    const host = await mount(unexpected, propsFor());
    try {
      const root = host.container.querySelector('.ov-console');
      const status = root.querySelector('.ov-console-activation__announcement');
      const unrelated = document.createElement('span');
      root.append(unrelated);
      await act(async () => {
        root.dataset.state = 'open';
      });
      await env.flush();
      assert.equal(status.textContent, '');
      await act(() => env.emit(unrelated, event));
      assert.equal(env.frames.size, 0);
      await act(() => {
        env.emit(root, event);
        env.emit(root, event);
        env.emit(root, event);
      });
      assert.equal(env.frames.size, 1);
      assert.equal(status.textContent, '');
      env.setVisible(true);
      await env.flush();
      assert.equal(status.textContent, '');
      assert.equal(env.frames.size, 1);
      await env.flush();
      assert.equal(status.textContent, 'VikingBot is ready.');
      await act(() =>
        motionStartEvents.forEach((type) => env.emit(root, type)),
      );
      assert.equal(env.frames.size, 1);
      await env.flush();
      assert.equal(env.frames.size, 0);
      assert.equal(status.textContent, 'VikingBot is ready.');
    } finally {
      await host.dispose();
      env.restore();
    }
  });
}

test('identity replacement and unmount cancel both the start recheck and announcement frames', async () => {
  const env = motionEnvironment();
  const scope = new AbortController();
  const props = propsFor({ requestOptions: { signal: scope.signal } });
  const host = await mount(unexpected, props);
  try {
    const root = host.container.querySelector('.ov-console');
    const status = root.querySelector('.ov-console-activation__announcement');
    await act(() => env.emit(root, 'transitionstart'));
    assert.equal(env.frames.size, 1);
    await host.render({ ...props, locale: 'zh-CN', scopeKey: 'replacement' });
    assert.equal(env.frames.size, 0);
    await act(() => env.emit(root, 'animationstart'));
    env.setVisible(true);
    await env.flush();
    assert.equal(env.frames.size, 1);
    assert.equal(status.textContent, '');
    await host.render({ ...props, locale: 'en-US', scopeKey: 'replacement' });
    await act(() => scope.abort());
    await env.flush();
    assert.equal(status.textContent, 'VikingBot status check cancelled.');
    assert.equal(env.listeners.size, motionEvents.length);
    await act(() => {
      env.setVisible(false);
      env.emit(root, 'transitioncancel');
    });
    await act(() => env.emit(root, 'animationstart'));
    assert.equal(env.frames.size, 1);
    await host.dispose();
    assert.equal(env.frames.size, 0);
    assert.equal(env.listeners.size, 0);
    await act(() => motionStartEvents.forEach((type) => env.emit(root, type)));
    await env.flush();
    assert.equal(status.textContent, '');
  } finally {
    await host.dispose();
    env.restore();
  }
});
