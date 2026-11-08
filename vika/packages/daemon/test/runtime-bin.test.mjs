// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { runtimeBinKind, runtimeBinProblem } from '../src/runtime-bin.mjs';

test('Windows drive-rooted binaries are rejected even though isAbsolute accepts them', () => {
  for (const value of ['\\tools\\codex.exe', '/tools/codex.exe']) {
    assert.equal(path.win32.isAbsolute(value), true);
    assert.notEqual(path.win32.resolve('C:\\setup', value), path.win32.resolve('D:\\workspace', value));
    assert.equal(runtimeBinKind(value, path.win32), 'relative');
  }
  for (const value of ['C:codex', 'bin/codex', './codex']) {
    assert.equal(runtimeBinKind(value, path.win32), 'relative');
  }
});

test('fully qualified paths and bare names retain their platform semantics', () => {
  for (const value of ['C:\\tools\\codex.exe', 'C:/tools/codex.exe', '\\\\server\\share\\codex.exe']) {
    assert.equal(runtimeBinKind(value, path.win32), 'absolute');
  }
  assert.equal(runtimeBinKind('/opt/codex', path.posix), 'absolute');
  assert.equal(runtimeBinKind('codex', path.win32), 'name');
  assert.equal(runtimeBinKind('codex', path.posix), 'name');
});

test('host validation uses the same classification as the portable predicate', () => {
  for (const value of ['\\tools\\codex.exe', '/tools/codex.exe']) {
    const rejected = runtimeBinKind(value) === 'relative';
    assert.equal(Boolean(runtimeBinProblem(value, 'CODEX_BIN')), rejected);
  }
});
