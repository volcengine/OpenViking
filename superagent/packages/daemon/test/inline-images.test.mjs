// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import { describe, it } from 'node:test';
import { imageSize } from 'image-size';
import { INLINE_IMAGE_LIMITS, inlineImageBlocks } from '../src/inline-images.mjs';

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(zlib.crc32(body));
  return Buffer.concat([length, body, crc]);
}

// An RGB PNG of random pixels: it does not compress, so its size is close to
// width * height * 3, like a large screenshot or photo sent from a phone.
function noisePng(width, height) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 2; // RGB
  const rows = [];
  for (let y = 0; y < height; y += 1) rows.push(Buffer.from([0]), crypto.randomBytes(width * 3));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', zlib.deflateSync(Buffer.concat(rows), { level: 0 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

function mediaFile(t, name, bytes, mimeType = 'image/png') {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-inline-images-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const file = path.join(directory, name);
  fs.writeFileSync(file, bytes);
  return { path: file, name, kind: 'image', mime_type: mimeType, size: bytes.length };
}

describe('inlineImageBlocks', () => {
  it('passes a small image through untouched without loading the resizer', async (t) => {
    const small = noisePng(64, 48);
    let loaded = false;
    const blocks = await inlineImageBlocks(
      [mediaFile(t, 'small.png', small), { path: '/x.pdf', name: 'x.pdf', mime_type: 'application/pdf', size: 3 }],
      { resizer: async () => { loaded = true; return null; } },
    );
    assert.deepEqual(blocks, [{ type: 'image', data: small.toString('base64'), mimeType: 'image/png' }]);
    assert.equal(loaded, false);
  });

  it('shrinks a large image with Pi\'s resizer below the inline limits', async (t) => {
    const large = noisePng(2400, 1000); // ~7.2 MB, wider than the 2000px edge
    const blocks = await inlineImageBlocks([mediaFile(t, 'large.png', large)]);

    assert.equal(blocks.length, 1);
    assert.equal(blocks[0].type, 'image');
    assert.ok(blocks[0].data.length <= INLINE_IMAGE_LIMITS.maxBytes, `inline image is ${blocks[0].data.length} base64 bytes`);
    const { width, height } = imageSize(Buffer.from(blocks[0].data, 'base64'));
    assert.ok(width <= INLINE_IMAGE_LIMITS.maxWidth && height <= INLINE_IMAGE_LIMITS.maxHeight, `${width}x${height}`);
    assert.ok(width / height > 2.3 && width / height < 2.5, 'aspect ratio kept');
  });

  it('falls back to the original only while it is modest, and otherwise names the file instead', async (t) => {
    const noResizer = { resizer: async () => null };
    const modest = noisePng(700, 700); // ~1.5 MB: too big to pass as is, small enough to send
    const [kept] = await inlineImageBlocks([mediaFile(t, 'modest.png', modest)], noResizer);
    assert.deepEqual(kept, { type: 'image', data: modest.toString('base64'), mimeType: 'image/png' });

    const huge = mediaFile(t, 'huge.png', noisePng(1500, 1500)); // ~6.75 MB
    const [note] = await inlineImageBlocks([huge], noResizer);
    assert.equal(note.type, 'text');
    assert.ok(note.text.includes(huge.path));
  });
});
