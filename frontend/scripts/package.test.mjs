import assert from 'node:assert/strict';
import { access, readFile } from 'node:fs/promises';
import test from 'node:test';

const packageRoot = new URL('../packages/react/', import.meta.url);
const manifest = JSON.parse(
  await readFile(new URL('package.json', packageRoot), 'utf8'),
);

// Exercise the built consumer boundary, including declaration and CSS targets.
test('all public package targets exist in the build', async () => {
  for (const [entry, target] of Object.entries(manifest.exports)) {
    const paths = typeof target === 'string' ? [target] : Object.values(target);
    for (const path of paths) {
      await assert.doesNotReject(
        access(new URL(path, packageRoot)),
        `${entry} must point to a shipped file: ${path}`,
      );
    }
  }
});

test('JavaScript entries resolve and import without browser globals', async () => {
  for (const [entry, target] of Object.entries(manifest.exports)) {
    if (typeof target === 'string') continue;
    const specifier = entry.replace(/^\./, manifest.name);
    const module = await import(specifier);
    assert.ok(
      Object.keys(module).length > 0,
      `${specifier} must expose an API`,
    );
  }
});

test('public stylesheets resolve through package exports', async () => {
  for (const entry of Object.keys(manifest.exports)) {
    if (!entry.endsWith('.css')) continue;
    const resolved = import.meta.resolve(entry.replace(/^\./, manifest.name));
    const css = await readFile(new URL(resolved), 'utf8');
    assert.ok(
      !css.includes('@import'),
      `${entry} must be independently bundled`,
    );
  }
});
