import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { ok, mount } from './helpers/console-dom.mjs';

const tick = () => act(() => new Promise((resolve) => setTimeout(resolve, 20)));

test('Console tracks native tasks after submission, keeps input available and dismissing notice keeps tracking', async () => {
  let finish;
  const calls = [];
  const host = await mount(
    async (url, init) => {
      calls.push({
        url: new URL(url),
        signal: init.signal,
        headers: init.headers,
      });
      if (url.endsWith('/tasks/task-a'))
        return new Promise((resolve) => {
          finish = resolve;
        });
      return ok({
        task_id: 'task-a',
        status: 'success',
        root_uri: 'viking://resources/demo',
      });
    },
    { requestOptions: { userId: 'task-user' }, locale: 'zh-CN' },
  );
  try {
    await host.execute('ov add-resource https://example.com/resource');
    await tick();
    assert.equal(host.input.disabled, false);
    assert.equal(host.records[0].dataset.status, 'pending');
    assert.match(host.records[0].textContent, /任务已提交/);
    assert.equal(
      calls.filter((call) => call.url.pathname.endsWith('/tasks/task-a'))
        .length,
      1,
    );
    assert.equal(calls.at(-1).headers['x-openviking-user'], 'task-user');
    const close = document.querySelector('.ov-notification__close');
    await act(() => close.click());
    await act(() => new Promise((resolve) => setTimeout(resolve, 240)));
    await act(() =>
      finish(
        ok({ task_id: 'task-a', status: 'failed', error: 'Parsing failed' }),
      ),
    );
    assert.equal(host.records[0].dataset.status, 'error');
    assert.match(host.records[0].textContent, /Parsing failed/);
    assert.equal(document.querySelector('.ov-notification'), null);
  } finally {
    await host.dispose();
  }
});

test('Console custom task adapter is isolated and aborts on identity switch', async () => {
  const pending = [];
  const taskService = {
    getTask: (id, signal) =>
      new Promise((resolve) => pending.push({ id, signal, resolve })),
  };
  const props = { taskService, requestOptions: { userId: 'first' } };
  const host = await mount(async () => ok({ task_id: 'same' }), props);
  try {
    await host.execute('ov add-resource https://example.com/resource');
    await tick();
    assert.equal(pending.length, 1);
    await host.render({ ...props, requestOptions: { userId: 'second' } });
    assert.equal(pending[0].signal.aborted, true);
    await host.execute('ov add-resource https://example.com/next');
    await tick();
    assert.equal(pending.length, 2);
    await act(() =>
      pending[0].resolve({ id: 'same', status: 'failed', error: 'old scope' }),
    );
    assert.doesNotMatch(host.text, /old scope/);
    await act(() => pending[1].resolve({ id: 'same', status: 'completed' }));
    assert.equal(host.records[0].dataset.status, 'success');
  } finally {
    await host.dispose();
  }
});

test('dismissing a task stays hidden when its pending query fails during the exit animation', async () => {
  let resolveTask;
  let taskSignal;
  const taskService = {
    getTask: (id, signal) => {
      taskSignal = signal;
      return new Promise((resolve) => {
        resolveTask = resolve;
      });
    },
  };
  const host = await mount(async () => ok({ task_id: 'exit-race' }), {
    taskService,
    locale: 'en-US',
  });
  try {
    await host.execute('ov add-resource https://example.com/resource');
    await tick();
    assert.equal(typeof resolveTask, 'function');
    const close = document.querySelector(
      'button[aria-label="Close notification"]',
    );
    assert.ok(close);
    await act(() => close.click());
    assert.equal(taskSignal.aborted, false);
    await act(() =>
      resolveTask({
        id: 'exit-race',
        status: 'failed',
        error: 'Failed during exit',
      }),
    );
    assert.equal(host.records[0].dataset.status, 'error');
    assert.match(host.records[0].textContent, /Failed during exit/);
    await act(() => new Promise((resolve) => setTimeout(resolve, 260)));
    assert.equal(document.querySelectorAll('.ov-notification').length, 0);
    assert.equal(taskSignal.aborted, false);
  } finally {
    await host.dispose();
  }
});
