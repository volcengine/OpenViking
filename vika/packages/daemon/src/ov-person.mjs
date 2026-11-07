// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';
import { senderLabel } from './runtime-input.mjs';

// Whose preferences a directory under memories/preferences/ holds. The Core
// names it from the authenticated sender of the input a preference came from;
// neither the model nor OV's extraction picks it, so a preference can only
// land with the person who stated it.
// - The owner's is `owner`: one directory on every channel, and the owner's
//   preferences are the agent's standing instructions.
// - Anyone else's is their name when first seen, `~`, and 12 hex digits of
//   their channel identity: `张三~3dace6ed510e`. The digits are the key.
//   Namesakes get different ones, a renamed person keeps theirs, and no one
//   else can produce `owner`. The name is there for people reading the tree.
// A person is one identity on one channel: the same human on Lark and in
// mail has two directories, since nothing proves the two are one person.

export const OWNER_DIRECTORY = 'owner';
const ID_DIGITS = 12;
const NAME_CHARS = 32;

// The digits that tell one person from everyone else, or null for an input
// nobody sent (a reminder) or a bot.
export function personId(event) {
  if (!event?.senderId || event.senderId === 'unknown' || ['bot', 'app'].includes(event.senderType)) return null;
  const email = event.senderType === 'email' || event.source === 'email';
  const id = email ? String(event.senderId).replace(/^email:/i, '').toLowerCase() : String(event.senderId);
  return createHash('sha256').update(`${event.source ?? ''}\n${event.tenantKey ?? ''}\n${id}`).digest('hex').slice(0, ID_DIGITS);
}

// A name safe as one path segment: no separators, no `~`, no leading dot.
function segmentName(name) {
  const clean = String(name ?? '').normalize('NFC')
    .replace(/[\u0000-\u001f\u007f/\\~%?#]+/g, '').replace(/\s+/g, '_').replace(/^[._]+/, '').slice(0, NAME_CHARS);
  return clean || 'someone';
}

// The directory a new preference of this sender goes to, or null when the
// input has no person behind it.
export function personDirectory(event, { owner = false } = {}) {
  if (owner) return OWNER_DIRECTORY;
  const id = personId(event);
  return id ? `${segmentName(senderLabel(event))}~${id}` : null;
}

// Whose a directory is: { owner: true }, { id } for anyone else, or null for
// one the Core never named (an older extraction's name-only directory).
export function directoryPerson(name) {
  const value = String(name ?? '');
  if (value === OWNER_DIRECTORY) return { owner: true };
  const match = new RegExp(`~([0-9a-f]{${ID_DIGITS}})$`).exec(value);
  return match ? { id: match[1] } : null;
}

// The file a topic is kept in: one path segment, the same file for the same
// topic however the model spells its case and spacing.
export function topicFile(topic) {
  const clean = String(topic ?? '').normalize('NFC').trim().toLowerCase()
    .replace(/[\u0000-\u001f\u007f/\\~%?#]+/g, '').replace(/[\s_]+/g, '-').replace(/^[.-]+|-+$/g, '').slice(0, 60);
  return clean ? `${clean}.md` : null;
}
