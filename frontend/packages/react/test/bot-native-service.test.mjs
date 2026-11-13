import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const { createOvClient } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-client.js'))
);
const { createNativeBotService } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-service.js'))
);

test('native history combines archives and full active JSONL in order, deduplicates messages and restores activity groups', async () => {
  const paths = [];
  const message = (id) => ({
    id,
    role: 'assistant',
    parts: [
      { type: 'reasoning', reasoning: 'thought' },
      {
        type: 'tool',
        tool_id: id,
        tool_name: 'read',
        tool_status: 'completed',
      },
    ],
  });
  const client = createOvClient({
    baseURL: 'https://demo.invalid',
    fetch: async (url) => {
      const parsed = new URL(url);
      const path = parsed.pathname;
      paths.push(path);
      let result;
      if (path.endsWith('/content/read')) {
        assert.equal(parsed.searchParams.get('limit'), '-1');
        assert.equal(parsed.searchParams.get('raw'), 'true');
        const uri = parsed.searchParams.get('uri');
        result = (
          uri.includes('archive_001')
            ? [message('one')]
            : uri.includes('archive_002')
              ? [message('two')]
              : [message('two'), message('three')]
        )
          .map((item) => JSON.stringify(item))
          .join('\n');
      } else
        result = {
          commit_count: 2,
          uri: 'viking://user/demo/sessions/session',
        };
      return Response.json({ status: 'ok', result });
    },
  });
  const service = createNativeBotService(client, {});
  const messages = await service.loadMessages(
    'session/with slash',
    new AbortController().signal,
  );
  assert.deepEqual(
    messages.map((item) => item.id),
    ['one', 'two', 'three'],
  );
  assert.equal(messages[0].parts[0].groupId, messages[0].parts[1].groupId);
  assert.ok(paths.some((path) => path.includes('session%2Fwith%20slash')));
  assert.ok(!paths.some((path) => path.endsWith('/messages')));
});

test('native draft preserves references and image payload; transient reasoning is not persisted', async () => {
  const calls = [];
  const client = createOvClient({
    baseURL: 'https://demo.invalid',
    fetch: async (url, init) => {
      calls.push({ url, body: JSON.parse(init.body) });
      return url.endsWith('/stream')
        ? new Response('data: {"event":"response","data":"answer"}\n\n', {
            headers: { 'content-type': 'text/event-stream' },
          })
        : Response.json({ status: 'ok', result: {} });
    },
  });
  const service = createNativeBotService(client, {});
  for await (const event of service.stream(
    {
      sessionId: 's',
      text: 'question',
      attachments: [
        {
          key: 'image',
          name: 'image.png',
          mimeType: 'image/png',
          previewUrl: 'https://example.com/image.png',
        },
      ],
      references: [
        { uri: 'viking://resources/demo.md', name: 'demo.md', type: 'file' },
      ],
    },
    new AbortController().signal,
  ))
    assert.equal(event.type, 'complete');
  assert.equal(calls[0].body.message, 'question\n\nviking://resources/demo.md');
  assert.equal(
    calls[0].body.images[0].image_url.url,
    'https://example.com/image.png',
  );
  await service.saveMessage(
    's',
    {
      id: 'm',
      role: 'assistant',
      parts: [
        { type: 'reasoning', text: 'transient' },
        { type: 'text', text: 'answer' },
      ],
    },
    new AbortController().signal,
  );
  assert.deepEqual(calls[1].body.parts, [{ type: 'text', text: 'answer' }]);
});

test('unarchived history exceeding 128k tokens is restored without context budgeting', async () => {
  const large = 'word '.repeat(140000);
  const service = createNativeBotService(
    createOvClient({
      fetch: async (url) => {
        const parsed = new URL(url, 'https://demo.invalid');
        assert.ok(!parsed.pathname.endsWith('/context'));
        const result = parsed.pathname.endsWith('/content/read')
          ? JSON.stringify({ id: 'early', role: 'user', content: large }) +
            '\n' +
            JSON.stringify({
              id: 'late',
              role: 'assistant',
              content: 'complete',
            })
          : { commit_count: 0, uri: 'viking://user/demo/sessions/s' };
        return Response.json({ status: 'ok', result });
      },
    }),
    {},
  );
  const messages = await service.loadMessages(
    's',
    new AbortController().signal,
  );
  assert.equal(messages.length, 2);
  assert.equal(messages[0].parts[0].text, large);
});

test('empty live history allows a missing JSONL but preserves permission errors', async () => {
  let status = 404;
  const service = createNativeBotService(
    createOvClient({
      fetch: async (url) =>
        new URL(url, 'https://demo.invalid').pathname.endsWith('/content/read')
          ? Response.json(
              {
                status: 'error',
                error: {
                  code: status === 404 ? 'NOT_FOUND' : 'FORBIDDEN',
                  message: 'read failure',
                },
              },
              { status },
            )
          : Response.json({
              status: 'ok',
              result: { uri: 'viking://user/demo/sessions/s', commit_count: 0 },
            }),
    }),
    {},
  );
  const signal = new AbortController().signal;
  assert.deepEqual(await service.loadMessages('s', signal), []);
  status = 403;
  await assert.rejects(
    service.loadMessages('s', signal),
    (error) => error.statusCode === 403,
  );
});
