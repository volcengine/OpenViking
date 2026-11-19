import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { key } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

const member = 'workbench:staging:library-a:member-a';
const otherMember = 'workbench:staging:library-a:member-b';
const otherLibrary = 'workbench:staging:library-b:member-a';
const prefix = 'openviking.console.command-history.v3:';
const importCommand = 'ov add-resource https://example.com/readme.md';
const props = (commandHistoryKey) => ({
  modes: ['command'],
  commandHistoryKey,
});
const listedCommands = () =>
  [...document.querySelectorAll('.ov-console__history-command')].map(
    (item) => item.textContent,
  );

test('Console restores command recall for the same library and user after a reload', async (t) => {
  const storage = new JSDOM('', { url: 'https://workbench.test' }).window;
  t.after(() => storage.close());
  t.mock.getter(window, 'localStorage', () => storage.localStorage);
  const transport = t.mock.fn(async (url) => {
    if (url.endsWith('/api/v1/resources')) {
      return ok({
        task_id: 'import-task',
        root_uri: 'viking://resources/readme',
        status: 'success',
      });
    }
    if (url.endsWith('/api/v1/tasks/import-task')) {
      return ok({ task_id: 'import-task', status: 'running' });
    }
    return ok({ state: 'ready' });
  });
  const first = await mount(transport, props(member));
  try {
    await first.execute(importCommand);
    await first.execute('ov health');
    assert.equal(first.records.length, 2);
    assert.equal(first.records[0].dataset.status, 'pending');
  } finally {
    await first.dispose();
  }

  const reopened = await mount(transport, props(member));
  try {
    assert.equal(reopened.records.length, 0);
    await reopened.press('Command history');
    assert.deepEqual(listedCommands(), ['ov health', importCommand]);
    assert.match(
      document.querySelector('.ov-console__history-time').textContent,
      /\d{4}-\d{2}-\d{2} \d{2}:\d{2}/,
    );
    await reopened.press('Command history');
    await key(reopened.input, 'ArrowUp');
    assert.equal(reopened.input.value, 'ov health');
    await key(reopened.input, 'ArrowUp');
    assert.equal(reopened.input.value, importCommand);
    await key(reopened.input, 'ArrowDown');
    assert.equal(reopened.input.value, 'ov health');
    await key(reopened.input, 'ArrowDown');
    assert.equal(reopened.input.value, '');
    assert.equal(
      transport.mock.calls.filter(
        ({ arguments: [url] }) => !url.endsWith('/api/v1/tasks/import-task'),
      ).length,
      2,
    );

    await reopened.render(props(otherMember));
    await reopened.press('Command history');
    assert.deepEqual(listedCommands(), []);
    await reopened.render(props(otherLibrary));
    assert.deepEqual(listedCommands(), []);
    await reopened.render(props(member));
    assert.deepEqual(listedCommands(), ['ov health', importCommand]);
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

test('Console migrates old history for the active scope into local storage', async (t) => {
  const storage = new JSDOM('', { url: 'https://workbench.test' }).window;
  t.after(() => storage.close());
  t.mock.getter(window, 'localStorage', () => storage.localStorage);
  t.mock.getter(window, 'sessionStorage', () => storage.sessionStorage);

  const previous = 'openviking.console.command-history.v2:';
  const entry = { command: 'ov health', createdAtMs: 1000 };
  storage.sessionStorage.setItem(previous + member, JSON.stringify([entry]));
  storage.sessionStorage.setItem(
    previous + otherMember,
    JSON.stringify([entry]),
  );

  const view = await mount(async () => ok({}), props(member));
  try {
    await view.press('Command history');
    assert.deepEqual(listedCommands(), ['ov health']);
    assert.deepEqual(
      JSON.parse(storage.localStorage.getItem(prefix + member)),
      [entry],
    );
    assert.equal(storage.sessionStorage.getItem(previous + member), null);
    assert.equal(storage.localStorage.getItem(prefix + otherMember), null);
    assert.ok(storage.sessionStorage.getItem(previous + otherMember));
  } finally {
    await view.dispose();
  }
});

test('Console evicts the oldest entries in the current scope when storage is full', async (t) => {
  const storage = new JSDOM('', { url: 'https://workbench.test' }).window;
  t.after(() => storage.close());
  t.mock.getter(window, 'localStorage', () => storage.localStorage);
  const currentKey = prefix + member;
  const otherKey = prefix + otherMember;
  const otherHistory = JSON.stringify([
    { command: 'ov ls', createdAtMs: 1000 },
  ]);
  storage.localStorage.setItem(otherKey, otherHistory);
  const setItem = storage.Storage.prototype.setItem;
  t.mock.method(storage.Storage.prototype, 'setItem', function (key, value) {
    if (key === currentKey && value.length > 130) {
      throw new DOMException('Storage is full', 'QuotaExceededError');
    }
    return setItem.call(this, key, value);
  });

  const view = await mount(async () => ok({}), props(member));
  try {
    await view.execute('ov health');
    await view.execute('ov status');
    await view.execute('ov invalid-command');
    await view.press('Command history');
    assert.deepEqual(listedCommands(), ['ov invalid-command', 'ov status']);
    assert.deepEqual(
      JSON.parse(storage.localStorage.getItem(currentKey)).map(
        ({ command }) => command,
      ),
      ['ov status', 'ov invalid-command'],
    );
    assert.equal(storage.localStorage.getItem(otherKey), otherHistory);
    await view.press('Command history');
    await key(view.input, 'ArrowUp');
    assert.equal(view.input.value, 'ov invalid-command');
    await key(view.input, 'ArrowUp');
    assert.equal(view.input.value, 'ov status');
    await key(view.input, 'ArrowUp');
    assert.equal(view.input.value, 'ov status');
  } finally {
    await view.dispose();
  }
});
