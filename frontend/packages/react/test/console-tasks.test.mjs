import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { ok, mount } from './helpers/console-dom.mjs';

const tick = () => act(() => new Promise((resolve) => setTimeout(resolve, 20)));

for (const [command, status, recordStatus] of [
  ['ov add-skill https://example.com/skill', 'failed', 'error'],
  ['ov add-memory "Remember this" --session-id demo', 'completed', 'success'],
  ['ov session commit demo', 'cancelled', 'cancelled'],
  ['ov compile --compile-name demo', 'completed', 'success'],
]) {
  test(`Console keeps ${command} task state inline without notifications`, async () => {
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
      await host.execute(command);
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
      assert.ok(document.querySelector('.ov-notification') === null);
      assert.ok(document.querySelector('.ov-toast') === null);
      await act(() =>
        finish(
          ok({
            task_id: 'task-a',
            status,
            ...(status === 'failed' ? { error: 'Parsing failed' } : {}),
          }),
        ),
      );
      assert.equal(host.records[0].dataset.status, recordStatus);
      assert.ok(
        host.records[0].querySelector(`[data-task-status="${status}"]`),
      );
      if (status === 'failed')
        assert.match(host.records[0].textContent, /Parsing failed/);
      assert.ok(document.querySelector('.ov-notification') === null);
      assert.ok(document.querySelector('.ov-toast') === null);
    } finally {
      await host.dispose();
    }
  });
}

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

test('a completed resource import keeps its output and opens the final URI without a notification', async () => {
  const rootUri = 'viking://resources/demo';
  const finalUri = 'viking://resources/demo-2';
  const opened = [];
  let finish;
  const host = await mount(
    async (url) =>
      url.endsWith('/tasks/resource-task')
        ? new Promise((resolve) => {
            finish = resolve;
          })
        : ok({
            task_id: 'resource-task',
            root_uri: rootUri,
            status: 'success',
          }),
    {
      locale: 'zh-CN',
      onOpenUri: () => assert.fail('The command callback takes precedence'),
      command: { onOpenUri: (uri) => opened.push(uri) },
    },
  );
  try {
    await host.execute('ov add-resource https://example.com/resource');
    await tick();
    assert.equal(host.records[0].dataset.status, 'pending');
    assert.ok(document.querySelector('.ov-notification') === null);
    await act(() =>
      finish(
        ok({
          task_id: 'resource-task',
          status: 'completed',
          stage: 'completed',
          result: { root_uri: finalUri },
        }),
      ),
    );
    const record = host.records[0];
    assert.equal(record.dataset.status, 'success');
    assert.ok(record.querySelector('.ov-task-status') === null);
    assert.deepEqual(
      [...record.querySelectorAll('.ov-command-console__write-line')].map(
        (line) => line.textContent,
      ),
      [
        'Task id: resource-task',
        `Root uri: ${rootUri}`,
        `任务结果: ${finalUri}`,
      ],
    );
    const link = record.querySelector(`a[href="${rootUri}"]`);
    assert.equal(link.textContent, rootUri);
    await act(() => link.click());
    assert.deepEqual(opened, [rootUri]);
    const finalLink = record.querySelector(`a[href="${finalUri}"]`);
    assert.ok(finalLink);
    await act(() => finalLink.click());
    assert.deepEqual(opened, [rootUri, finalUri]);
    assert.ok(document.querySelector('.ov-notification') === null);
  } finally {
    await host.dispose();
  }
});

test('CLI task query failures stay inline without cancelling tracking', async () => {
  let rejectTask;
  let taskSignal;
  const taskService = {
    getTask: (id, signal) => {
      taskSignal = signal;
      return new Promise((_, reject) => {
        rejectTask = reject;
      });
    },
  };
  const host = await mount(async () => ok({ task_id: 'query-failure' }), {
    taskService,
    locale: 'en-US',
  });
  try {
    await host.execute('ov add-skill https://example.com/skill');
    await tick();
    assert.equal(typeof rejectTask, 'function');
    assert.ok(document.querySelector('.ov-notification') === null);
    assert.equal(taskSignal.aborted, false);
    await act(() => rejectTask(new Error('Task query unavailable')));
    await tick();
    assert.equal(host.records[0].dataset.status, 'pending');
    assert.match(host.records[0].textContent, /Task query unavailable/);
    assert.equal(document.querySelectorAll('.ov-notification').length, 0);
    assert.ok(document.querySelector('.ov-toast') === null);
    assert.equal(taskSignal.aborted, false);
  } finally {
    await host.dispose();
  }
});
