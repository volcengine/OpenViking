import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { key } from './helpers/command-console-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

test('Consoles sharing a history key append without overwriting or restoring cleared entries', async (t) => {
  const windowWithStorage = new JSDOM('', {
    url: 'https://workbench.test',
  }).window;
  t.after(() => windowWithStorage.close());
  const storage = windowWithStorage.localStorage;
  t.mock.getter(window, 'localStorage', () => storage);

  const commandHistoryKey = 'workbench:staging:member-a';
  const storageKey =
    'openviking.console.command-history.v3:' + commandHistoryKey;
  const savedCommands = () =>
    JSON.parse(storage.getItem(storageKey) || '[]').map(
      ({ command }) => command,
    );
  const open = () =>
    mount(async () => ok({ state: 'ready' }), {
      modes: ['command'],
      commandHistoryKey,
    });

  const first = await open();
  const second = await open();
  try {
    await first.execute('ov health');
    await second.execute('ov invalid-command');
    assert.deepEqual(savedCommands(), ['ov health', 'ov invalid-command']);

    await second.execute('ov status');
    assert.deepEqual(savedCommands(), [
      'ov health',
      'ov invalid-command',
      'ov status',
    ]);

    await first.press('New session');
    assert.equal(storage.getItem(storageKey), null);

    await second.execute('ov help');
    assert.deepEqual(savedCommands(), ['ov help']);
  } finally {
    await second.dispose();
    await first.dispose();
  }

  const reopened = await open();
  try {
    await key(reopened.input, 'ArrowUp');
    assert.equal(reopened.input.value, 'ov help');
    await key(reopened.input, 'ArrowUp');
    assert.equal(reopened.input.value, 'ov help');
  } finally {
    await reopened.dispose();
  }
});
