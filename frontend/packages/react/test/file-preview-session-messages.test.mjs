import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import {
  isSessionMessagesSource,
  parseSessionMessages,
} from '../src/components/file-preview/session-messages.ts';
import { withBundledPreview } from './helpers/bundled-preview.mjs';

const native = {
  id: 'msg-user',
  role: 'user',
  parts: [{ type: 'text', text: 'First line\nSecond line' }],
  created_at: '2026-11-14T03:29:18.902232+00:00',
};

test('session selection requires messages.jsonl under an identified session', () => {
  const source = (path, fileName = 'messages.jsonl') => ({ path, fileName });
  for (const path of [
    'viking://user/default/sessions/session-1/messages.jsonl',
    'viking://~/sessions/session-1/messages.jsonl',
    'viking://user/default/sessions/session-1/archive_001/messages.jsonl',
    'viking://~/sessions/session-1/archive_001/messages.jsonl',
  ])
    assert.equal(isSessionMessagesSource(source(path)), true);
  for (const candidate of [
    source('viking://resources/messages.jsonl'),
    source('viking://resources/sessions/tutorial/messages.jsonl'),
    source(
      'https://example.com/user/default/sessions/session-1/messages.jsonl',
    ),
    source('viking://user/default/sessions/messages.jsonl'),
    source('viking://~/sessions/messages.jsonl'),
    source('viking://user/default/sessions/session-1/other.jsonl'),
    source('viking://user/default/sessions/session-1/../messages.jsonl'),
    source('viking://user/default/sessions/session-1/data.jsonl', 'data.jsonl'),
    { fileName: 'messages.jsonl' },
    {
      ...source('viking://user/default/sessions/session-1/messages.jsonl'),
      truncated: true,
    },
  ])
    assert.equal(isSessionMessagesSource(candidate), false);
});

test('native parts and legacy content retain time, IDs and tool results', () => {
  const messages = parseSessionMessages(
    [
      native,
      {
        role: 'assistant',
        parts: [
          { type: 'text', text: 'Reply' },
          {
            type: 'tool',
            tool_name: 'list',
            tool_status: 'completed',
            tool_output: { entries: ['guide.md'] },
          },
        ],
      },
      {
        id: 'old',
        role: 'system',
        content: 'Legacy',
        createdAt: 'invalid-date',
      },
    ]
      .map((message) => JSON.stringify(message))
      .join('\r\n'),
  );
  assert.equal(messages[0].id, 'msg-user');
  assert.equal(messages[0].createdAt, native.created_at);
  assert.equal(messages[0].content, 'First line\nSecond line');
  assert.match(messages[1].content, /Reply\n\nlist · completed\n/);
  assert.match(messages[1].content, /"guide.md"/);
  assert.equal(messages[2].createdAt, 'invalid-date');
});

test('unsupported or malformed records fall back as a whole without losing lines', () => {
  for (const invalid of [
    '{',
    '[]',
    'null',
    '{"role":"assistant","parts":[{"type":"image_url","url":"image"}]}',
    '{"content":"Missing role"}',
  ])
    assert.equal(
      parseSessionMessages(`${JSON.stringify(native)}\n${invalid}`),
      undefined,
    );
  assert.equal(parseSessionMessages(' \n'), undefined);
});

test('mixed unsupported parts and legacy content never hide part payloads', () => {
  const text = { type: 'text', text: 'Visible text' };
  for (const part of [
    { type: 'text', text: 42 },
    { type: 'unknown', text: 'Unsupported' },
    { type: 'tool', tool_name: 42 },
    null,
  ])
    for (const content of [undefined, 'Visible text'])
      assert.equal(
        parseSessionMessages(
          JSON.stringify({ role: 'user', content, parts: [text, part] }),
        ),
        undefined,
      );
  for (const parts of [{ type: 'text', text: 'Malformed array' }, [text]])
    assert.equal(
      parseSessionMessages(
        JSON.stringify({ role: 'user', content: 'Different text', parts }),
      ),
      undefined,
    );
  assert.equal(
    parseSessionMessages(
      JSON.stringify({ role: 'user', content: 'Visible text', parts: [text] }),
    )[0].content,
    'Visible text',
  );
});

test('public preview renders session bubbles and retains JSONL source/fallbacks', () =>
  withBundledPreview('file-preview-session-messages', async (dom) => {
    const { document } = dom.window;
    async function waitFor(predicate) {
      for (let attempt = 0; attempt < 400; attempt++) {
        if (predicate()) return;
        await delay(20);
      }
      assert.fail('Session preview did not become ready');
    }
    const transcript = (id) =>
      document.querySelector(`#${id} .ov-file-preview__session`);
    await waitFor(() => transcript('inline') && transcript('remote'));
    const messages = transcript('inline').querySelectorAll('article');
    assert.equal(messages.length, 4);
    assert.match(messages[0].className, /--user$/);
    assert.match(messages[1].className, /--assistant$/);
    assert.equal(messages[0].querySelector('time').dateTime, native.created_at);
    assert.match(messages[0].textContent, /msg-user/);
    assert.equal(
      messages[0].querySelector('.ov-file-preview__session-content')
        .textContent,
      'First line\nSecond line',
    );
    assert.match(
      transcript('remote').textContent,
      /Reply <script>safe text<\/script>/,
    );
    assert.equal(transcript('remote').querySelector('script'), null);
    assert.match(messages[2].textContent, /context_type: resource/);
    assert.match(messages[2].textContent, /viking:\/\/resources\/guide.md/);
    assert.match(
      messages[3].textContent,
      /https:\/\/images.example\/figure.png/,
    );
    assert.match(messages[3].textContent, /"detail": "high"/);
    assert.equal(transcript('inline').querySelector('img'), null);

    const viewSwitch = (label) =>
      [...document.querySelectorAll('#inline button')].find(
        (button) => button.textContent === label,
      );
    await waitFor(() => viewSwitch('Source'));
    viewSwitch('Source').click();
    await waitFor(
      () =>
        !transcript('inline') &&
        document.querySelector('#inline').textContent.includes('"parts"'),
    );
    assert.match(document.querySelector('#inline').textContent, /"parts"/);
    viewSwitch('Preview').click();
    await waitFor(() => transcript('inline'));
    const jsonPreview = (id) =>
      document.querySelector(`#${id} [data-file-viewer-json]`);
    await waitFor(() => jsonPreview('ordinary') && jsonPreview('unsupported'));
    assert.match(jsonPreview('ordinary').textContent, /1 \/ 4/);
    assert.equal(transcript('ordinary'), null);
    assert.equal(transcript('unsupported'), null);
    const source = [...document.querySelectorAll('#unsupported button')].find(
      (button) => button.textContent === 'Source',
    );
    assert.ok(source);
    source.click();
    await waitFor(() =>
      document
        .querySelector('#unsupported')
        .textContent.includes('Unsupported future content'),
    );
  }));
