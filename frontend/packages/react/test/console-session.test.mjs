import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act, type, key, click } from './helpers/tui-dom.mjs';
import { mount, ok } from './helpers/console-dom.mjs';

test('new session remains available when empty and cancels pending work without restoring late records', async () => {
  let resolve;
  let signal;
  const host = await mount((url, init) => {
    signal = init.signal;
    return new Promise((done) => {
      resolve = done;
    });
  });
  try {
    assert.equal(host.button('New session').disabled, false);
    await host.press('New session');
    await host.execute('ov health');
    assert.equal(host.input.disabled, true);
    assert.equal(host.button('New session').disabled, false);
    await host.press('New session');
    assert.equal(signal.aborted, true);
    assert.equal(host.input.disabled, false);
    assert.equal(host.input.value, '');
    assert.equal(host.records.length, 0);
    await act(() => resolve(ok({ stale: true })));
    assert.equal(host.records.length, 0);
    assert.doesNotMatch(host.text, /stale/);
  } finally {
    await host.dispose();
  }
});

test('scope, identity and new-session resets cannot restore a prior keyboard draft', async () => {
  const host = await mount(async () => ok({ state: 'ready' }));
  try {
    for (const reset of [
      () => host.render({ scopeKey: 'next' }),
      () => host.render({}, { userId: 'next-user' }),
      () => host.press('New session'),
      () => host.execute('ov clear'),
    ]) {
      await host.execute('ov status');
      await type(host.input, 'private previous draft');
      await key(host.input, 'ArrowUp');
      assert.equal(host.input.value, 'ov status');
      await reset();
      assert.equal(host.input.value, '');
      const status = [
        ...host.container.querySelectorAll('.ov-console__quick-action'),
      ].find((button) => button.textContent.includes('status'));
      await click(status);
      await key(host.input, 'Enter', { ctrlKey: true });
      await host.press('Command history');
      await click(
        document.querySelector('.ov-console__history-content button'),
      );
      await key(host.input, 'ArrowDown');
      assert.notEqual(host.input.value, 'private previous draft');
    }
  } finally {
    await host.dispose();
  }
});
