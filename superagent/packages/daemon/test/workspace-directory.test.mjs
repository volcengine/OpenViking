// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { inflateRawSync } from 'node:zlib';
import { readWorkspaceDirectory } from '../src/workspace-directory.mjs';

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'workspace-directory-'));
  const workspace = path.join(directory, 'workspace');
  fs.mkdirSync(workspace);
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return { directory, workspace };
}

function entriesOf(zip) {
  const entries = new Map();
  let offset = 0;
  while (offset < zip.length && zip.readUInt32LE(offset) === 0x04034b50) {
    const method = zip.readUInt16LE(offset + 8);
    const compressedSize = zip.readUInt32LE(offset + 18);
    const size = zip.readUInt32LE(offset + 22);
    const nameLength = zip.readUInt16LE(offset + 26);
    const extraLength = zip.readUInt16LE(offset + 28);
    const name = zip.subarray(offset + 30, offset + 30 + nameLength).toString();
    const dataOffset = offset + 30 + nameLength + extraLength;
    const compressed = zip.subarray(dataOffset, dataOffset + compressedSize);
    const bytes = method === 8 ? inflateRawSync(compressed) : compressed;
    assert.equal(bytes.length, size);
    entries.set(name, bytes);
    offset = dataOffset + compressedSize;
  }
  assert.equal(zip.readUInt32LE(offset), 0x02014b50);
  return entries;
}

describe('workspace directory ZIP snapshots', () => {
  it('keeps the top-level directory, nested paths, empty files and UTF-8 names', (t) => {
    const { workspace } = fixture(t);
    fs.mkdirSync(path.join(workspace, 'folder', 'empty-dir'), { recursive: true });
    fs.mkdirSync(path.join(workspace, 'folder', 'nested'));
    fs.writeFileSync(path.join(workspace, 'folder', 'empty.txt'), '');
    fs.writeFileSync(path.join(workspace, 'folder', 'nested', '报告.txt'), 'contents');
    const first = readWorkspaceDirectory('folder', workspace);
    const second = readWorkspaceDirectory('folder', workspace);
    assert.equal(first.name, 'folder.zip');
    assert.deepEqual(first.bytes, second.bytes);
    const entries = entriesOf(first.bytes);
    assert.deepEqual([...entries.keys()], [
      'folder/', 'folder/empty-dir/', 'folder/empty.txt', 'folder/nested/', 'folder/nested/报告.txt',
    ]);
    assert.equal(entries.get('folder/empty.txt').length, 0);
    assert.equal(entries.get('folder/nested/报告.txt').toString(), 'contents');
  });

  it('rejects empty directories, traversal, absolute paths and workspace escapes', (t) => {
    const { directory, workspace } = fixture(t);
    fs.mkdirSync(path.join(workspace, 'empty'));
    fs.mkdirSync(path.join(workspace, 'only-empty-dirs', 'nested'), { recursive: true });
    fs.mkdirSync(path.join(directory, 'outside'));
    fs.writeFileSync(path.join(directory, 'outside', 'secret.txt'), 'secret');
    fs.symlinkSync(path.join(directory, 'outside'), path.join(workspace, 'escape'), 'dir');
    for (const filePath of ['../outside', path.join(directory, 'outside'), 'https://example.com/folder']) {
      assert.throws(() => readWorkspaceDirectory(filePath, workspace), /file_path/);
    }
    assert.throws(() => readWorkspaceDirectory('empty', workspace), /at least one regular file/);
    assert.throws(() => readWorkspaceDirectory('only-empty-dirs', workspace), /at least one regular file/);
    assert.throws(() => readWorkspaceDirectory('escape', workspace), /escapes the workspace/);
  });

  it('rejects a symlink as the root, a parent, or any directory entry', (t) => {
    const { workspace } = fixture(t);
    fs.mkdirSync(path.join(workspace, 'real', 'nested'), { recursive: true });
    fs.writeFileSync(path.join(workspace, 'real', 'nested', 'file.txt'), 'contents');
    fs.symlinkSync(path.join(workspace, 'real'), path.join(workspace, 'root-link'), 'dir');
    fs.symlinkSync(path.join(workspace, 'real'), path.join(workspace, 'parent-link'), 'dir');
    fs.symlinkSync(path.join(workspace, 'real', 'nested', 'file.txt'), path.join(workspace, 'real', 'file-link'), 'file');
    fs.symlinkSync(path.join(workspace, 'real', 'nested'), path.join(workspace, 'real', 'dir-link'), 'dir');
    assert.throws(() => readWorkspaceDirectory('root-link', workspace), /symbolic links/);
    assert.throws(() => readWorkspaceDirectory('parent-link/nested', workspace), /symbolic links/);
    assert.throws(() => readWorkspaceDirectory('real', workspace), /symbolic links/);
  });

  it('rejects special files instead of blocking or archiving them', (t) => {
    if (process.platform === 'win32') return t.skip('POSIX special files');
    const { workspace } = fixture(t);
    fs.mkdirSync(path.join(workspace, 'folder'));
    const fifo = spawnSync('mkfifo', [path.join(workspace, 'folder', 'pipe')]);
    assert.equal(fifo.status, 0, fifo.stderr.toString());
    assert.throws(() => readWorkspaceDirectory('folder', workspace), /special files/);
  });

  it('applies the size limit to the generated ZIP', (t) => {
    const { workspace } = fixture(t);
    fs.mkdirSync(path.join(workspace, 'folder'));
    fs.writeFileSync(path.join(workspace, 'folder', 'data.bin'), Buffer.alloc(1024, 7));
    assert.throws(() => readWorkspaceDirectory('folder', workspace, 64), /directory ZIP must be at most/);
  });
});
