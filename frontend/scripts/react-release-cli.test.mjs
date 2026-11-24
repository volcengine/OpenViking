import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import test from 'node:test';

const cases = [
  { channel: 'beta', input: '', version: '0.1.8-beta.42.1.gabcdef012345' },
  { channel: 'latest', input: '0.2.0', version: '0.2.0' },
];

for (const { channel, input, version } of cases) {
  test(
    `CLI manual ${channel} on a tag honors the selected channel and version`,
    {
      skip: process.platform === 'win32' && 'Release workflow runs on Linux',
    },
    () => {
      const root = realpathSync(mkdtempSync(join(tmpdir(), 'ov-release-cli-')));
      try {
        for (const path of ['bin', 'scripts', 'packages/react', 'docs']) {
          mkdirSync(join(root, path), { recursive: true });
        }
        copyFileSync(
          new URL('./react-release.mjs', import.meta.url),
          join(root, 'scripts/react-release.mjs'),
        );
        const write = (path, value) =>
          writeFileSync(join(root, path), JSON.stringify(value));
        write('packages/react/package.json', { version: '0.1.7' });
        write('docs/package.json', {
          dependencies: { '@openviking/react': '0.1.7' },
        });
        write('package-lock.json', {
          packages: {
            'packages/react': { version: '0.1.7' },
            docs: { dependencies: { '@openviking/react': '0.1.7' } },
          },
        });
        const trace = join(root, 'npm-lookup.json');
        writeFileSync(
          join(root, 'bin/npm'),
          '#!/usr/bin/env node\n' +
            'require("node:fs").writeFileSync(process.env.NPM_LOOKUP_TRACE, JSON.stringify(process.argv.slice(2)));\n' +
            'console.log(JSON.stringify("0.1.7"));\n',
          { mode: 0o755 },
        );
        const output = join(root, 'github-output');
        const result = spawnSync(
          process.execPath,
          [join(root, 'scripts/react-release.mjs')],
          {
            cwd: root,
            encoding: 'utf8',
            env: {
              ...process.env,
              PATH: `${join(root, 'bin')}${delimiter}${process.env.PATH}`,
              NPM_LOOKUP_TRACE: trace,
              GITHUB_OUTPUT: output,
              GITHUB_EVENT_NAME: 'workflow_dispatch',
              GITHUB_REF_TYPE: 'tag',
              GITHUB_REF_NAME: 'react@0.1.7',
              GITHUB_RUN_NUMBER: '42',
              GITHUB_RUN_ATTEMPT: '1',
              GITHUB_SHA: 'abcdef0123456789abcdef0123456789abcdef01',
              RELEASE_CHANNEL: channel,
              RELEASE_VERSION: input,
            },
          },
        );
        assert.equal(result.status, 0, result.stderr);
        assert.equal(
          readFileSync(output, 'utf8'),
          `channel=${channel}\nversion=${version}\n`,
        );
        assert.equal(existsSync(trace), channel === 'beta');
        if (channel === 'beta') {
          assert.deepEqual(JSON.parse(readFileSync(trace, 'utf8')), [
            'view',
            '@openviking/react@latest',
            'version',
            '--json',
            '--registry=https://registry.npmjs.org',
          ]);
        }
        const read = (path) =>
          JSON.parse(readFileSync(join(root, path), 'utf8'));
        assert.equal(read('packages/react/package.json').version, version);
        assert.equal(
          read('docs/package.json').dependencies['@openviking/react'],
          version,
        );
        const lock = read('package-lock.json').packages;
        assert.equal(lock['packages/react'].version, version);
        assert.equal(lock.docs.dependencies['@openviking/react'], version);
      } finally {
        rmSync(root, { recursive: true, force: true });
      }
    },
  );
}
