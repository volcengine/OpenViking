import assert from 'node:assert/strict';
import test from 'node:test';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createOvClient } from '@openviking/react/client';
const { createNativeBotService } = await import(
  pathToFileURL(join(process.env.OV_UI_TEST_HOOKS, 'bot-service.js'))
);

test('native history combines archives and context in order, deduplicates messages and restores activity groups', async () => {
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
      const path = new URL(url).pathname;
      paths.push(path);
      const result = path.endsWith('/context')
        ? { messages: [message('two'), message('three')] }
        : path.endsWith('archive_001')
          ? { messages: [message('one')] }
          : path.endsWith('archive_002')
            ? { messages: [message('two')] }
            : { commit_count: 2 };
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
  assert.ok(paths.every((path) => path.includes('session%2Fwith%20slash')));
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
