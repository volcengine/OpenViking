// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { crc32, deflateRawSync } from 'node:zlib';
import { resolveWorkspacePath } from './workspace-file.mjs';

function assertSafeEntryName(name) {
  if (!name || name === '.' || name === '..' || /[/\\\0]/.test(name)) {
    throw new Error('directory contains a filename that cannot be represented safely in ZIP');
  }
}

function assertNoSymlinkComponents(root, filename) {
  let current = root;
  for (const component of path.relative(root, filename).split(path.sep)) {
    current = path.join(current, component);
    if (fs.lstatSync(current).isSymbolicLink()) throw new Error('directory must not contain symbolic links');
  }
}

function readRegularFile(filename) {
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile()) throw new Error('directory must contain only regular files and directories');
    if (stat.size > 0xffffffff) throw new Error('directory contains a file too large for ZIP');
    const bytes = Buffer.alloc(stat.size);
    const size = fs.readSync(fd, bytes, 0, stat.size, 0);
    if (size !== stat.size) throw new Error('file changed while the directory was being read');
    return bytes;
  } finally { fs.closeSync(fd); }
}

function collectEntries(directory, archiveDirectory, entries) {
  const children = fs.readdirSync(directory, { withFileTypes: true })
    .sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0);
  for (const dirent of children) {
    assertSafeEntryName(dirent.name);
    const filename = path.join(directory, dirent.name);
    const stat = fs.lstatSync(filename);
    const archiveName = `${archiveDirectory}/${dirent.name}`;
    if (Buffer.byteLength(archiveName) > 0xffff) throw new Error('directory contains a path too long for ZIP');
    if (stat.isSymbolicLink()) throw new Error('directory must not contain symbolic links');
    if (stat.isDirectory()) {
      entries.push({ name: `${archiveName}/`, directory: true });
      collectEntries(filename, archiveName, entries);
    } else if (stat.isFile()) {
      entries.push({ name: archiveName, directory: false, filename });
    } else {
      throw new Error('directory must contain only regular files and directories; special files are not supported');
    }
    if (entries.length > 0xffff) throw new Error('directory contains too many entries for ZIP');
  }
}

// A deterministic ZIP32 archive with UTF-8 names. Source files are opened one
// at a time without following a final symlink and frozen in the returned buffer.
function zipEntries(entries, maxBytes) {
  const localParts = [];
  const centralParts = [];
  let offset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.name);
    const source = entry.directory ? Buffer.alloc(0) : readRegularFile(entry.filename);
    const compressed = entry.directory ? source : deflateRawSync(source);
    const method = entry.directory ? 0 : 8;
    const crc = crc32(source);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(method, 8);
    // A fixed timestamp keeps retries idempotent when only mtimes changed.
    local.writeUInt16LE(0, 10);
    local.writeUInt16LE(0x0021, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(compressed.length, 18);
    local.writeUInt32LE(source.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(0, 28);
    localParts.push(local, name, compressed);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE((3 << 8) | 20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x0800, 8);
    central.writeUInt16LE(method, 10);
    central.writeUInt16LE(0, 12);
    central.writeUInt16LE(0x0021, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(compressed.length, 20);
    central.writeUInt32LE(source.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt16LE(0, 30);
    central.writeUInt16LE(0, 32);
    central.writeUInt16LE(0, 34);
    central.writeUInt16LE(0, 36);
    central.writeUInt32LE((((entry.directory ? 0o40755 : 0o100644) << 16) | (entry.directory ? 0x10 : 0)) >>> 0, 38);
    central.writeUInt32LE(offset, 42);
    centralParts.push(central, name);
    offset += local.length + name.length + compressed.length;
    if (offset > maxBytes) throw new Error(`directory ZIP must be at most ${maxBytes / 1024 / 1024} MiB`);
  }
  const centralSize = centralParts.reduce((size, part) => size + part.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);
  const size = offset + centralSize + end.length;
  if (size > maxBytes) throw new Error(`directory ZIP must be at most ${maxBytes / 1024 / 1024} MiB`);
  return Buffer.concat([...localParts, ...centralParts, end], size);
}

export function readWorkspaceDirectory(relative, workspace, maxBytes = 20 * 1024 * 1024) {
  const { root, requested, filename } = resolveWorkspacePath(relative, workspace);
  assertNoSymlinkComponents(root, requested);
  if (!fs.statSync(filename).isDirectory()) throw new Error('file_path directory must be a directory');
  const name = path.basename(filename);
  assertSafeEntryName(name);
  const entries = [{ name: `${name}/`, directory: true }];
  collectEntries(filename, name, entries);
  if (!entries.some((entry) => !entry.directory)) throw new Error('directory must contain at least one regular file');
  return { name: `${name}.zip`, bytes: zipEntries(entries, maxBytes) };
}
