import assert from 'node:assert/strict';
import { test } from 'node:test';
import { act } from './helpers/tui-dom.mjs';
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
