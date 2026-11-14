import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { ok, mount } from './helpers/console-dom.mjs';

for (const status of ['completed', 'failed', 'cancelled']) {
  test(`command acceptance and terminal ${status} are separate and correlate once`, async () => {
    const events = [];
    let finish;
    const taskService = {
      getTask: () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    };
    const analytics = { onEvent: (event) => events.push(event) };
    const props = { taskService };
    const host = await mount(
      async () => ok({ task_id: 'private-task', status: 'success' }),
      props,
      { analytics },
    );
    const operations = () =>
      events.filter((event) =>
        /^openviking_console_command_(submit|accepted|success|fail|cancel)$/.test(
          event.name,
        ),
      );
    try {
      await host.execute(
        'ov add-resource https://example.com/private-resource',
      );
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
      assert.deepEqual(
        operations().map((event) => event.name),
        [
          'openviking_console_command_submit',
          'openviking_console_command_accepted',
        ],
      );
      await act(() =>
        finish({
          id: 'private-task',
          status,
          error: status === 'failed' ? 'private failure' : undefined,
        }),
      );
      const suffix =
        status === 'completed'
          ? 'success'
          : status === 'failed'
            ? 'fail'
            : 'cancel';
      assert.deepEqual(
        operations().map((event) => event.name),
        [
          'openviking_console_command_submit',
          'openviking_console_command_accepted',
          `openviking_console_command_${suffix}`,
        ],
      );
      assert.equal(
        new Set(operations().map((event) => event.params.operation_id)).size,
        1,
      );
      await host.render({ ...props, locale: 'zh-CN' }, { analytics });
      assert.equal(operations().length, 3);
      assert.equal(JSON.stringify(events).includes('private'), false);
    } finally {
      await host.dispose();
    }
  });
}
