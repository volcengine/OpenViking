import assert from 'node:assert/strict';
import test from 'node:test';
import { parseSessionMessages } from '../src/components/file-preview/session-messages.ts';

const image = {
  type: 'image_url',
  image_url: { url: 'https://images.example/figure.png', detail: 'high' },
};
const context = {
  type: 'context',
  uri: 'viking://resources/guide.md',
  context_type: 'resource',
  abstract: 'First line\nSecond line',
};
const parse = (parts) =>
  parseSessionMessages(
    JSON.stringify({
      id: 'message',
      role: 'user',
      parts,
    }),
  );

test('mixed native image and context parts preserve their fields, order and line breaks', () => {
  const messages = parse([
    { type: 'text', text: 'Before\nText' },
    image,
    context,
    { type: 'text', text: 'After' },
  ]);
  assert.equal(messages.length, 1);
  assert.equal(
    messages[0].content,
    [
      'Before\nText',
      'type: image_url\nimage_url: {\n  "url": "https://images.example/figure.png",\n  "detail": "high"\n}',
      'type: context\nuri: viking://resources/guide.md\ncontext_type: resource\nabstract: First line\nSecond line',
      'After',
    ].join('\n\n'),
  );
});

test('native image-only and context-only messages remain in the conversation', () => {
  for (const part of [image, context]) {
    const messages = parse([part]);
    assert.equal(messages.length, 1);
    assert.equal(messages[0].id, 'message');
    assert.ok(messages[0].content.includes(part.type));
  }
  const legacyImage = parse([
    { type: 'image_url', image_url: 'https://images.example/legacy.png' },
  ]);
  assert.equal(
    legacyImage[0].content,
    'type: image_url\nimage_url: https://images.example/legacy.png',
  );
});

test('valid native references keep neighboring messages and do not depend on browser APIs', () => {
  const values = [
    { role: 'assistant', parts: [{ type: 'text', text: 'First' }] },
    { role: 'user', parts: [context, image] },
    { role: 'assistant', parts: [{ type: 'text', text: 'Last' }] },
  ];
  const messages = parseSessionMessages(
    values.map((value) => JSON.stringify(value)).join('\n'),
  );
  assert.equal(messages.length, 3);
  assert.equal(messages[0].content, 'First');
  assert.equal(messages[2].content, 'Last');
  const metadata = parse([
    { ...context, original: { label: '<script>plain text</script>' } },
  ]);
  assert.match(metadata[0].content, /<script>plain text<\/script>/);
});

test('malformed image/context payloads and unknown parts retain the JSON fallback', () => {
  for (const part of [
    { type: 'image_url', url: 'https://images.example/wrong-shape.png' },
    { type: 'image_url', image_url: '' },
    { type: 'image_url', image_url: null },
    { type: 'image_url', image_url: { url: ' ' } },
    { type: 'image_url', image_url: { url: 42 } },
    { type: 'image_url', image_url: { url: 'image.png', detail: 42 } },
    { ...context, uri: 42 },
    { ...context, abstract: {} },
    { ...context, context_type: 'unknown' },
    { type: 'future_part', content: 'Do not drop me' },
  ]) {
    assert.equal(parse([part]), undefined);
    assert.equal(parse([{ type: 'text', text: 'Before' }, part]), undefined);
  }
});
