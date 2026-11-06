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
