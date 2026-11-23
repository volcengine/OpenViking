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
const messages = [
  {
    id: 'tool-answer',
    role: 'assistant',
    parts: [
      {
        type: 'tool',
        id: 'skill-call',
        name: 'add-skill',
        status: 'completed',
        tasks: [{ id: 'shared', kind: 'skill' }],
      },
    ],
  },
];
const tick = () => act(() => new Promise((resolve) => setTimeout(resolve, 20)));

for (const first of ['tool', 'command']) {
  test(`a shared task keeps its Bot notification when ${first} registers first`, async () => {
    const pending = [];
    const taskService = {
      getTask: (id) => new Promise((resolve) => pending.push({ id, resolve })),
    };
    const props = {
      taskService,
      conversation: { composer: false },
      chatOptions: {
        service,
        initialMessages: first === 'tool' ? messages : [],
      },
    };
    const host = await mount(async () => ok({ task_id: 'shared' }), props);
    try {
      await tick();
      await host.execute('ov add-skill https://example.com/skill');
      await tick();
      if (first === 'command') {
        assert.equal(document.querySelector('.ov-notification') === null, true);
        await host.render({
          ...props,
          chatOptions: { service, initialMessages: messages },
        });
        await tick();
      }
      assert.equal(pending.length, 1);
      assert.equal(pending[0].id, 'shared');
      assert.equal(host.records[0].dataset.status, 'pending');
      assert.equal(document.querySelectorAll('.ov-notification').length, 1);
      assert.ok(document.querySelector('.ov-notification--loading'));
      await act(() =>
        pending[0].resolve({ id: 'shared', status: 'completed' }),
      );
      assert.equal(host.records[0].dataset.status, 'success');
      assert.equal(document.querySelectorAll('.ov-notification').length, 1);
      assert.ok(document.querySelector('.ov-notification--success'));
      assert.equal(pending.length, 1);
    } finally {
      await host.dispose();
    }
  });
}
