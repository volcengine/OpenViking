import assert from 'node:assert/strict';
import test from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';

test('Console renders only enabled modes and sends no disabled chat requests', async () => {
  let lists = 0;
  let history = 0;
  const service = {
    async listSessions() {
      lists++;
      return [];
    },
    async loadMessages() {
      history++;
      return [];
    },
    async createSession() {
      return { id: 's', title: 's' };
    },
    async removeSession() {},
    async *stream() {
      yield { type: 'complete', text: 'answer' };
    },
  };
  const props = {
    chatOptions: { service },
    // Avoid Ark's browser-only composer in jsdom.
    conversation: { composer: false, sessions: { currentId: 'past' } },
  };
  const changes = [];
  const host = await mount(async () => ok({ status: 'healthy' }), {
    ...props,
    modes: ['command'],
    mode: 'conversation',
    onModeChange: (mode) => changes.push(mode),
  });
  try {
    assert.equal(lists, 0);
    assert.equal(history, 0);
    assert.equal(host.container.querySelector('.ov-console__modes'), null);
    assert.equal(host.button('VikingBot'), undefined);
    assert.equal(host.container.querySelector('.ov-viking-bot'), null);
    assert.ok(host.input);
    await host.execute('ov health');
    assert.equal(host.records.length, 1);
    assert.deepEqual(changes, []);
    await host.render({ ...props, modes: ['conversation'] });
    assert.equal(host.container.querySelector('.ov-command-console'), null);
    assert.equal(host.container.querySelector('.ov-console__modes'), null);
    assert.equal(host.button('Terminal'), undefined);
    assert.ok(host.container.querySelector('.ov-viking-bot'));
    assert.ok(history > 0);
    await host.render({ ...props, modes: [] });
    assert.equal(host.container.querySelector('.ov-console__toolbar'), null);
    assert.equal(host.container.querySelector('.ov-console__panel'), null);
  } finally {
    await host.dispose();
  }
});
