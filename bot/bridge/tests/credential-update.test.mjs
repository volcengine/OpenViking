import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { createCredentialUpdateHandler } from '../dist/credential-update.js';

const tick = () => new Promise(resolve => setImmediate(resolve));

test('waits for a successful credential write', async () => {
  let writes = 0;
  const errors = [];
  const handler = createCredentialUpdateHandler(
    async () => {
      await tick();
      writes += 1;
    },
    message => errors.push(message),
  );

  await handler();

  assert.equal(writes, 1);
  assert.deepEqual(errors, []);
});

test('contains and reports a rejected credential write from the event listener', async () => {
  const failure = Object.assign(new Error('ENOSPC: no space left on device'), {
    code: 'ENOSPC',
  });
  const errors = [];
  const unhandled = [];
  const onUnhandled = reason => unhandled.push(reason);
  process.on('unhandledRejection', onUnhandled);

  try {
    const events = new EventEmitter();
    events.on(
      'creds.update',
      createCredentialUpdateHandler(
        async () => {
          throw failure;
        },
        message => errors.push(message),
      ),
    );

    assert.equal(events.emit('creds.update', {}), true);
    await tick();
    await tick();

    assert.deepEqual(errors, [
      'Failed to save WhatsApp credentials: ENOSPC: no space left on device',
    ]);
    assert.deepEqual(unhandled, []);
  } finally {
    process.off('unhandledRejection', onUnhandled);
  }
});
