// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import { PassThrough } from 'node:stream';
import { inflateSync, crc32 } from 'node:zlib';
import test from 'node:test';
import { qrMatrix, qrPng, terminalQr } from '../src/qr-code.mjs';
import { showScan } from '../src/qr.mjs';
import { setLanguage } from '../src/i18n.mjs';
import { Prompter } from '../src/prompt.mjs';
import { stripAnsi, width } from '../src/ui.mjs';

const TELEGRAM = 'https://t.me/superagent_test_bot?start=abcdefghijklmnopqrstuvwxyz012345';
const FEISHU = `https://open.feishu.cn/page/cli?addons=${'x'.repeat(1500)}`;

function screen(columns = 100, rows = 40, interactive = false) {
  const output = new PassThrough();
  Object.assign(output, { isTTY: true, columns, rows, getColorDepth: () => 24 });
  const input = new PassThrough();
  if (interactive) Object.assign(input, { isTTY: true, setRawMode() {} });
  let text = ''; output.on('data', (chunk) => { text += chunk; });
  return { output, input, text: () => text, prompter: new Prompter({ input, output, env: {} }) };
}

// [top, bottom] dark per cell. A glyph only ever draws light modules; with
// colour, a white-background cell is all light, a black one is dark where no
// glyph covers it.
const PAPER = new Set(['48;2;255;255;255', '48;5;231', '107']);
const INK = new Set(['48;2;0;0;0;38;2;255;255;255', '48;5;16;38;5;231', '40;97']);
const GLYPH = { ' ': [true, true], '▀': [false, true], '▄': [true, false], '█': [false, false] };

function cells(line) {
  const result = [];
  let style = null;
  for (const [, sgr, ch] of line.matchAll(/\x1b\[0;([0-9;]+)m|\x1b\[0m|(.)/gu)) {
    if (sgr) style = sgr;
    else if (ch) result.push({ style, ch });
  }
  return result;
}

test('terminal QR preserves every module in half-height cells with four white modules of quiet space', () => {
  for (const url of [TELEGRAM, FEISHU]) {
    const matrix = qrMatrix(url);
    for (const depth of [1, 4, 8, 24]) {
      const lines = terminalQr(matrix, depth);
      assert.equal(lines.length, Math.ceil(matrix.length / 2));
      assert.ok(lines.every((line) => width(line) === matrix.length));
      if (depth >= 4) assert.ok(lines.every((line) => line.startsWith('\x1b[0;') && line.endsWith('\x1b[0m')));
      const decoded = lines.flatMap((line) => {
        const pixels = cells(line).map(({ style, ch }) => {
          if (depth < 4) return GLYPH[ch];
          if (PAPER.has(style)) { assert.equal(ch, ' '); return [false, false]; }
          assert.ok(INK.has(style), style);
          assert.notEqual(ch, '█', 'a cell with a dark module is black, not a white glyph');
          return GLYPH[ch];
        });
        return [pixels.map(([top]) => top), pixels.map(([, bottom]) => bottom)];
      }).slice(0, matrix.length);
      assert.deepEqual(decoded, matrix);
    }
    matrix.forEach((row, y) => row.forEach((pixel, x) => {
      if (x < 4 || y < 4 || x >= matrix.length - 4 || y >= matrix.length - 4) assert.equal(pixel, false);
    }));
  }
});

test('PNG chunks have valid checksums and lossless black/white pixels at an integer scale', () => {
  for (const url of [TELEGRAM, FEISHU]) {
    const matrix = qrMatrix(url), png = qrPng(matrix);
    assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    const chunks = {};
    for (let at = 8; at < png.length;) {
      const length = png.readUInt32BE(at), type = png.toString('ascii', at + 4, at + 8);
      assert.equal(crc32(png.subarray(at + 4, at + 8 + length)), png.readUInt32BE(at + 8 + length));
      chunks[type] = png.subarray(at + 8, at + 8 + length); at += length + 12;
    }
    const size = chunks.IHDR.readUInt32BE(0);
    assert.equal(size, chunks.IHDR.readUInt32BE(4));
    assert.equal(size % matrix.length, 0);
    assert.equal(chunks.IHDR[8], 8); assert.equal(chunks.IHDR[9], 0);
    const pixels = inflateSync(chunks.IDAT), scale = size / matrix.length;
    assert.equal(pixels.length, size * (size + 1));
    for (let y = 0; y < size; y += 1) {
      assert.equal(pixels[y * (size + 1)], 0);
      for (let x = 0; x < size; x += 1) assert.equal(pixels[y * (size + 1) + x + 1],
        matrix[Math.floor(y / scale)][Math.floor(x / scale)] ? 0 : 255);
    }
  }
});

test('a code too big for the window says what it needs and where its private image is; resizing redraws it', (t) => {
  const s = screen();
  const scan = showScan(s.prompter, { url: FEISHU, title: '创建飞书', env: { TERM_PROGRAM: 'vscode' } });
  t.after(() => scan.stop());
  assert.equal(fs.statSync(scan.imagePath).mode & 0o777, 0o600);
  assert.equal(fs.statSync(scan.imagePath.slice(0, scan.imagePath.lastIndexOf('/'))).mode & 0o777, 0o700);
  assert.match(s.text(), /终端放不下这个二维码（要 \d+×\d+，现在 100×40）[\s\S]*也可以打开图片 \S+scan\.png/);
  assert.doesNotMatch(s.text(), /[▀▄█]/);
  Object.assign(s.output, { columns: 200, rows: 120 }); s.output.emit('resize');
  assert.match(s.text(), /\x1b\[0;48;2;255;255;255m/);
  const first = s.text(); s.output.emit('resize');
  assert.ok(s.text().length > first.length, 'redraw even after a QR was already shown');
  scan.stop();
  assert.equal(fs.existsSync(scan.imagePath), false);
  assert.equal(s.output.listenerCount('resize'), 0);
});

test('a code that fits comes first, its short link under it, and a long status cannot wrap it offscreen', (t) => {
  const s = screen(80, 25, true);
  const scan = showScan(s.prompter, { url: TELEGRAM, title: 'Telegram', label: '等待'.repeat(100),
    env: { TERM_PROGRAM: 'vscode' }, onBack() {} });
  t.after(() => scan.stop());
  const text = s.text();
  const codeAt = text.indexOf('\x1b[0;48;2;255;255;255m');
  assert.ok(codeAt >= 0 && codeAt < text.indexOf(TELEGRAM));
  assert.ok(!text.includes(scan.imagePath), 'the image is a key away, not a path on screen');
  assert.ok(width(text.slice(text.lastIndexOf('\r\x1b[2K'))) < 80);
  assert.match(text.slice(codeAt), /\r\n/);
});

test('a link too long to print waits for l, and the page speaks the run\'s language', async (t) => {
  setLanguage('en');
  t.after(() => setLanguage('zh'));
  const s = screen(200, 120, true);
  const scan = showScan(s.prompter, { url: FEISHU, title: 'Create', where: 'Lark', env: {} });
  t.after(() => scan.stop());
  assert.ok(!s.text().includes(FEISHU));
  assert.match(stripAnsi(s.text()), /Waiting for the scan 0:00 +│ [^\r\n]*\bl show link/);
  s.input.write('l');
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(stripAnsi(s.text()), new RegExp(`Can't scan it\\? Open in Lark: ${FEISHU.replace(/[?.]/g, '\\$&')}`));
  const small = screen(100, 40);
  showScan(small.prompter, { url: FEISHU, title: 'Create', env: {} }).stop();
  assert.match(small.text(), /This code needs a \d+×\d+ terminal \(now 100×40\): shrink the font or enlarge the window/);
});
