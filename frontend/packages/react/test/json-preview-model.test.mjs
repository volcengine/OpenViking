import assert from 'node:assert/strict';
import test from 'node:test';
import {
  createJsonPreviewModel,
  visibleJsonPreviewLines,
} from '../src/components/file-preview/json-preview-model.ts';

test('ordinary JSON preview uses formatted line numbers without changing tokens', () => {
  const model = createJsonPreviewModel(
    '{"profile":{"id":9007199254740993,"limit":1e400,"items":[1,2]}}',
  );
  assert.ok(model);
  assert.deepEqual(
    model.lines.map((line) => line.lineNumber),
    Array.from({ length: model.lines.length }, (_, index) => index + 1),
  );
  assert.match(model.text, /9007199254740993/);
  assert.match(model.text, /1e400/);
});

test('folding a parent preserves the nested fold state when reopened', () => {
  const model = createJsonPreviewModel(
    '{"outer":{"child":{"value":1},"sibling":2}}',
    Infinity,
  );
  assert.ok(model);
  const outer = model.lines.findIndex((line) => line.text.includes('"outer"'));
  const child = model.lines.findIndex((line) => line.text.includes('"child"'));
  assert.notEqual(outer, -1);
  assert.notEqual(child, -1);

  assert.deepEqual(
    visibleJsonPreviewLines(model, new Set([outer, child])).map(
      (line) => line.lineNumber,
    ),
    [1, model.lines[outer].lineNumber, model.lines.at(-1).lineNumber],
  );
  const reopened = visibleJsonPreviewLines(model, new Set([child]));
  assert.equal(
    reopened.some((line) => line.text.includes('"sibling"')),
    true,
  );
  assert.equal(
    reopened.some((line) => line.text.includes('"value"')),
    false,
  );
  assert.equal(
    reopened
      .find((line) => line.lineNumber === child + 1)
      ?.collapsedText?.includes('}'),
    true,
  );
});
