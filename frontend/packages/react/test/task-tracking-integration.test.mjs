import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement as h, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { act } from './helpers/command-console-dom.mjs';
import { OpenVikingProvider, useConsole } from './helpers/internal-hooks.mjs';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
const internal = (name) =>
  import(pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, `${name}.js`)));
const { TaskTrackingProvider } = await internal('task-context');
const { useVikingBotChat } = await internal('bot-hook');
const tick = () => act(() => new Promise((resolve) => setTimeout(resolve, 15)));

test('Console terminal and Bot flows share queries, continue after replies and retain tasks across sessions', async () => {
  let terminal, bot;
  const pending = [];
  const taskService = {
    getTask: (id, signal) =>
      new Promise((resolve) => pending.push({ id, signal, resolve })),
  };
  const service = {
    createSession: async () => ({ id: 'session', title: 'Demo' }),
    loadMessages: async () => [],
    async *stream() {
      yield {
        type: 'tool',
        part: { type: 'tool', id: 'call', name: 'import', status: 'running' },
      };
      yield {
        type: 'tool-result',
        id: 'call',
        output: 'Request accepted',
        tasks: [{ id: 'shared', kind: 'resource' }],
      };
      yield { type: 'complete', text: 'Task submitted' };
    },
  };
  const fetch = async () =>
    Response.json({ status: 'ok', result: { task_id: 'shared' } });
  function Host({ locale }) {
    terminal = useConsole({ locale });
    bot = useVikingBotChat({ service, locale });
    return null;
  }
  const root = createRoot(document.createElement('div'));
  const render = (locale) =>
    act(() =>
      root.render(
        h(
          StrictMode,
          null,
          h(
            OpenVikingProvider,
            { fetch },
            h(
              TaskTrackingProvider,
              { service: taskService },
              h(Host, { locale }),
            ),
          ),
        ),
      ),
    );
  try {
    await render('en-US');
    await act(() =>
      terminal.execute('ov add-resource https://example.com/resource'),
    );
    await tick();
    assert.equal(terminal.isExecuting, false);
    assert.equal(terminal.records[0].status, 'pending');
    await act(() => bot.send('import'));
    await tick();
    assert.equal(pending.length, 1);
    assert.equal(bot.status, 'idle');
    assert.equal(
      bot.messages.at(-1).parts.find((part) => part.type === 'tool').tasks[0]
        .id,
      'shared',
    );
    const before = bot.messages
      .at(-1)
      .parts.find((part) => part.type === 'tool').output;
    await act(() => pending[0].resolve({ id: 'shared', status: 'completed' }));
    assert.equal(terminal.records[0].status, 'success');
    const part = bot.messages.at(-1).parts.find((part) => part.type === 'tool');
    assert.equal(part.tasks[0].status, 'completed');
    assert.equal(part.output, before);
    await render('zh-CN');
    assert.equal(pending.length, 1);
    await act(() => bot.selectSession('other'));
    assert.equal(terminal.records[0].tasks[0].status, 'completed');
    assert.equal(pending[0].signal.aborted, false);
  } finally {
    await act(() => root.unmount());
  }
});
