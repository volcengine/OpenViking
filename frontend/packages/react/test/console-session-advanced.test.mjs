import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';

test('Console forwards advanced session arguments with the CLI session routes', async () => {
  const calls = [];
  const parts = [{ type: 'text', text: 'Remember the wiki scope' }];
  const createdAt = '2026-11-18T00:00:00Z';
  const messages = [{ role: 'user', parts, created_at: createdAt }];
  const host = await mount(async (url, init) => {
    const { pathname, search } = new URL(url);
    calls.push([
      init.method,
      pathname + search,
      init.body && JSON.parse(init.body),
    ]);
    return ok({ session_id: 'review', message_count: 1 });
  });

  try {
    await host.execute(
      `ov session new --id review --memory-policy '{"working_memory":true}' --telemetry`,
    );
    await host.execute('ov session get review --auto-create');
    await host.execute(
      'ov session get-session-context review --token-budget 9',
    );
    await host.execute(
      `ov session add-message review --role user --parts '${JSON.stringify(parts)}' --created-at ${createdAt}`,
    );
    await host.execute(
      `ov session add-messages review --messages '${JSON.stringify(messages)}' --telemetry`,
    );
    await host.execute(
      'ov session commit review --keep-recent-count 5 --no-event-tags',
    );
    await host.execute("ov session add-messages review --messages '[{}]'");
    assert.deepEqual(calls, [
      [
        'POST',
        '/gateway/api/v1/sessions',
        {
          session_id: 'review',
          memory_policy: { working_memory: true },
          telemetry: true,
        },
      ],
      ['GET', '/gateway/api/v1/sessions/review?auto_create=true', undefined],
      [
        'GET',
        '/gateway/api/v1/sessions/review/context?token_budget=9',
        undefined,
      ],
      [
        'POST',
        '/gateway/api/v1/sessions/review/messages',
        { role: 'user', parts, created_at: createdAt },
      ],
      [
        'POST',
        '/gateway/api/v1/sessions/review/messages/batch',
        { messages, telemetry: true },
      ],
      [
        'POST',
        '/gateway/api/v1/sessions/review/commit',
        { keep_recent_count: 5, extraction_metadata: { event: { tags: [] } } },
      ],
    ]);
    assert.equal(host.records.at(-1).dataset.status, 'error');
  } finally {
    await host.dispose();
  }
});
