// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { parseUniqueJson } from '../unique-json.mjs';

// The owner's hand-written policy: which groups the agent is in and how
// closely it listens there. A typo must fail loudly rather than silently
// disconnect a group, so unknown keys and repeated keys are errors.

const KEYS = new Set(['groups', 'private', 'commandPrefixes', 'names', 'settleMs']);
const MODES = new Set(['ambient', 'mention']);
const PRIVATE = new Set(['owner', 'friends']);
const FILE_BYTES = 256 * 1024;
const ENTRY_CHARS = 32;
const label = (key) => JSON.stringify(String(key).slice(0, 40));

function entries(value, key, max) {
  if (value === undefined) return Object.freeze([]);
  if (!Array.isArray(value) || value.length > max) throw new Error(`OneBot policy ${key} must be a list of at most ${max} entries`);
  for (const item of value) {
    if (typeof item !== 'string' || !item || [...item].length > ENTRY_CHARS
      || /[\u0000-\u001f\u007f]/.test(item) || /^\s/.test(item)) {
      throw new Error(`OneBot policy ${key} entries must be 1–${ENTRY_CHARS} characters of one-line text, not starting with a space`);
    }
  }
  return Object.freeze([...new Set(value)]);
}

export function parsePolicy(text) {
  let value;
  try { value = parseUniqueJson(text); } catch (error) {
    throw new Error(String(error.message).startsWith('duplicate JSON key') ? `OneBot policy has a ${error.message}` : 'OneBot policy is not valid JSON');
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('OneBot policy must be a JSON object');
  for (const key of Object.keys(value)) if (!KEYS.has(key)) throw new Error(`OneBot policy key ${label(key)} is unknown`);
  const groupsInput = value.groups === undefined ? {} : value.groups;
  if (!groupsInput || typeof groupsInput !== 'object' || Array.isArray(groupsInput)) throw new Error('OneBot policy groups must be an object');
  const groups = new Map();
  for (const [id, mode] of Object.entries(groupsInput)) {
    if (!/^[1-9]\d{0,15}$/.test(id) || !Number.isSafeInteger(Number(id))) throw new Error(`OneBot policy group id ${label(id)} must be a group number`);
    if (!MODES.has(mode)) throw new Error(`OneBot policy group ${id} must be "ambient" or "mention"`);
    groups.set(id, mode);
  }
  const privateChats = value.private === undefined ? 'owner' : value.private;
  if (!PRIVATE.has(privateChats)) throw new Error('OneBot policy private must be "owner" or "friends"');
  const settleMs = value.settleMs === undefined ? 3000 : value.settleMs;
  if (!Number.isInteger(settleMs) || settleMs < 0 || settleMs > 60_000) throw new Error('OneBot policy settleMs must be an integer from 0 to 60000');
  return Object.freeze({
    groups, private: privateChats, commandPrefixes: entries(value.commandPrefixes, 'commandPrefixes', 50),
    names: entries(value.names, 'names', 10), settleMs,
  });
}

export const EMPTY_POLICY = parsePolicy('{}');

// `<dataDir>/onebot/config.json`, read again whenever it changes. A changed
// file that does not parse keeps the last good policy, because a half-saved
// edit must not drop every group; with no good policy yet it is an error.
// Missing is the empty policy.
export class PolicyFile {
  constructor(dataDir, logger = null) {
    if (!path.isAbsolute(dataDir)) throw new Error('OneBot data directory must be absolute');
    this.path = path.join(dataDir, 'onebot', 'config.json');
    this.logger = logger;
    this.stamp = null;
    this.policy = null;
  }

  current() {
    let stat = null;
    let stamp = 'missing';
    try {
      stat = fs.statSync(this.path);
      stamp = `${stat.ino}:${stat.size}:${stat.mtimeMs}`;
    } catch (error) {
      if (error.code !== 'ENOENT') stamp = `error:${error.code}`;
    }
    if (stamp === this.stamp) return this.policy;
    let policy;
    try {
      if (stamp.startsWith('error:')) throw new Error(`OneBot policy cannot be read (${stamp.slice(6)})`);
      if (!stat) policy = EMPTY_POLICY;
      else if (!stat.isFile() || stat.size > FILE_BYTES) throw new Error(`OneBot policy must be a regular file of at most ${FILE_BYTES / 1024} KiB`);
      else policy = parsePolicy(fs.readFileSync(this.path, 'utf8'));
    } catch (error) {
      if (!this.policy) throw new Error(`${error.message}: ${this.path}`);
      this.stamp = stamp;
      this.logger?.warn('onebot_policy_invalid', { path: this.path, error: error.message });
      return this.policy;
    }
    this.stamp = stamp;
    this.policy = policy;
    return policy;
  }
}

export function groupMode(policy, chat) {
  const match = /^group:(\d+)$/.exec(String(chat ?? ''));
  return (match && policy.groups.get(match[1])) ?? null;
}

// Which chats exist for the agent: the groups the policy lists, and the
// owner's private chat or, with `"private": "friends"`, every private chat
// (normalizeMessage keeps friends only). Everything else is neither
// delivered nor answered.
export function admitted(policy, account, chat) {
  const match = /^(group|private):(\d+)$/.exec(String(chat ?? ''));
  if (!match || !account) return false;
  if (match[1] === 'group') return policy.groups.has(match[2]);
  return match[2] === String(account.ownerId) || policy.private === 'friends';
}

// What an admitted line is to the agent:
// `direct` wakes it now, `ambient` once the chat is quiet, `context` only
// rides along with the next release, `drop` is never shown. `text` is the
// message's `bareText` from normalizeMessage: what was typed, after a leading
// quote and @self. `spoken` is its `spokenText`, the text without any
// @mention, where `names` are looked for.
export function classify({ kind, chat, self, own, fromOwner, text, spoken, atSelf, replyToOwn }, policy) {
  const typed = String(text ?? '').trimStart();
  // The agent's own words as an event would wake it with them. A private `/`
  // line sent from the account by another client would run as a daemon
  // command (see below), and it is not the owner's.
  if (self) return own || (kind === 'private' && typed.startsWith('/')) ? 'drop' : 'context';
  const command = policy.commandPrefixes.some((prefix) => typed.startsWith(prefix));
  if (kind === 'private') {
    // An exchange with the other program on the account; secrets typed
    // there must not reach the model.
    if (command) return 'drop';
    // The Core runs a leading `/` in any direct chat as a daemon command for
    // whoever sent it (commands.mjs commandCandidate, daemon.mjs
    // handleSlashCommand), and /new or /model would change the session every
    // chat shares: only the owner's reach it.
    if (!fromOwner && typed.startsWith('/')) return 'drop';
    return fromOwner || policy.private === 'friends' ? 'direct' : 'drop';
  }
  const mode = groupMode(policy, chat);
  if (kind !== 'group' || !mode) return 'drop';
  if (command) return 'context';
  // An @ of someone whose name contains one of `names` does not address the agent.
  const lower = String(spoken ?? '').toLowerCase();
  if (atSelf || replyToOwn || policy.names.some((name) => lower.includes(name.toLowerCase()))) return 'direct';
  return mode === 'ambient' ? 'ambient' : 'context';
}
