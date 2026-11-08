// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { OVUnavailableError } from './ov-connection.mjs';
import { DEFAULT_PROMPT_DIR, substitutePromptPlaceholders } from './conversation-policy.mjs';

export const DEFAULT_PERSONALITY = 'outgoing';
export const PERSONALITIES = Object.freeze(['outgoing', 'reserved']);

// Each preset has its own editable file in the instance's OV identity. Switching
// presets keeps edits to the previous one. Normal memory extraction never writes
// identity; chat content is not promoted to policy by this reader.
export async function loadOVPersonality(client, config = {}, { onCached = () => {} } = {}) {
  // Bind the last successful read to this instance's exact OV configuration
  // and preset. A different account, key, server or selection never reuses it.
  const cache = config.dataDir && config.openVikingConfigPath
    ? path.join(config.dataDir, 'ov-personality.json') : null;
  const key = cache ? createHash('sha256').update(fs.readFileSync(config.openVikingConfigPath))
    .update('\0').update(config.personality ?? DEFAULT_PERSONALITY).digest('hex') : null;
  let text;
  try {
    text = await readPersonality(client, config);
  } catch (error) {
    const status = error.httpStatus;
    const unavailable = error instanceof OVUnavailableError
      && (status == null || status === 408 || status === 429 || status >= 500);
    if (!cache || !unavailable) throw error;
    let saved;
    try { saved = JSON.parse(fs.readFileSync(cache, 'utf8')); } catch { throw error; }
    if (saved.key !== key || typeof saved.text !== 'string' || !saved.text.trim() || saved.text.length > 32_000) throw error;
    onCached();
    return substitutePromptPlaceholders(saved.text, config).trim();
  }
  if (cache) {
    fs.mkdirSync(config.dataDir, { recursive: true, mode: 0o700 });
    const temporary = `${cache}.${randomUUID()}.tmp`;
    try {
      fs.writeFileSync(temporary, JSON.stringify({ key, text }), { mode: 0o600 });
      fs.renameSync(temporary, cache);
    } finally { fs.rmSync(temporary, { force: true }); }
  }
  return substitutePromptPlaceholders(text, config).trim();
}

async function readPersonality(client, config) {
  const preset = config.personality ?? DEFAULT_PERSONALITY;
  if (!PERSONALITIES.includes(preset)) throw new Error('Unknown personality preset');
  const uri = `viking://~/memories/identity/personalities/${preset}.md`;
  let text;
  try {
    text = await client.read(uri, 0, -1);
  } catch (error) {
    if (error.httpStatus !== 404) throw error;
    const seed = fs.readFileSync(path.join(config.promptDir || DEFAULT_PROMPT_DIR, 'personalities', `${preset}.md`), 'utf8');
    await client.request('/content/write', { uri, content: seed, mode: 'create', wait: false });
    // Always inject what OV stores, including edits, not the shipped seed.
    text = await client.read(uri, 0, -1);
  }
  if (typeof text !== 'string' || !text.trim() || text.length > 32_000) {
    throw new Error('OpenViking personality must be nonempty text of at most 32000 characters');
  }
  text = text.replace(/<!--[\s\S]*?-->/g, '').trim();
  if (!text) throw new Error('OpenViking personality is empty');
  return text;
}
