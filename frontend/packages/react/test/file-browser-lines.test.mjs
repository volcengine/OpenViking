import assert from 'node:assert/strict';
import test from 'node:test';

import { joinLines, splitLines } from '../src/modules/file-browser/fs-lines.ts';

test('a trailing terminator does not create an extra line', () => {
  // 服务端 `'a\n'.splitlines()` 是 1 行，不是 2 行；多算一行会让「正好读完」被
  // 当成截断，还会丢掉最后的换行符。
  //
  // `'a\n'.splitlines()` is 1 line on the server, not 2; counting one extra turns
  // "read to the end" into truncation and drops the final newline.
  assert.deepEqual(splitLines('a\n'), ['a\n']);
  assert.deepEqual(splitLines('a\nb\n'), ['a\n', 'b\n']);
  assert.deepEqual(splitLines('a\nb'), ['a\n', 'b']);
});

test('a lone CR ends a line, as it does on the server', () => {
  // `split('\n')` 看不见单独的 `\r`，会把整份文件当成一行。
  // `split('\n')` does not see a lone `\r` and reads the whole file as one line.
  assert.deepEqual(splitLines('a\rb\r'), ['a\r', 'b\r']);
});

test('CRLF counts as one separator', () => {
  // 拆成两个分隔符会凭空多出一行空行。
  // Splitting it as two separators invents an empty line.
  assert.deepEqual(splitLines('a\r\nb'), ['a\r\n', 'b']);
});

test('the remaining Python separators also end a line', () => {
  // Python 的 `splitlines` 还认这些；记忆文件里出现时行号同样要对得上。
  // Python's `splitlines` recognises these too, and line numbers have to agree when a
  // memory file contains one.
  const separators = [
    '\v',
    '\f',
    '\x1c',
    '\x1d',
    '\x1e',
    '\x85',
    '\u2028',
    '\u2029',
  ];
  for (const separator of separators) {
    assert.deepEqual(splitLines(`a${separator}b`), [`a${separator}`, 'b']);
  }
});

test('an empty string has no lines', () => {
  // 与 `''.splitlines()` 一致：空数组，而不是一个空行。
  // Matches `''.splitlines()`: an empty array rather than one empty line.
  assert.deepEqual(splitLines(''), []);
});

test('joining restores the text exactly', () => {
  // 行自带换行符，拼回去必须逐字节相同——预览显示的就是拼回来的结果。
  // Each line carries its terminator, so joining has to restore the bytes exactly: what
  // the preview shows is the joined result.
  const text = 'a\r\nb\rc\nd';
  assert.equal(joinLines(splitLines(text)), text);
});
