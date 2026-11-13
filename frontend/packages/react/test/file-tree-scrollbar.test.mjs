import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const css = await readFile(
  require.resolve('@openviking/react/components/file-tree/style.css'),
  'utf8',
);

test('file tree body defines a thin themed scrollbar on the real scroll container', () => {
  const body = css.match(/\.ov-file-tree__body \{([^}]*)\}/)?.[1] ?? '';
  assert.match(body, /overflow:\s*auto/);
  assert.match(body, /scrollbar-width:\s*thin/);
  assert.match(
    body,
    /scrollbar-color:\s*var\(--ov-ui-file-tree-scrollbar-thumb\) transparent/,
  );
  assert.match(css, /\.ov-file-tree__body::-webkit-scrollbar-track/);
  assert.match(css, /\.ov-file-tree__body::-webkit-scrollbar-thumb/);
});

test('scrollbar thumb colour derives from the theme baseline', () => {
  assert.match(
    css,
    /--ov-ui-file-tree-scrollbar-thumb:\s*var\(\s*--ov-ui-color-border-3/,
  );
});
