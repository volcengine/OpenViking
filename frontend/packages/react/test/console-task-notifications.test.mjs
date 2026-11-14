import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { ok, mount } from './helpers/console-dom.mjs';

for (const [status, type] of [
  ['completed', 'success'],
  ['failed', 'error'],
  ['cancelled', 'warning'],
]) {
  test(`task notification changes from loading to ${type} for ${status}`, async () => {
    let finish;
    const taskService = {
      getTask: () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    };
    const host = await mount(async () => ok({ task_id: 'task' }), {
      taskService,
    });
    try {
      await host.execute('ov add-resource https://example.com/resource');
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
      const card = document.querySelector('.ov-notification--loading');
      assert.ok(card);
      assert.equal(card.querySelectorAll('.ov-loading-icon').length, 1);
      assert.equal(card.getAttribute('aria-busy'), 'true');
      assert.equal(host.input.disabled, false);
      await act(() => finish({ id: 'task', status }));
      assert.equal(card.classList.contains(`ov-notification--${type}`), true);
      assert.equal(document.querySelectorAll('.ov-notification').length, 1);
      assert.equal(card.querySelectorAll('.ov-loading-icon').length, 0);
      assert.equal(card.getAttribute('aria-busy'), null);
    } finally {
      await host.dispose();
    }
  });
}
