// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { preparePrivateDirectory } from '../private-temp.mjs';
import { readWorkspaceFile } from '../workspace-file.mjs';
import { IMAGE_ALIAS, MAX_CARD_IMAGES, MAX_CARD_IMAGE_BYTES, cardImageFormat, checkCardImageTemplate, validateCard } from '../card-images.mjs';
export { MAX_CARD_BYTES } from '../card-images.mjs';

export const MAX_FILE_BYTES = 20 * 1024 * 1024;

export const CONTENT_PROPERTIES = {
  markdown: { type: 'string', minLength: 1, description: 'Message text (Markdown where the connector supports it). Mutually exclusive with file_path/file_key/card/choices.' },
  file_path: { type: 'string', minLength: 1, description: 'Local file relative to the agent workspace, at most 20 MiB; images are sent as images. No URLs, absolute paths, traversal, or symlinks escaping the workspace.' },
  file_key: { type: 'string', pattern: '^file_[A-Za-z0-9_-]{8,}$', description: 'Lark only: existing IM file key from trusted tool output, sent without uploading.' },
  card: { type: 'object', description: 'Lark only: inline interactive card JSON (Card 1.0 elements or Card 2.0 body.elements), at most 30 KiB. Callback controls arrive as user input, never executed by the daemon. For a single-choice question prefer choices.' },
  card_images: { type: 'object', minProperties: 1, maxProperties: MAX_CARD_IMAGES,
    propertyNames: { pattern: IMAGE_ALIAS }, additionalProperties: { type: 'string', minLength: 1, maxLength: 4096 },
    description: 'Lark only, only with card: map unique aliases to workspace-relative images (JPEG/PNG/GIF/WebP/BMP/ICO/TIFF, nonempty, at most 10 MiB each). Use each alias in a complete img_key value {{image:alias}}. Up to 32 aliases; identical bytes upload once. Resources belong to the card, not a second body.' },
  choices: { type: 'object', additionalProperties: false, required: ['prompt', 'options'], description: 'Lark only: a single-choice question card. The selected key later arrives as authenticated card input.', properties: {
    prompt: { type: 'string', minLength: 1, maxLength: 2000 },
    options: { type: 'array', minItems: 2, maxItems: 10, items: {
      type: 'object', additionalProperties: false, required: ['key', 'label'], properties: {
        key: { type: 'string', pattern: '^[A-Za-z0-9_-]{1,64}$' },
        label: { type: 'string', minLength: 1, maxLength: 100 },
      },
    } },
    answerer_id: { type: 'string', pattern: '^ou_[A-Za-z0-9_-]{8,}$', description: 'Optional user allowed to answer. Defaults to the author of decision_id.' },
    timeout_seconds: { type: 'integer', minimum: 30, maximum: 86400, default: 600 },
  } },
};

const BODY_FIELDS = ['markdown', 'file_path', 'file_key', 'card', 'choices'];

function freezeCardImages(card, images, { workspace, tempDir }) {
  if (!images || typeof images !== 'object' || Array.isArray(images) || !Object.keys(images).length) {
    throw new Error('card_images must be a nonempty alias-to-path object');
  }
  const aliases = Object.keys(images).sort();
  checkCardImageTemplate(card, aliases);
  preparePrivateDirectory(tempDir);
  const directory = fs.mkdtempSync(path.join(preparePrivateDirectory(path.join(tempDir, 'outbox')), 'card-'));
  const cleanup = () => fs.rmSync(directory, { recursive: true, force: true });
  const byDigest = new Map();
  const assets = [];
  try {
    for (const alias of aliases) {
      const { bytes } = readWorkspaceFile(images[alias], workspace, MAX_CARD_IMAGE_BYTES);
      const format = cardImageFormat(bytes);
      const digest = createHash('sha256').update(bytes).digest('hex');
      if (!byDigest.has(digest)) {
        const filename = path.join(directory, `${digest}.${format}`);
        fs.writeFileSync(filename, bytes, { mode: 0o600 });
        byDigest.set(digest, filename);
      }
      assets.push({ alias, digest, path: byDigest.get(digest) });
    }
    const json = validateCard(card);
    return { type: 'card', payload: { type: 'card', card: JSON.parse(json), card_images: assets },
      canonical: ['card', json, assets.map(({ alias, digest }) => [alias, digest])],
      summary: '[interactive card with images]', cleanup };
  } catch (error) { cleanup(); throw error; }
}

export function choiceCard(question, answer = null) {
  const elements = [{ tag: 'div', text: { tag: 'plain_text', content: question.prompt } }];
  if (answer) {
    elements.push({ tag: 'div', text: { tag: 'plain_text', content: `已选择：${answer.label}` } });
  } else {
    for (let index = 0; index < question.options.length; index += 4) {
      elements.push({ tag: 'action', actions: question.options.slice(index, index + 4).map((option) => ({
        tag: 'button', text: { tag: 'plain_text', content: option.label }, type: 'default',
        value: { vika_choice: option.key },
      })) });
    }
  }
  return { config: { wide_screen_mode: true, update_multi: true }, elements };
}

// Resolves exactly one message body into the Connector payload, the canonical
// form hashed into the idempotency key, and a journal summary. A file is frozen
// into a private snapshot so the upload cannot change after validation; the
// caller must call cleanup() once the delivery attempt is over.
export function resolveContent(input, { workspace, tempDir, maxTextChars }) {
  const present = BODY_FIELDS.filter((key) => input[key] !== undefined);
  if (present.length !== 1) {
    throw new Error(present.length
      ? 'provide exactly one of markdown, file_path, file_key, card, or choices'
      : 'a message body is required: markdown, file_path, file_key, card, or choices');
  }
  const kind = present[0];
  const value = input[kind];
  const none = () => {};
  if (input.card_images !== undefined && kind !== 'card') throw new Error('card_images can only accompany card');
  if (kind === 'markdown') {
    if (typeof value !== 'string' || !value.trim()) throw new Error('markdown must not be empty');
    if (value.length > maxTextChars) throw new Error(`text exceeds the ${maxTextChars}-character limit`);
    return { type: 'markdown', payload: { type: 'markdown', text: value }, canonical: ['markdown', value], summary: value, cleanup: none };
  }
  if (input.mention_names?.length || input.mention_user_ids?.length) {
    throw new Error('mentions are supported only with markdown bodies');
  }
  if (kind === 'file_key') {
    return { type: 'file_key', payload: { type: 'file_key', key: value }, canonical: ['file_key', value], summary: '[file attachment]', cleanup: none };
  }
  if (kind === 'file_path') {
    const { name, bytes } = readWorkspaceFile(value, workspace);
    const digest = createHash('sha256').update(bytes).digest('hex');
    // Upload from a private snapshot under the same configurable temp root
    // as downloads, even when the original workspace is under /root.
    preparePrivateDirectory(tempDir);
    const snapshotDir = preparePrivateDirectory(path.join(tempDir, 'outbox'));
    const directory = fs.mkdtempSync(path.join(snapshotDir, 'upload-'));
    const snapshot = path.join(directory, name);
    try {
      fs.writeFileSync(snapshot, bytes, { mode: 0o600 });
    } catch (error) {
      fs.rmSync(directory, { recursive: true, force: true });
      throw error;
    }
    return {
      type: 'file', payload: { type: 'file', path: snapshot, name }, canonical: ['file', name, digest],
      summary: `[file ${name}]`, cleanup: () => fs.rmSync(directory, { recursive: true, force: true }),
    };
  }
  if (kind === 'choices') {
    const keys = new Set(value.options.map((option) => option.key));
    if (keys.size !== value.options.length) throw new Error('choices options require unique keys');
    const question = {
      prompt: value.prompt, options: value.options.map(({ key, label }) => ({ key, label })),
      answerer_id: value.answerer_id, timeout_seconds: value.timeout_seconds ?? 600,
    };
    const card = choiceCard(question);
    return { type: 'card', payload: { type: 'card', card }, canonical: ['choices', question],
      summary: `[question] ${question.prompt}`, question, cleanup: none };
  }
  const json = validateCard(value);
  if (input.card_images !== undefined) return freezeCardImages(value, input.card_images, { workspace, tempDir });
  checkCardImageTemplate(value, []);
  return { type: 'card', payload: { type: 'card', card: value }, canonical: ['card', json], summary: '[interactive card]', cleanup: none };
}
