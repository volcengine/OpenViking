import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { key } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

const member = 'workbench:staging:member-a';
const otherMember = 'workbench:staging:member-b';
const props = (commandHistoryKey) => ({
  modes: ['command'],
  commandHistoryKey,
});
const listedCommands = () =>
  [...document.querySelectorAll('.ov-console__history-command')].map(
    (item) => item.textContent,
  );

test('Console restores command recall in the same tab and scope across mounts', async (t) => {
  const storage = new JSDOM('', { url: 'https://workbench.test' }).window;
  t.after(() => storage.close());
  t.mock.getter(window, 'sessionStorage', () => storage.sessionStorage);
  const transport = t.mock.fn(async () => ok({ state: 'ready' }));
  const first = await mount(transport, props(member));
  try {
    await first.execute('ov health');
    await first.execute('ov status');
    assert.equal(first.records.length, 2);
  } finally {
    await first.dispose();
  }

  const reopened = await mount(transport, props(member));
  try {
    assert.equal(reopened.records.length, 0);
    await reopened.press('Command history');
    assert.deepEqual(listedCommands(), ['ov status', 'ov health']);
    assert.match(
      document.querySelector('.ov-console__history-time').textContent,
      /\d{4}-\d{2}-\d{2} \d{2}:\d{2}/,
    );
    await reopened.press('Command history');
    await key(reopened.input, 'ArrowUp');
    assert.equal(reopened.input.value, 'ov status');
    await key(reopened.input, 'ArrowUp');
    assert.equal(reopened.input.value, 'ov health');
    await key(reopened.input, 'ArrowDown');
    assert.equal(reopened.input.value, 'ov status');
    await key(reopened.input, 'ArrowDown');
    assert.equal(reopened.input.value, '');
    assert.equal(transport.mock.calls.length, 2);

    await reopened.render(props(otherMember));
    await reopened.press('Command history');
    assert.deepEqual(listedCommands(), []);
    await reopened.render(props(member));
    assert.deepEqual(listedCommands(), ['ov status', 'ov health']);
    await reopened.press('New session');
  } finally {
    await reopened.dispose();
  }

  const cleared = await mount(transport, props(member));
  try {
    await cleared.press('Command history');
    assert.deepEqual(listedCommands(), []);
    await key(cleared.input, 'ArrowUp');
    assert.equal(cleared.input.value, '');
  } finally {
    await cleared.dispose();
  }
});

test('Console preserves long read-only commands and keeps failed write bodies out of storage', async (t) => {
  const storage = new JSDOM('', { url: 'https://workbench.test' }).window;
  t.after(() => storage.close());
  t.mock.getter(window, 'sessionStorage', () => storage.sessionStorage);

  const legacy = 'openviking.console.command-history.v1:';
  const saved = 'openviking.console.command-history.v2:';
  const longCommand = `ov ls viking://resources/${'a'.repeat(4097)}`;
  const failedWrite = 'ov add-memory "synthetic private body" --session-id s1';
  const malformedWrite = 'ov add-memory "synthetic unclosed body';
  const oldBody = 'ov add-memory "legacy private body" --session-id s1';
  const entry = (command) => ({ command, createdAtMs: 1000 });
  storage.sessionStorage.setItem(
    legacy + member,
    JSON.stringify([entry('ov health'), entry(oldBody)]),
  );
  storage.sessionStorage.setItem(
    legacy + otherMember,
    JSON.stringify([entry('ov status'), entry(oldBody)]),
  );

  const transport = t.mock.fn(async (_url, options) =>
    options.method === 'POST'
      ? Response.json(
          { status: 'error', error: { code: 'DENIED', message: 'Denied' } },
          { status: 403 },
        )
      : ok({ entries: [] }),
  );
  const first = await mount(transport, props(member));
  try {
    assert.equal(storage.sessionStorage.getItem(legacy + member), null);
    assert.equal(storage.sessionStorage.getItem(legacy + otherMember), null);
    assert.deepEqual(
      JSON.parse(storage.sessionStorage.getItem(saved + otherMember)),
      [entry('ov status')],
    );

    await first.execute(longCommand);
    await first.execute(failedWrite);
    assert.equal(first.records.at(-1).dataset.status, 'error');
    const writeRequest = transport.mock.calls.find(
      ({ arguments: [, options] }) => options.method === 'POST',
    );
    assert.match(writeRequest.arguments[1].body, /synthetic private body/);
    await first.execute(malformedWrite);

    await key(first.input, 'ArrowUp');
    assert.equal(first.input.value, malformedWrite);
    await key(first.input, 'ArrowUp');
    assert.equal(first.input.value, failedWrite);
    const persisted = JSON.parse(
      storage.sessionStorage.getItem(saved + member),
    );
    assert.deepEqual(
      persisted.map(({ command }) => command),
      ['ov health', longCommand],
    );
  } finally {
    await first.dispose();
  }

  const reopened = await mount(transport, props(member));
  try {
    await reopened.press('Command history');
    assert.deepEqual(listedCommands(), [longCommand, 'ov health']);
    await reopened.press('Command history');
    await key(reopened.input, 'ArrowUp');
    assert.equal(reopened.input.value, longCommand);
  } finally {
    await reopened.dispose();
  }
});
