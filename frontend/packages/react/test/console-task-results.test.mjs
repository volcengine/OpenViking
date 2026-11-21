import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { ok, mount } from './helpers/console-dom.mjs';

const submittedUri = 'viking://resources/submitted';
const finalUri = 'viking://resources/final';
const tick = () => act(() => new Promise((resolve) => setTimeout(resolve, 20)));

async function submit(command, props = {}, uri = finalUri) {
  let finish;
  const taskService = {
    getTask: () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  };
  const options = { locale: 'en-US', ...props, taskService };
  const host = await mount(
    async () =>
      ok({
        task_id: 'result-task',
        status: 'accepted',
        root_uri: submittedUri,
      }),
    options,
  );
  try {
    await host.execute(command);
    await tick();
    assert.equal(host.records[0].dataset.status, 'pending');
    assert.equal(
      host.records[0].querySelector(`a[href="${finalUri}"]`) === null,
      true,
    );
    await act(() =>
      finish({ id: 'result-task', status: 'completed', ...(uri && { uri }) }),
    );
    return { host, options };
  } catch (error) {
    await host.dispose();
    throw error;
  }
}

for (const command of [
  'ov add-skill https://example.com/skill',
  'ov add-memory "Remember this" --session-id demo',
  'ov session commit demo',
  'ov compile --compile-name demo',
]) {
  test(`${command} exposes its final task URI through the Console callback`, async () => {
    const opened = [];
    const { host, options } = await submit(command, {
      onOpenUri: (uri) => opened.push(uri),
    });
    try {
      const record = host.records[0];
      assert.match(record.textContent, /Task id: result-task/);
      const link = record.querySelector(`a[href="${finalUri}"]`);
      assert.ok(link);
      assert.equal(link.parentElement.textContent, `Task result: ${finalUri}`);
      await act(() => link.click());
      assert.deepEqual(opened, [finalUri]);
      assert.equal(document.querySelector('.ov-notification') === null, true);
      await host.render({ ...options, locale: 'zh-CN' });
      assert.equal(
        host.records[0].querySelector(`a[href="${finalUri}"]`).parentElement
          .textContent,
        `任务结果: ${finalUri}`,
      );
      assert.equal(document.querySelector('.ov-notification') === null, true);
    } finally {
      await host.dispose();
    }
  });
}

for (const uri of [submittedUri, null]) {
  test(`completed tasks do not duplicate their submission URI (${uri ?? 'fallback'})`, async () => {
    const { host } = await submit(
      'ov add-resource https://example.com/resource',
      {},
      uri,
    );
    try {
      assert.equal(
        host.records[0].querySelectorAll(`a[href="${submittedUri}"]`).length,
        1,
      );
      assert.equal(host.records[0].textContent.includes('Task result:'), false);
      assert.equal(document.querySelector('.ov-notification') === null, true);
    } finally {
      await host.dispose();
    }
  });
}

test('final task links use native navigation when neither URI callback is configured', async () => {
  const { host } = await submit('ov add-resource https://example.com/resource');
  try {
    const link = host.records[0].querySelector(`a[href="${finalUri}"]`);
    assert.ok(link);
    let prevented;
    host.container.addEventListener('click', (event) => {
      prevented = event.defaultPrevented;
      event.preventDefault();
    });
    await act(() =>
      link.dispatchEvent(
        new window.MouseEvent('click', {
          bubbles: true,
          cancelable: true,
        }),
      ),
    );
    assert.equal(prevented, false);
    assert.equal(document.querySelector('.ov-notification') === null, true);
  } finally {
    await host.dispose();
  }
});

test('a final URI already linked inside nested command output is not repeated', async () => {
  let finish;
  const opened = [];
  const host = await mount(
    async () =>
      ok({
        task_id: 'nested-task',
        status: 'accepted',
        result: { root_uri: finalUri },
      }),
    {
      onOpenUri: (uri) => opened.push(uri),
      taskService: {
        getTask: () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      },
    },
  );
  try {
    await host.execute('ov add-resource https://example.com/resource');
    await tick();
    const links = () =>
      host.records[0].querySelectorAll(`a[href="${finalUri}"]`);
    assert.equal(links().length, 1);
    await act(() =>
      finish({ id: 'nested-task', status: 'completed', uri: finalUri }),
    );
    assert.equal(links().length, 1);
    await act(() => links()[0].click());
    assert.deepEqual(opened, [finalUri]);
    assert.equal(host.records[0].textContent.includes('Task result:'), false);
    assert.ok(document.querySelector('.ov-notification') === null);
  } finally {
    await host.dispose();
  }
});
