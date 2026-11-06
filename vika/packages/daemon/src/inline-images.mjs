// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import { imageSize } from 'image-size';

// Images a tool hands back (get_message_media) travel inline to the model as
// base64. The connector keeps the original on disk and the tool's text names
// its path; only the inline copy is reduced here, so a vision model gets a
// readable picture without a multi-megabyte payload per image in its context,
// its provider request, and every runtime event that echoes the result.
export const MAX_INLINE_IMAGE_FILE_BYTES = 10_000_000;
export const INLINE_IMAGE_LIMITS = Object.freeze({ maxWidth: 2000, maxHeight: 2000, maxBytes: 1_000_000 });
// When the image cannot be reduced (no decoder for it, say), the original
// still goes inline up to the size providers commonly accept per image.
export const INLINE_IMAGE_FALLBACK_BYTES = 4_500_000;

function base64Bytes(bytes) {
  return Math.ceil(bytes / 3) * 4;
}

function fitsAsIs(bytes) {
  if (base64Bytes(bytes.length) > INLINE_IMAGE_LIMITS.maxBytes) return false;
  let dimensions;
  try { dimensions = imageSize(bytes); } catch { return true; }
  return !(dimensions.width > INLINE_IMAGE_LIMITS.maxWidth || dimensions.height > INLINE_IMAGE_LIMITS.maxHeight);
}

let defaultResizer = null;
// Pi's Photon (WASM) resizer, already a daemon dependency: no native build.
// Loaded on the first image that needs it, so tool calls without one stay
// cheap.
function loadResizer() {
  defaultResizer ??= import('@earendil-works/pi-coding-agent').then(
    (module) => (typeof module.resizeImage === 'function' ? module.resizeImage : null),
    () => null,
  );
  return defaultResizer;
}

async function inlineImage(item, resizer) {
  const mimeType = String(item?.mime_type ?? '');
  if (!mimeType.startsWith('image/')) return [];
  if (!Number.isFinite(item.size) || item.size > MAX_INLINE_IMAGE_FILE_BYTES) return [];
  let bytes;
  try { bytes = fs.readFileSync(item.path); } catch { return []; }
  if (fitsAsIs(bytes)) return [{ type: 'image', data: bytes.toString('base64'), mimeType }];

  const resizeImage = await resizer();
  if (typeof resizeImage === 'function') {
    try {
      const resized = await resizeImage(new Uint8Array(bytes), mimeType, { ...INLINE_IMAGE_LIMITS });
      if (resized?.data && resized.mimeType) return [{ type: 'image', data: resized.data, mimeType: resized.mimeType }];
    } catch {}
  }
  if (base64Bytes(bytes.length) <= INLINE_IMAGE_FALLBACK_BYTES) {
    return [{ type: 'image', data: bytes.toString('base64'), mimeType }];
  }
  const name = String(item.name || 'image');
  return [{ type: 'text', text: `[${name} is not shown inline: it could not be reduced to a size the model accepts. The original is at ${item.path}.]` }];
}

// Content blocks for the images among a tool's media, in order. A `resizer`
// returning Pi's resizeImage signature can be injected for tests.
export async function inlineImageBlocks(media = [], { resizer = loadResizer } = {}) {
  const blocks = [];
  for (const item of Array.isArray(media) ? media : []) blocks.push(...await inlineImage(item, resizer));
  return blocks;
}
