import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { mkdtemp, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRslib } from '@rslib/core';

const source = resolve('packages/react/src');
const files = (await readdir(source, { recursive: true })).filter((file) =>
  /[/\\]locales[/\\](en-US|zh-CN)\.ts$/.test(file),
);
const output = await mkdtemp(join(tmpdir(), 'ov-locale-resources-'));
after(() => rm(output, { recursive: true, force: true }));
const entry = Object.fromEntries(
  files.map((file, index) => [`locale-${index}`, join(source, file)]),
);
entry.shared = join(source, 'shared/locale.ts');
entry.installer = join(
  source,
  'components/agent-connect/agent-connect-installer.ts',
);
const builder = await createRslib({
  config: {
    lib: [{ format: 'esm', dts: false, source: { entry } }],
    performance: { buildCache: false },
    output: { target: 'node', distPath: { root: output } },
  },
});
await builder.build();
await writeFile(join(output, 'package.json'), '{"type":"module"}');
const load = (name) => import(pathToFileURL(join(output, `${name}.js`)));
const groups = new Map();
for (const [index, file] of files.entries()) {
  const languages = groups.get(dirname(file)) ?? {};
  const resource = await load(`locale-${index}`);
  languages[basename(file, '.ts')] = {
    ...resource.default,
    ...(resource.renderErrors ? { renderErrors: resource.renderErrors } : {}),
  };
  groups.set(dirname(file), languages);
}

const placeholders = (text) =>
  [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
function compare(english, chinese, key) {
  assert.equal(typeof chinese, typeof english, key);
  if (typeof english === 'function') {
    assert.equal(
      chinese.length,
      english.length,
      `${key}: formatter parameters`,
    );
  } else if (typeof english === 'string') {
    assert.ok(english.trim(), key);
    assert.ok(chinese.trim(), key);
    assert.deepEqual(
      placeholders(chinese),
      placeholders(english),
      `${key}: placeholders`,
    );
  } else {
    assert.deepEqual(
      Object.keys(chinese).sort(),
      Object.keys(english).sort(),
      key,
    );
    for (const name of Object.keys(english))
      compare(english[name], chinese[name], `${key}.${name}`);
  }
}

for (const [owner, languages] of groups) {
  test(`${owner} has matching bilingual keys, placeholders and formatters`, () => {
    assert.deepEqual(Object.keys(languages).sort(), ['en-US', 'zh-CN']);
    compare(languages['en-US'], languages['zh-CN'], owner);
  });
}

test('shared resolution preserves language defaults and explicit empty overrides', async () => {
  const { resolveLocale } = await load('shared');
  const messages = { 'en-US': { retry: 'Retry' }, 'zh-CN': { retry: '重试' } };
  assert.equal(resolveLocale(messages).retry, 'Retry');
  assert.equal(
    resolveLocale(messages, 'zh-CN', { retry: undefined }).retry,
    '重试',
  );
  assert.equal(resolveLocale(messages, 'zh-CN', { retry: '' }).retry, '');
  assert.equal(messages['zh-CN'].retry, '重试');
  const browser = groups.get(join('modules', 'file-browser', 'locales'));
  assert.equal(browser['en-US'].formatCharacterCount(1), '1 character');
  assert.equal(browser['en-US'].formatCharacterCount(1000), '1,000 characters');
  assert.equal(browser['zh-CN'].formatCharacterCount(1000), '1,000 个字符');
});

test('regional UI locales preserve installer language flags and guide URLs', async () => {
  const { getInstallerInstructions } = await load('installer');
  for (const [locale, flag, heading] of [
    ['zh-CN', 'zh', '帮我为 Codex 安装'],
    ['en-US', 'en', 'Install the OpenViking memory plugin'],
  ]) {
    const text = getInstallerInstructions(
      'Codex',
      'codex',
      locale,
      'demo-key',
      'https://example.com',
    );
    assert.ok(text.startsWith(heading));
    assert.ok(text.includes(`--lang ${flag} --harness codex,cli`));
    assert.ok(!text.includes(`--lang ${locale}`));
    assert.ok(text.includes(`/agents/${flag}/onboarding.md`));
  }
});
