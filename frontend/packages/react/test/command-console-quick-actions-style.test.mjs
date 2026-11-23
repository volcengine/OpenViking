import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const require = createRequire(import.meta.url);
const css = await readFile(
  require.resolve('@openviking/react/components/command-console/style.css'),
  'utf8',
);

test('terminal quick actions keep a 28px minimum height and grow for wrapped labels', () => {
  const block = css.match(
    /(?:^|\n)\.ov-command-console__quick-action\s*\{([^}]+)\}/,
  )?.[1];

  assert.ok(block);
  assert.match(block, /min-block-size:\s*28px/);
  assert.doesNotMatch(block, /(?:^|;)\s*block-size:\s*28px/);
  assert.match(block, /padding:\s*2px 8px/);
});

test('terminal quick actions only move when reduced motion is not requested', () => {
  assert.match(
    css,
    /@media\s*\(hover:\s*hover\)\s*and\s*\(prefers-reduced-motion:\s*no-preference\)[\s\S]*?\.ov-command-console__quick-action:hover:not\(:disabled\)\s*\{[^}]*transform:\s*translateY\(-1px\)/,
  );
});
