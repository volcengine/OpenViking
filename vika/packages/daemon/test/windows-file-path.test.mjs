import test from 'node:test';
import assert from 'node:assert/strict';
import { assertWindowsFilePath, guardWindowsFileTool } from '../src/windows-file-path.mjs';
test('Windows structured file tools reject ambiguous NTFS names before IO', async () => {
  for (const name of ['a:b.txt', 'C:\\work\\a:b', 'CON.txt', 'aux.pdf', 'trail. ', 'COM1', 'a?.txt', '\\\\?\\C:\\work\\a']) assert.throws(() => assertWindowsFilePath(name));
  for (const name of ['C:\\work\\你好.txt', 'relative.txt', '..\\parent\\ok', '\\\\server\\share\\file']) assert.doesNotThrow(() => assertWindowsFilePath(name));
  let called = false;
  const tool = guardWindowsFileTool({ execute: async () => { called = true; return 'ok'; } });
  await assert.rejects(tool.execute({ path: 'a:b' }));
  assert.equal(called, false);
  assert.equal(await tool.execute({ path: 'ok.txt' }), 'ok');
});
