import '../packages/react/test/helpers/browser-env.mjs';
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

test('JavaScript entries expose their API in the browser environment', async () => {
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

test('business entries expose components without headless Hooks', async () => {
  const root = await import('@openviking/react');
  assert.equal(
    Object.keys(root).some((name) => /^use[A-Z]/.test(name)),
    false,
  );
  for (const path of [
    'modules/file-browser',
    'modules/console',
    'modules/scope-cascade',
    'components/wiki-dashboard',
    'components/experience-dashboard',
  ]) {
    const entry = await import('@openviking/react/' + path);
    assert.equal(
      Object.keys(entry).some((name) => /^use[A-Z]/.test(name)),
      false,
      path,
    );
  }
  for (const name of ['wiki-dashboard', 'experience-dashboard']) {
    assert.equal(`./modules/${name}` in manifest.exports, false);
    assert.ok(`./components/${name}` in manifest.exports);
  }
});

test('VikingBot chat hooks remain internal to Console', () => {
  assert.throws(
    () => import.meta.resolve('@openviking/react/modules/viking-bot'),
    {
      code: 'ERR_PACKAGE_PATH_NOT_EXPORTED',
    },
  );
  assert.equal('./hooks/viking-bot' in manifest.exports, false);
  assert.equal('./modules/viking-bot/style.css' in manifest.exports, false);
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

// Internal overlays must stay inaccessible through the SDK.
test('Modal remains internal and has no root or subpath API', async () => {
  const root = await import('@openviking/react');
  assert.equal('Modal' in root, false);
  for (const path of [
    'components/modal',
    'components/modal/style.css',
    'shared/primitives/modal/modal.js',
    'shared/primitives/modal/style.css',
  ]) {
    assert.throws(() => import.meta.resolve(`@openviking/react/${path}`), {
      code: 'ERR_PACKAGE_PATH_NOT_EXPORTED',
    });
  }
});
