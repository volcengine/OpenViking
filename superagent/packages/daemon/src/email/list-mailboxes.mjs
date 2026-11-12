// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { parseResponse } from './imap.mjs';
import { mailboxName } from './mailbox.mjs';

// Response text arrives as latin1 byte-preserving strings; literals are Buffers.
function wireBytes(value) {
  if (Buffer.isBuffer(value)) return value;
  if (typeof value === 'string') return Buffer.from(value, 'latin1');
  if (value && typeof value.string === 'string') return Buffer.from(value.string, 'latin1');
  throw new Error('Invalid IMAP LIST string');
}

export function decodeMailboxName(value, { utf8 = false } = {}) {
  const bytes = wireBytes(value);
  if (utf8) return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
  if ([...bytes].some((byte) => byte < 0x20 || byte > 0x7e)) throw new Error('Invalid IMAP modified UTF-7 mailbox name');
  return bytes.toString('ascii').replace(/&([^-]*)-|&/g, (_, encoded) => {
    if (encoded === undefined) throw new Error('Unterminated IMAP modified UTF-7 shift');
    if (!encoded) return '&';
    if (!/^[A-Za-z0-9+,]+$/.test(encoded)) throw new Error('Invalid IMAP modified UTF-7 shift');
    const decoded = Buffer.from(encoded.replace(/,/g, '/'), 'base64');
    if (!decoded.length || decoded.length % 2 || decoded.toString('base64').replace(/\//g, ',').replace(/=+$/, '') !== encoded) {
      throw new Error('Invalid IMAP modified UTF-7 shift');
    }
    return new TextDecoder('utf-16be', { fatal: true, ignoreBOM: true }).decode(decoded);
  }).replace(/\u0000/g, () => { throw new Error('Invalid IMAP mailbox name'); });
}

// Return null for unrelated unsolicited responses. Malformed LIST results fail
// the refresh so the caller can keep its previous successful discovery snapshot.
export function parseListResponse(parts, { utf8 = false } = {}) {
  const tokens = parseResponse(parts);
  if (tokens[0] !== '*' || String(tokens[1]).toUpperCase() !== 'LIST') return null;
  const [, , attributes, separator, mailbox] = tokens;
  if (!Array.isArray(attributes) || mailbox === undefined || attributes.some((flag) => typeof flag !== 'string')) {
    throw new Error('Invalid IMAP LIST response');
  }
  const delimiter = typeof separator === 'string' && separator.toUpperCase() === 'NIL' ? null : new TextDecoder(utf8 ? 'utf-8' : 'latin1', { fatal: true }).decode(wireBytes(separator));
  if (delimiter !== null && [...delimiter].length !== 1) throw new Error('Invalid IMAP LIST delimiter');
  const name = mailboxName(decodeMailboxName(mailbox, { utf8 }));
  return { name, delimiter, flags: attributes.map((flag) => flag.toLowerCase()) };
}

export async function listMailboxes(imap) {
  const { untagged } = await imap.command('LIST', '"" "*"');
  const utf8 = imap.capabilities.has('IMAP4REV2');
  return untagged.map((parts) => parseListResponse(parts, { utf8 })).filter(Boolean);
}

// Match the root itself or a child separated by the server's hierarchy delimiter.
// A missing root may still have children; use the candidate's delimiter then.
export function selectMailboxes(entries, { roots = [], exclude = [] } = {}) {
  const included = roots.map((name) => mailboxName(name));
  const excluded = exclude.map((name) => mailboxName(name));
  const known = new Map(entries.map((entry) => [entry.name, entry]));
  const under = (entry, root) => {
    if (entry.name === root) return true;
    const delimiter = known.has(root) ? known.get(root).delimiter : entry.delimiter;
    return delimiter !== null && entry.name.startsWith(`${root}${delimiter}`);
  };
  const seen = new Set();
  return entries.filter((entry) => {
    if (seen.has(entry.name)) return false;
    if (entry.flags.some((flag) => ['\\noselect', '\\nonexistent'].includes(flag.toLowerCase()))) return false;
    if (included.length && !included.some((root) => under(entry, root))) return false;
    if (excluded.some((root) => under(entry, root))) return false;
    seen.add(entry.name);
    return true;
  });
}
