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
