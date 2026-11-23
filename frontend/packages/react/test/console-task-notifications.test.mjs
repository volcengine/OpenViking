import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { ok, mount } from './helpers/console-dom.mjs';

const service = {
  createSession: async () => ({ id: 'demo', title: 'Demo' }),
  listSessions: async () => [],
  loadMessages: async () => [],
  async removeSession() {},
  async *stream() {},
};
const tick = () => act(() => new Promise((resolve) => setTimeout(resolve, 20)));

for (const [status, type] of [
  ['completed', 'success'],
  ['failed', 'error'],
  ['cancelled', 'warning'],
]) {
  test(`Bot tool task notification changes from loading to ${type} for ${status}`, async () => {
    let finish;
    const taskService = {
      getTask: () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    };
    const host = await mount(async () => ok({ task_id: 'task' }), {
      defaultMode: 'conversation',
      conversation: { composer: false },
      chatOptions: {
        service,
        initialMessages: [
          {
            id: 'tool-answer',
            role: 'assistant',
            parts: [
              {
                type: 'tool',
                id: 'skill-call',
                name: 'add-skill',
                status: 'completed',
                tasks: [{ id: 'task', kind: 'skill' }],
              },
            ],
          },
        ],
      },
      taskService,
    });
    try {
      await tick();
      const card = document.querySelector('.ov-notification--loading');
      assert.ok(card);
      assert.equal(card.querySelectorAll('.ov-loading-icon').length, 1);
      assert.equal(card.getAttribute('aria-busy'), 'true');
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
