import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount, ok } from './helpers/console-dom.mjs';

test('Console sends serialized session commands to the scoped routes', async () => {
  const calls = [];
  const messages = [{ role: 'user', content: 'Remember the wiki scope' }];
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
    await host.execute('ov session new --id review');
    await host.execute(
      'ov session get-session-context review --token-budget 9',
    );
    await host.execute(
      `ov session add-messages review --messages '${JSON.stringify(messages)}'`,
    );
    await host.execute("ov session add-messages review --messages '{}'");
    assert.deepEqual(calls, [
      ['POST', '/gateway/api/v1/sessions', { session_id: 'review' }],
      [
        'GET',
        '/gateway/api/v1/sessions/review/context?token_budget=9',
        undefined,
      ],
      ['POST', '/gateway/api/v1/sessions/review/messages/batch', { messages }],
    ]);
    assert.match(host.records.at(-1).textContent, /Invalid messages/);
  } finally {
    await host.dispose();
  }
});

test('Console downloads file and pack responses using browser filenames', async () => {
  const calls = [];
  const downloads = [];
  const originalClick = window.HTMLAnchorElement.prototype.click;
  window.HTMLAnchorElement.prototype.click = function () {
    downloads.push([this.protocol, this.download]);
  };
  const host = await mount(async (url, init) => {
    const { pathname, searchParams } = new URL(url);
    calls.push([
      init.method,
      pathname,
      searchParams.get('uri'),
      init.body && JSON.parse(init.body),
    ]);
    return new Response('download');
  });

  try {
    const uri = 'viking://resources/demo.md';
    await host.execute(`ov get ${uri} 'folder/output name.md'`);
    await host.execute(`ov export ${uri} folder/export --include-vectors`);
    await host.execute('ov backup folder/backup.ovpack');
    assert.deepEqual(calls, [
      ['GET', '/gateway/api/v1/content/download', uri, undefined],
      [
        'POST',
        '/gateway/api/v1/pack/export',
        null,
        { uri, include_vectors: true },
      ],
      ['POST', '/gateway/api/v1/pack/backup', null, { include_vectors: false }],
    ]);
    assert.deepEqual(downloads, [
      ['blob:', 'output name.md'],
      ['blob:', 'export.ovpack'],
      ['blob:', 'backup.ovpack'],
    ]);
    assert.match(host.records.at(-1).textContent, /Downloaded backup\.ovpack/);
  } finally {
    window.HTMLAnchorElement.prototype.click = originalClick;
    await host.dispose();
  }
});
