// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';

export function mailboxName(value = 'INBOX') {
  if (typeof value !== 'string' || !value || /[\u0000-\u001f\u007f]/.test(value)) {
    throw new Error('email config imap.mailbox must be a nonempty mailbox name without control characters');
  }
  return /^inbox$/i.test(value) ? 'INBOX' : value;
}

// INBOX retains its original ids and archive paths across upgrades.
export function mailboxIdentity(account, mailbox, validity, uid) {
  const name = mailboxName(mailbox);
  const folder = name === 'INBOX' ? '' : `${createHash('sha256').update(name).digest('hex').slice(0, 32)}:`;
  return `${account}:${folder}${validity}:${uid}`;
}

// IMAP4rev1 mailbox names use modified UTF-7; UTF-8 is native to rev2.
export function mailboxWireName(name, utf8 = false) {
  if (utf8) return name;
  return name.replace(/&|[^\x20-\x7e]+/g, (part) => {
    if (part === '&') return '&-';
    const bytes = Buffer.from(part, 'utf16le');
    bytes.swap16();
    return `&${bytes.toString('base64').replace(/\//g, ',').replace(/=+$/, '')}-`;
  });
}

// A bounded explicit set, with the legacy singular spelling retained.
export function configuredMailboxes(imap = {}) {
  if (imap.mailboxes !== undefined && imap.mailbox !== undefined) {
    throw new Error('email config uses either imap.mailbox or imap.mailboxes');
  }
  if (imap.mailboxes === undefined) return [mailboxName(imap.mailbox)];
  if (!Array.isArray(imap.mailboxes) || !imap.mailboxes.length || imap.mailboxes.length > 100) {
    throw new Error('email config imap.mailboxes must contain 1 to 100 mailbox names');
  }
  return [...new Set(imap.mailboxes.map(mailboxName))];
}

export function mailboxPollSeconds(value = 60) {
  if (!Number.isInteger(value) || value < 15 || value > 3600) {
    throw new Error('email config imap.pollIntervalSeconds must be an integer from 15 to 3600');
  }
  return value;
}

// Discovery is opt-in and replaces the explicit set, rather than silently
// subscribing existing accounts to all folders.
export function mailboxDiscovery(imap = {}) {
  const value = imap.discovery;
  if (value === undefined) return null;
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('imap.discovery must be an object');
  if (imap.mailbox !== undefined || imap.mailboxes !== undefined) throw new Error('imap.discovery cannot be combined with mailbox or mailboxes');
  const names = (key) => {
    const items = value[key] ?? [];
    if (!Array.isArray(items) || items.length > 100) throw new Error(`imap.discovery.${key} must be an array of at most 100 names`);
    return [...new Set(items.map(mailboxName))];
  };
  const refreshIntervalSeconds = value.refreshIntervalSeconds ?? 300;
  if (!Number.isInteger(refreshIntervalSeconds) || refreshIntervalSeconds < 15 || refreshIntervalSeconds > 86400) {
    throw new Error('imap.discovery.refreshIntervalSeconds must be an integer from 15 to 86400');
  }
  return { roots: names('roots'), exclude: names('exclude'), refreshIntervalSeconds };
}
