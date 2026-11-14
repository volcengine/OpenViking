import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createReporter } from '../src/shared/analytics/reporter.ts';
const { createChatController } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-chat.js'))
);

for (const complete of [true, false]) {
  test(`reply ${complete ? 'completion' : 'truncation'} reports its actual outcome without message data`, async () => {
    const events = [];
    const reporter = createReporter(
      { onEvent: (event) => events.push(event) },
      { current: undefined },
    );
    const service = {
      createSession: async () => ({ id: 'private-session' }),
      loadMessages: async () => [],
      async *stream() {
        yield { type: 'text', text: 'private reply', delta: true };
        if (complete) yield { type: 'complete' };
      },
    };
    const controller = createChatController(
      service,
      { persist: false },
      undefined,
      { current: reporter },
    );
    const cleanup = controller.start();
    try {
      await controller.send('private input');
      assert.deepEqual(
        events.map((event) => event.name),
        [
          'openviking_viking_bot_message_submit',
          'openviking_viking_bot_reply_start',
          complete
            ? 'openviking_viking_bot_reply_success'
            : 'openviking_viking_bot_reply_fail',
        ],
      );
      assert.equal(
        new Set(events.map((event) => event.params.operation_id)).size,
        1,
      );
      assert.equal(JSON.stringify(events).includes('private'), false);
    } finally {
      cleanup();
    }
  });
}
