// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  DSH_EXTENSIONS_FILE, DSH_PROJECT_EXTENSIONS_FILE, defaultHostSkillDirs,
  dshExtensionsPaths, normalizeMcpServer, readDshExtensions,
} from '../src/dsh-extensions.mjs';

function temp(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-dsh-extensions-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

function write(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, typeof value === 'string' ? value : `${JSON.stringify(value, null, 2)}\n`);
}

test('extension paths are the instance document and the workspace .agents document', t => {
  const root = temp(t);
  assert.deepEqual(dshExtensionsPaths({ dataDir: '/data/one', workspace: '/work/repo' }), {
    global: path.join('/data/one', 'dsh', DSH_EXTENSIONS_FILE),
    project: path.join('/work/repo', '.agents', DSH_PROJECT_EXTENSIONS_FILE),
  });
  assert.throws(() => dshExtensionsPaths({ workspace: '/work' }), /dataDir/);
  assert.throws(() => dshExtensionsPaths({ dataDir: root }), /workspace/);
});

test('host skill roots are ~/.agents/skills and ~/.dsh/skills', () => {
  assert.deepEqual(defaultHostSkillDirs({ home: '/home/me' }), [
    path.join('/home/me', '.agents', 'skills'),
    path.join('/home/me', '.dsh', 'skills'),
  ]);
  assert.throws(() => defaultHostSkillDirs({ home: '' }), /home/);
});

test('missing documents still give the host skill roots', t => {
  const root = temp(t);
  const home = path.join(root, 'home');
  assert.deepEqual(readDshExtensions({
    dataDir: path.join(root, 'data'), workspace: path.join(root, 'work'), home,
  }), {
    mcpServers: [],
    skillDirs: defaultHostSkillDirs({ home }),
    hostSkills: true,
  });
});

test('workspace values win per server and configured roots follow the host roots', t => {
  const root = temp(t);
  const home = path.join(root, 'home');
  const dataDir = path.join(root, 'data');
  const workspace = path.join(root, 'work');
  const paths = dshExtensionsPaths({ dataDir, workspace });
  write(paths.global, {
    skillDirs: ['./skills', '/shared/skills'],
    mcpServers: {
      github: { command: 'old-github', args: ['--old'] },
      files: { command: 'files-server', env: { TOKEN: 'x' } },
    },
  });
  write(paths.project, {
    skillDirs: ['../project-skills', './skills', '~/.agents/skills'],
    mcpServers: {
      github: { command: 'new-github', args: ['--new'], failOnStartupError: true },
      remote: { url: 'https://mcp.example.com/mcp', headers: { Authorization: 'Bearer x' } },
    },
  });
  const extensions = readDshExtensions({ dataDir, workspace, home });
  assert.deepEqual(extensions.skillDirs, [
    path.join(home, '.agents', 'skills'),
    path.join(home, '.dsh', 'skills'),
    path.join(dataDir, 'dsh', 'skills'),
    '/shared/skills',
    path.join(root, 'project-skills'),
    path.join(workspace, 'skills'),
  ]);
  assert.equal(extensions.hostSkills, true);
  assert.deepEqual(extensions.mcpServers, [
    { transport: 'stdio', serverName: 'github', command: 'new-github', args: ['--new'], failOnStartupError: true },
    { transport: 'stdio', serverName: 'files', command: 'files-server', args: [], env: { TOKEN: 'x' } },
    { transport: 'streamable-http', serverName: 'remote', url: 'https://mcp.example.com/mcp', headers: { Authorization: 'Bearer x' } },
  ]);
});

test('either document can keep the host skill roots out', t => {
  const root = temp(t);
  const home = path.join(root, 'home');
  const dataDir = path.join(root, 'data');
  const workspace = path.join(root, 'work');
  const paths = dshExtensionsPaths({ dataDir, workspace });

  write(paths.global, { hostSkills: false });
  assert.deepEqual(readDshExtensions({ dataDir, workspace, home }).skillDirs, []);

  write(paths.project, { hostSkills: true });
  // A project cannot widen the global isolation back on.
  assert.deepEqual(readDshExtensions({ dataDir, workspace, home }).skillDirs, []);
});

test('invalid values are skipped one at a time and never throw', t => {
  const root = temp(t);
  const home = path.join(root, 'home');
  const dataDir = path.join(root, 'data');
  const workspace = path.join(root, 'work');
  const paths = dshExtensionsPaths({ dataDir, workspace });
  write(paths.global, '{ not json');
  write(paths.project, {
    skillDirs: ['ok', 42],
    hostSkills: 'yes',
    mcpServers: {
      personal_agent: { command: 'taken' },
      'bad name': { command: 'x' },
      broken: { transport: 'stdio' },
      'no-url': { transport: 'streamable-http' },
      good: { command: 'server', args: ['--flag'], env: { A: 'b' } },
    },
  });
  const warnings = [];
  const extensions = readDshExtensions({ dataDir, workspace, home, warn: message => warnings.push(message) });
  assert.deepEqual(extensions.skillDirs, [...defaultHostSkillDirs({ home }), path.join(workspace, 'ok')]);
  assert.deepEqual(extensions.mcpServers, [
    { transport: 'stdio', serverName: 'good', command: 'server', args: ['--flag'], env: { A: 'b' } },
  ]);
  assert.equal(extensions.hostSkills, true);
  assert.ok(warnings.some(line => line.includes(paths.global)));
  assert.ok(warnings.some(line => line.includes('personal_agent')));
  assert.ok(warnings.some(line => line.includes('bad name')));
  assert.ok(warnings.some(line => line.includes('stdio needs a command')));
  assert.ok(warnings.some(line => line.includes('streamable-http needs a url')));
  assert.ok(warnings.some(line => line.includes('hostSkills must be a boolean')));
});

test('mcp server normalization defaults the transport from the fields present', () => {
  assert.deepEqual(normalizeMcpServer('local', { command: 'node' }), {
    transport: 'stdio', serverName: 'local', command: 'node', args: [],
  });
  assert.deepEqual(normalizeMcpServer('http', { url: 'https://example.test/mcp', toolCallTimeoutMs: 1000 }), {
    transport: 'streamable-http', serverName: 'http', url: 'https://example.test/mcp', toolCallTimeoutMs: 1000,
  });
  assert.equal(normalizeMcpServer('bad', { command: 'x', transport: 'sse' }), null);
});
