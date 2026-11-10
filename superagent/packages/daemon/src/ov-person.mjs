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

// Where people's preferences are kept, one directory each.
export const OV_PREFERENCES = 'viking://~/memories/preferences';
// A whole-tree scan reads this many entries a request. OV 0.5 pages with
// offset/has_more; earlier releases return one page of up to this many.
const SCAN_PAGE = 2_000;
const SCAN_PAGES = 50;
const leaf = (uri) => String(uri ?? '').replace(/\/+$/, '').split('/').pop() ?? '';

// One directory's entries, or null when it does not exist. `json(route)` is
// the caller's OV connection.
export async function listDirectory(json, uri) {
  try {
    const { result } = await json(`/api/v1/fs/ls?uri=${encodeURIComponent(uri)}&output=original`);
    return Array.isArray(result) ? result : [];
  } catch (error) {
    if (error?.httpStatus === 404) return null;
    throw error;
  }
}

// Every person with preferences: key (`owner` or their digits) to directory
// name, read page by page so no one is missed however many there are. Only
// needed for someone whose directory is not under the name they have now.
export async function scanPeople(json) {
  const people = new Map();
  for (let page = 0, offset = 0; page < SCAN_PAGES; page += 1) {
    let body;
    try {
      body = await json(`/api/v1/fs/ls?uri=${encodeURIComponent(OV_PREFERENCES)}&output=original`
        + `&node_limit=${SCAN_PAGE}&offset=${offset}`);
    } catch (error) {
      if (error?.httpStatus === 404) return people;
      throw error;
    }
    const entries = Array.isArray(body?.result) ? body.result : [];
    for (const entry of entries) {
      const whose = entry?.isDir ? directoryPerson(leaf(entry.uri)) : null;
      const key = whose?.owner ? OWNER_DIRECTORY : whose?.id;
      if (key && !people.has(key)) people.set(key, leaf(entry.uri));
    }
    if (!body?.has_more || entries.length === 0) return people;
    offset += entries.length;
  }
  return people;
}

// A person's directory ({ owner } or { id, directory }) and its entries, or
// null when they have none: the directory under their name now, in one
// request almost always; else, for someone renamed since, the one with their
// key from `scan()` (scanPeople, possibly cached by the caller).
export async function findPersonDirectory(json, person, scan = () => scanPeople(json)) {
  const direct = person.owner ? OWNER_DIRECTORY : person.directory;
  if (direct) {
    const entries = await listDirectory(json, `${OV_PREFERENCES}/${direct}`);
    if (entries?.length) return { name: direct, entries };
  }
  if (person.owner || !person.id) return null;
  const name = (await scan()).get(person.id);
  if (!name || name === direct) return null;
  const entries = await listDirectory(json, `${OV_PREFERENCES}/${name}`);
  return entries ? { name, entries } : null;
}
