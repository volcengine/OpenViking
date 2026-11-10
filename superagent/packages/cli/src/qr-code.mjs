// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { deflateSync } from 'node:zlib';
import QRCode from 'qrcode-terminal/vendor/QRCode/index.js';
import QRErrorCorrectLevel from 'qrcode-terminal/vendor/QRCode/QRErrorCorrectLevel.js';

// Use the encoder from our pinned qrcode-terminal dependency. Its small
// renderer assumes a dark terminal and leaves only a one-module border.
// Share one padded matrix between the terminal and a font-independent PNG.
const QUIET = 4;

export function qrMatrix(text) {
  const code = new QRCode(-1, QRErrorCorrectLevel.L);
  // The bundled encoder writes one byte per JS character, not UTF-8.
  code.addData(Buffer.from(text, 'utf8').toString('latin1'));
  code.make();
  const count = code.getModuleCount();
  return Array.from({ length: count + QUIET * 2 }, (_, y) =>
    Array.from({ length: count + QUIET * 2 }, (_, x) =>
      y >= QUIET && y < count + QUIET && x >= QUIET && x < count + QUIET
        ? code.isDark(y - QUIET, x - QUIET) : false));
}

// A terminal fills a cell's background edge to edge, but draws a glyph only as
// big as the font or renderer makes it: line height, letter spacing and
// font-drawn block characters leave seams around it (VS Code with or without
// GPU, macOS Terminal). Seams through dark modules cut the finder patterns
// apart and WeChat cannot scan the code; seams through light ones it ignores.
// So dark modules are always background, and only light modules are glyphs.
// One character carries two vertically adjacent modules. With colour, a cell
// of two light modules is a white space and every other cell is black, its
// light half a white half block; without, the terminal is assumed dark.
export function terminalQr(matrix, depth = 1) {
  const color = depth >= 4;
  const [paper, ink] = depth >= 24 ? ['48;2;255;255;255', '48;2;0;0;0;38;2;255;255;255']
    : depth >= 8 ? ['48;5;231', '48;5;16;38;5;231'] : ['107', '40;97'];
  const lines = [];
  for (let y = 0; y < matrix.length; y += 2) {
    let line = '', style = null;
    for (let x = 0; x < matrix.length; x += 1) {
      const top = matrix[y][x], bottom = matrix[y + 1]?.[x] ?? false;
      const light = top ? (bottom ? ' ' : '▄') : (bottom ? '▀' : '█');
      if (!color) { line += light; continue; }
      const next = top || bottom ? ink : paper;
      if (next !== style) { line += `\x1b[0;${next}m`; style = next; }
      line += next === paper ? ' ' : light;
    }
    lines.push(color ? `${line}\x1b[0m` : line);
  }
  return lines;
}

const CRC_TABLE = Array.from({ length: 256 }, (_, value) => {
  for (let bit = 0; bit < 8; bit += 1) value = (value & 1) ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
  return value >>> 0;
});

function chunk(type, bytes) {
  const data = Buffer.concat([Buffer.from(type, 'ascii'), bytes]);
  let crc = 0xffffffff;
  for (const byte of data) crc = CRC_TABLE[(crc ^ byte) & 255] ^ (crc >>> 8);
  const size = Buffer.alloc(4), checksum = Buffer.alloc(4);
  size.writeUInt32BE(bytes.length); checksum.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
  return Buffer.concat([size, data, checksum]);
}

export function qrPng(matrix) {
  const scale = Math.max(2, Math.min(8, Math.floor(640 / matrix.length)));
  const size = matrix.length * scale;
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0); header.writeUInt32BE(size, 4);
  header[8] = 8; // 8-bit grayscale, no alpha or palette.
  const pixels = Buffer.alloc((size + 1) * size, 255);
  for (let y = 0; y < size; y += 1) {
    const row = y * (size + 1);
    pixels[row] = 0; // PNG filter: None.
    for (let x = 0; x < size; x += 1) {
      pixels[row + x + 1] = matrix[Math.floor(y / scale)][Math.floor(x / scale)] ? 0 : 255;
    }
  }
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', header), chunk('IDAT', deflateSync(pixels)), chunk('IEND', Buffer.alloc(0))]);
}
