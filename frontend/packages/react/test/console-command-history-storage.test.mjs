import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { key } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

const member = 'workbench:staging:library-a:member-a';
const otherMember = 'workbench:staging:library-a:member-b';
const prefix = 'openviking.console.command-history.v3:';
const props = (commandHistoryKey) => ({
  modes: ['command'],
  commandHistoryKey,
});
const listedCommands = () =>
  [...document.querySelectorAll('.ov-console__history-command')].map(
    (item) => item.textContent,
  );

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

test('Console keeps saved history when other local storage data leaves no room for the new entry', async (t) => {
  const quota = 320;
  const storage = new JSDOM('', {
    url: 'https://workbench.test',
    storageQuota: quota,
  }).window;
  t.after(() => storage.close());
  t.mock.getter(window, 'localStorage', () => storage.localStorage);
  const currentKey = prefix + member;
  const unrelatedKey = 'other-feature';
  const command = 'ov add-resource https://example.com/readme.md';
  const view = await mount(async () => ok({}), props(member));
  try {
    await view.execute('ov health');
    const saved = storage.localStorage.getItem(currentKey);
    const unrelated = 'x'.repeat(
      quota - currentKey.length - saved.length - unrelatedKey.length,
    );
    storage.localStorage.setItem(unrelatedKey, unrelated);

    await view.execute(command);
    assert.equal(view.records.length, 2);
    assert.equal(storage.localStorage.getItem(currentKey), saved);
    assert.equal(storage.localStorage.getItem(unrelatedKey), unrelated);
    await view.press('Command history');
    assert.deepEqual(listedCommands(), ['ov health']);
    await view.press('Command history');
    await key(view.input, 'ArrowUp');
    assert.equal(view.input.value, 'ov health');
  } finally {
    await view.dispose();
  }
});

test('Console keeps the displayed saved history if local storage becomes inaccessible', async (t) => {
  const storage = new JSDOM('', { url: 'https://workbench.test' }).window;
  t.after(() => storage.close());
  let blocked = false;
  t.mock.getter(window, 'localStorage', () => {
    if (blocked) throw new DOMException('Storage is blocked', 'SecurityError');
    return storage.localStorage;
  });
  const view = await mount(async () => ok({}), props(member));
  try {
    await view.execute('ov health');
    blocked = true;
    await view.execute('ov status');

    assert.equal(view.records.length, 2);
    await view.press('Command history');
    assert.deepEqual(listedCommands(), ['ov health']);
    await view.press('Command history');
    await key(view.input, 'ArrowUp');
    assert.equal(view.input.value, 'ov health');
  } finally {
    await view.dispose();
  }
});
