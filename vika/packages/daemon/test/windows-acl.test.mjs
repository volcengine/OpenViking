// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { preparePrivateDirectory, assertPrivateDirectory } from '../src/private-temp.mjs';

const windows = process.platform === 'win32';
function powershell(script, directory) {
  return execFileSync(path.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
    ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], {
      env: { ...process.env, TEST_PRIVATE_DIRECTORY: directory }, encoding: 'utf8', windowsHide: true,
    });
}
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-acl-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

test('Windows private directories receive protected current-user ACLs, including Unicode paths', { skip: !windows }, (t) => {
  const root = fixture(t);
  const directory = path.join(root, "私人 & ' $目录");
  assert.equal(preparePrivateDirectory(directory), directory);
  assert.doesNotThrow(() => assertPrivateDirectory(directory));
  assert.doesNotThrow(() => preparePrivateDirectory(directory));
  const child = path.join(directory, 'outbox');
  assert.equal(preparePrivateDirectory(child), child);
  assert.doesNotThrow(() => assertPrivateDirectory(child));
});

test('Windows existing broad ACL is refused without being modified', { skip: !windows }, (t) => {
  const root = fixture(t);
  const directory = path.join(root, 'shared');
  fs.mkdirSync(directory);
  powershell(`$ErrorActionPreference='Stop'; $p=$env:TEST_PRIVATE_DIRECTORY; $a=Get-Acl -LiteralPath $p;
    $sid=New-Object Security.Principal.SecurityIdentifier('S-1-1-0');
    $rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,'ReadAndExecute','ContainerInherit, ObjectInherit','None','Allow');
    $a.AddAccessRule($rule); Set-Acl -LiteralPath $p -AclObject $a`, directory);
  const sddl = () => powershell('(Get-Acl -LiteralPath $env:TEST_PRIVATE_DIRECTORY).Sddl', directory).trim();
  const before = sddl();
  assert.throws(() => preparePrivateDirectory(directory), /ACL validation failed/);
  assert.equal(sddl(), before);
});

test('Windows explicit third-party allow ACE is refused even with inheritance disabled', { skip: !windows }, (t) => {
  const directory = path.join(fixture(t), 'protected');
  preparePrivateDirectory(directory);
  powershell(`$ErrorActionPreference='Stop'; $p=$env:TEST_PRIVATE_DIRECTORY; $a=Get-Acl -LiteralPath $p;
    $sid=New-Object Security.Principal.SecurityIdentifier('S-1-1-0');
    $rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,'ReadAndExecute','Allow');
    $a.AddAccessRule($rule); Set-Acl -LiteralPath $p -AclObject $a`, directory);
  assert.throws(() => assertPrivateDirectory(directory), /ACL validation failed/);
});

test('Windows junctions are refused without changing the target ACL', { skip: !windows }, (t) => {
  const root = fixture(t);
  const target = path.join(root, 'target');
  preparePrivateDirectory(target);
  const link = path.join(root, 'junction');
  fs.symlinkSync(target, link, 'junction');
  assert.throws(() => preparePrivateDirectory(link), /directory owned|symbolic link/);
  assert.doesNotThrow(() => assertPrivateDirectory(target));
});

test('Windows Lark media and image cache work through real private-directory callers', { skip: !windows }, async (t) => {
  const { prepareLarkMediaDirectory, pruneLarkMedia } = await import('../src/lark-media.mjs');
  const { LarkImageCache } = await import('../src/lark-image-cache.mjs');
  const root = fixture(t);
  const tempDir = path.join(root, '临时 文件');
  const media = prepareLarkMediaDirectory(tempDir);
  assert.equal(media, path.join(tempDir, 'media'));
  const filename = path.join(media, `lark-${'a'.repeat(24)}.png`);
  fs.writeFileSync(filename, Buffer.from('downloaded media'));
  fs.utimesSync(filename, new Date(0), new Date(0));
  assert.equal(pruneLarkMedia(tempDir, 1000), 1);
  assert.equal(fs.existsSync(filename), false);
  const dataDir = path.join(root, '账户 数据');
  const digest = 'b'.repeat(64);
  const key = 'img_v3_windows_acl_fixture';
  let cache = new LarkImageCache(dataDir);
  try {
    cache.put('lark', 'test-profile', digest, key);
    assert.equal(cache.get('lark', 'test-profile', digest), key);
  } finally { cache.close(); }
  cache = new LarkImageCache(dataDir);
  try { assert.equal(cache.get('lark', 'test-profile', digest), key); }
  finally { cache.close(); }
});

test('Windows outgoing file snapshots use private ACLs and retain bytes until cleanup', { skip: !windows }, async (t) => {
  const { resolveContent } = await import('../src/actions/content.mjs');
  const root = fixture(t);
  const workspace = path.join(root, 'workspace');
  const tempDir = path.join(root, 'temp');
  fs.mkdirSync(workspace);
  const bytes = Buffer.from('private attachment 中文');
  fs.writeFileSync(path.join(workspace, 'report.txt'), bytes);
  const resolved = resolveContent({ file_path: 'report.txt' }, { workspace, tempDir, maxTextChars: 1000 });
  try {
    assert.equal(resolved.type, 'file');
    assert.deepEqual(fs.readFileSync(resolved.payload.path), bytes);
    assert.doesNotThrow(() => assertPrivateDirectory(path.join(tempDir, 'outbox')));
  } finally { resolved.cleanup(); }
  assert.equal(fs.existsSync(resolved.payload.path), false);
});
