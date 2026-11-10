// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { imageSize } from 'image-size';

export const MAX_CARD_BYTES = 30 * 1024;
export const MAX_CARD_IMAGE_BYTES = 10 * 1024 * 1024;
export const MAX_CARD_IMAGES = 32;
export const IMAGE_ALIAS = '^[A-Za-z][A-Za-z0-9_-]{0,31}$';
// Reserve the maximum accepted key length before any upload. Actual provider
// keys must fit this bound, so substitution cannot overflow the card budget.
export const MAX_IMAGE_KEY_LENGTH = 256;
const PLACEHOLDER = /^\{\{image:([A-Za-z][A-Za-z0-9_-]{0,31})\}\}$/;

export function validImageKey(key) {
  return typeof key === 'string' && key.length <= MAX_IMAGE_KEY_LENGTH && key.match(/^img_[A-Za-z0-9_-]+$/)?.[0] === key;
}

export function validateCard(card) {
  if (!card || typeof card !== 'object' || Array.isArray(card)
    || (card.schema && card.schema !== '2.0')
    || !Array.isArray(card.schema === '2.0' ? card.body?.elements : card.elements)) {
    throw new Error('card must be an inline Card 1.0 or Card 2.0 object with elements');
  }
  const json = JSON.stringify(card);
  if (Buffer.byteLength(json) > MAX_CARD_BYTES) throw new Error('card exceeds 30 KiB');
  if (/<at\b/i.test(json) || /"tag"\s*:\s*"at"/i.test(json)) {
    throw new Error('raw card mention tags are unsupported; use a separate explicit message');
  }
  return json;
}

// Walk fields, never replace serialized JSON. Any placeholder outside an
// exact img_key value is an error rather than text silently sent to the user.
export function replaceCardImages(card, keys) {
  const copy = JSON.parse(validateCard(card));
  const used = new Set();
  function visit(value) {
    if (!value || typeof value !== 'object') return;
    for (const [field, item] of Object.entries(value)) {
      if (field.includes('{{image:')) throw new Error('image placeholders must be complete img_key values');
      if (typeof item === 'string' && item.includes('{{image:')) {
        const match = field === 'img_key' && item.match(PLACEHOLDER);
        if (!match || match[0] !== item) throw new Error('image placeholders must be complete img_key values');
        const alias = match[1];
        if (!keys.has(alias)) throw new Error(`missing card_images alias: ${alias}`);
        const key = keys.get(alias);
        if (!validImageKey(key)) throw new Error(`invalid image_key for card_images alias: ${alias}`);
        value[field] = key;
        used.add(alias);
      } else visit(item);
    }
  }
  visit(copy);
  for (const alias of keys.keys()) {
    if (!used.has(alias)) throw new Error(`unused card_images alias: ${alias}`);
  }
  validateCard(copy);
  return copy;
}

export function checkCardImageTemplate(card, aliases) {
  if (new Set(aliases).size !== aliases.length) throw new Error('card_images aliases must be unique');
  if (aliases.length > MAX_CARD_IMAGES || aliases.some((alias) => alias.match(new RegExp(IMAGE_ALIAS))?.[0] !== alias)) {
    throw new Error(`card_images requires at most ${MAX_CARD_IMAGES} aliases matching ${IMAGE_ALIAS}`);
  }
  replaceCardImages(card, new Map(aliases.map((alias) => [alias, `img_${'x'.repeat(MAX_IMAGE_KEY_LENGTH - 4)}`])));
}

export function cardImageFormat(bytes) {
  if (!bytes.length || bytes.length > MAX_CARD_IMAGE_BYTES) throw new Error('card image must be nonempty and at most 10 MiB');
  let image;
  try { image = imageSize(bytes); } catch { throw new Error('card image has an invalid image header'); }
  const extensions = { jpg: 'jpg', png: 'png', gif: 'gif', webp: 'webp', bmp: 'bmp', ico: 'ico', tiff: 'tiff' };
  if (!Object.hasOwn(extensions, image.type)) throw new Error('card image must be JPEG, PNG, GIF, WebP, BMP, ICO or TIFF');
  const limit = image.type === 'gif' ? 2000 : 12000;
  for (const dimensions of [image, ...(image.images ?? [])]) {
    if (!(dimensions.width > 0 && dimensions.height > 0 && dimensions.width <= limit && dimensions.height <= limit)) {
      throw new Error(`card image dimensions must be within ${limit} x ${limit}`);
    }
  }
  return extensions[image.type];
}
