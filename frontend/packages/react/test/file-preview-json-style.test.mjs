import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const style = await readFile(
  new URL(
    '../src/components/file-preview/file-preview-console.css',
    import.meta.url,
  ),
  'utf8',
);

test('JSON preview hides record numbers that do not represent source lines', () => {
  assert.match(
    style,
    /\.ov-file-preview \.ark-agentic-file-viewer-json__gutter\s*{[^}]*display:\s*none;/s,
  );
  assert.match(
    style,
    /\.ov-file-preview \.ark-agentic-file-viewer-json__row\s*{[^}]*padding-inline-start:\s*16px;/s,
  );
});
