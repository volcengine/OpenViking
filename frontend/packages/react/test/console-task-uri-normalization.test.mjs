import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { ok, mount } from './helpers/console-dom.mjs';

const target = 'viking://resources/demo';
for (const [submitted, completed] of [
  [`${target} `, target],
  [target, ` ${target} `],
  [`${target} `, undefined],
]) {
  test(`structured result URIs deduplicate their rendered href (${JSON.stringify([submitted, completed])})`, async () => {
    let finish;
    const opened = [];
    const host = await mount(
      async () =>
        ok({
          task_id: 'trimmed-task',
          status: 'accepted',
          root_uri: submitted,
        }),
      {
        onOpenUri: (uri) => opened.push(uri),
        taskService: {
          getTask: () => new Promise((resolve) => (finish = resolve)),
        },
      },
    );
    try {
      await host.execute('ov add-resource https://example.com/resource');
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
      const links = () =>
        host.records[0].querySelectorAll(`a[href="${target}"]`);
      assert.equal(links().length, 1);
      await act(() =>
        finish({ id: 'trimmed-task', status: 'completed', uri: completed }),
      );
      assert.equal(host.records[0].dataset.status, 'success');
      assert.equal(links().length, 1);
      assert.ok(host.records[0].textContent.includes(`Root uri: ${submitted}`));
      await act(() => links()[0].click());
      assert.deepEqual(opened, [target]);
      assert.ok(document.querySelector('.ov-notification') === null);
    } finally {
      await host.dispose();
    }
  });
}
