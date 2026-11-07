// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import tls from 'node:tls';
import { mailboxName, mailboxWireName } from './mailbox.mjs';

// The IMAP4rev1 subset a read-only inbox watcher needs: LOGIN, EXAMINE (the
// mailbox opens read-only, so nothing is ever marked read), UID SEARCH, UID
// FETCH of BODY.PEEK[], and IDLE. Responses are read with their literals;
// anything unexpected fails the connection and the Connector restarts.

const COMMAND_TIMEOUT_MS = 60_000;
const FETCH_TIMEOUT_MS = 300_000;

function quote(value) {
  const text = String(value);
  if (/[\r\n\u0000]/.test(text)) throw new Error('IMAP string contains a line break');
  return `"${text.replace(/[\\"]/g, '\\$&')}"`;
}

// One response as its text pieces and literals, in order, into nested lists of
// atoms, strings and Buffers.
export function parseResponse(parts) {
  const tokens = [];
  for (const part of parts) {
    if (Buffer.isBuffer(part)) {
      tokens.push(part);
      continue;
    }
    const text = part.replace(/\{\d+\+?\}$/, '');
    let index = 0;
    while (index < text.length) {
      const character = text[index];
      if (character === ' ') { index += 1; continue; }
      if (character === '(' || character === ')') { tokens.push(character); index += 1; continue; }
      if (character === '"') {
        let value = '';
        index += 1;
        while (index < text.length && text[index] !== '"') {
          if (text[index] === '\\') index += 1;
          value += text[index] ?? '';
          index += 1;
        }
        index += 1;
        tokens.push({ string: value });
        continue;
      }
      let atom = '';
      let depth = 0;
      while (index < text.length) {
        const next = text[index];
        if (next === '[') depth += 1;
        if (next === ']') depth -= 1;
        if (depth <= 0 && (next === ' ' || next === '(' || next === ')')) break;
        atom += next;
        index += 1;
      }
      tokens.push(atom);
    }
  }
  const root = [];
  const stack = [root];
  for (const token of tokens) {
    if (token === '(') {
      const list = [];
      stack.at(-1).push(list);
      stack.push(list);
    } else if (token === ')') {
      if (stack.length > 1) stack.pop();
    } else {
      stack.at(-1).push(token);
    }
  }
  return root;
}

function text(value) {
  if (Buffer.isBuffer(value)) return value.toString('utf8');
  if (value && typeof value === 'object' && 'string' in value) return value.string;
  return typeof value === 'string' ? value : '';
}

// `* 12 FETCH (UID 34 RFC822.SIZE 5 BODY[] {5}…)` -> the attributes we read.
export function fetchAttributes(list) {
  const attributes = {};
  for (let index = 0; index + 1 < list.length; index += 2) {
    const key = text(list[index]).toUpperCase();
    const value = list[index + 1];
    if (key === 'UID') attributes.uid = Number(text(value));
    else if (key === 'RFC822.SIZE') attributes.size = Number(text(value));
    else if (key === 'INTERNALDATE') {
      const parsed = Date.parse(text(value).replace(/^(\d{1,2})-(\w{3})-(\d{4})/, '$1 $2 $3'));
      attributes.internalDate = Number.isFinite(parsed) ? parsed : null;
    } else if (key === 'X-GM-THRID') attributes.thread = text(value);
    else if (key === 'BODY[]' || key === 'BODY[HEADER]') attributes.body = Buffer.isBuffer(value) ? value : Buffer.from(text(value), 'latin1');
  }
  return attributes;
}

export class ImapConnection {
  constructor({ host, port, connect = tls.connect }) {
    Object.assign(this, { host, port, connectTls: connect });
    this.socket = null;
    this.buffer = Buffer.alloc(0);
    this.parts = [];
    this.literal = null;
    this.sequence = 0;
    this.current = null;
    this.capabilities = new Set();
    this.onUntagged = null;
    this.closed = false;
    this.closeError = null;
  }

  async connect() {
    const greeting = new Promise((resolve, reject) => {
      this.current = { tag: null, untagged: [], resolve, reject, timer: null };
    });
    this.socket = this.connectTls({ host: this.host, port: this.port, servername: this.host });
    this.socket.setKeepAlive?.(true, 60_000);
    this.socket.on('data', (chunk) => this.receive(chunk));
    this.socket.on('error', (error) => this.fail(error));
    this.socket.on('close', () => this.fail(new Error('IMAP connection closed')));
    await this.wait(greeting, COMMAND_TIMEOUT_MS);
  }

  wait(promise, timeoutMs) {
    let timer;
    return Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => {
        const error = new Error('IMAP server did not answer in time');
        reject(error);
        this.fail(error);
      }, timeoutMs);
      timer.unref?.();
    })]).finally(() => clearTimeout(timer));
  }

  receive(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    while (true) {
      if (this.literal !== null) {
        if (this.buffer.length < this.literal) return;
        this.parts.push(this.buffer.subarray(0, this.literal));
        this.buffer = this.buffer.subarray(this.literal);
        this.literal = null;
        continue;
      }
      const end = this.buffer.indexOf('\r\n');
      if (end < 0) return;
      const line = this.buffer.subarray(0, end).toString('latin1');
      this.buffer = this.buffer.subarray(end + 2);
      this.parts.push(line);
      const literal = /\{(\d+)\+?\}$/.exec(line);
      if (literal) {
        this.literal = Number(literal[1]);
        continue;
      }
      const parts = this.parts;
      this.parts = [];
      this.handle(parts);
    }
  }

  handle(parts) {
    const head = parts[0];
    const current = this.current;
    if (head.startsWith('+')) {
      current?.continued?.();
      return;
    }
    if (head.startsWith('* ')) {
      if (/^\* BYE\b/i.test(head) && current?.tag !== null && !current?.logout) {
        this.fail(new Error(`IMAP server closed the session: ${head.slice(6, 200)}`));
        return;
      }
      const capability = /\[CAPABILITY ([^\]]+)\]|^\* CAPABILITY (.+)$/i.exec(head);
      if (capability) this.capabilities = new Set((capability[1] ?? capability[2]).toUpperCase().split(/\s+/));
      if (current?.tag === null) {
        // The greeting: OK, or PREAUTH, or BYE.
        this.current = null;
        if (/^\* (OK|PREAUTH)\b/i.test(head)) current.resolve({ untagged: [] });
        else current.reject(new Error('IMAP server refused the connection'));
        return;
      }
      current?.untagged.push(parts);
      this.onUntagged?.(parts);
      return;
    }
    if (!current || !head.startsWith(`${current.tag} `)) return;
    this.current = null;
    const [, status, rest = ''] = /^\S+ (\w+)\s?(.*)$/.exec(head) ?? [];
    if (status?.toUpperCase() === 'OK') current.resolve({ untagged: current.untagged, text: rest });
    // Server text can echo a mailbox name, never the password; still bound it.
    else current.reject(new Error(`IMAP ${current.name} failed: ${status ?? ''} ${rest.slice(0, 200)}`.trim()));
  }

  fail(error) {
    if (this.closed) return;
    this.closed = true;
    this.closeError = error;
    const current = this.current;
    this.current = null;
    current?.reject(error);
    this.socket?.destroy();
  }

  // One command at a time; `continued` runs on a `+` continuation (IDLE).
  command(name, args = '', { timeoutMs = COMMAND_TIMEOUT_MS, continued = null, logout = false } = {}) {
    if (this.closed) return Promise.reject(this.closeError ?? new Error('IMAP connection is closed'));
    if (this.current) return Promise.reject(new Error('IMAP command already in progress'));
    this.sequence += 1;
    const tag = `L${this.sequence}`;
    const promise = new Promise((resolve, reject) => {
      this.current = { tag, name, untagged: [], resolve, reject, continued, logout };
    });
    this.socket.write(`${tag} ${name}${args ? ` ${args}` : ''}\r\n`);
    return this.wait(promise, timeoutMs);
  }

  async login(user, password) {
    await this.command('LOGIN', `${quote(user)} ${quote(password)}`);
    await this.command('CAPABILITY');
    // Some providers (163.com) refuse a session that never identified its client.
    if (this.capabilities.has('ID')) await this.command('ID', '("name" "vika")').catch(() => {});
  }

  async examine(mailbox = 'INBOX') {
    const { untagged } = await this.command('EXAMINE', quote(mailboxWireName(mailboxName(mailbox), this.capabilities.has('IMAP4REV2'))));
    const box = { uidValidity: null, uidNext: null, exists: 0 };
    for (const parts of untagged) {
      const head = parts[0];
      const validity = /\[UIDVALIDITY (\d+)\]/i.exec(head);
      const next = /\[UIDNEXT (\d+)\]/i.exec(head);
      const exists = /^\* (\d+) EXISTS/i.exec(head);
      if (validity) box.uidValidity = Number(validity[1]);
      if (next) box.uidNext = Number(next[1]);
      if (exists) box.exists = Number(exists[1]);
    }
    if (!box.uidValidity) throw new Error('IMAP server reported no UIDVALIDITY');
    if (!box.uidNext) {
      // No UIDNEXT: the newest message's UID, plus one.
      const last = box.exists > 0 ? await this.fetch('*', ['UID'], { byUid: false }) : [];
      box.uidNext = Math.max(0, ...last.map((item) => item.uid)) + 1;
    }
    return box;
  }

  async searchUids(fromUid) {
    const { untagged } = await this.command('UID SEARCH', `UID ${fromUid}:*`);
    const uids = [];
    for (const parts of untagged) {
      const match = /^\* SEARCH\b(.*)$/i.exec(parts[0]);
      if (match) uids.push(...match[1].trim().split(/\s+/).filter(Boolean).map(Number));
    }
    // `n:*` always matches the newest message, even when its UID is below n.
    return uids.filter((uid) => uid >= fromUid).sort((left, right) => left - right);
  }

  // The server may add FETCH responses of its own, such as flags changed from
  // another client (RFC 3501 7.4.2), for any message. Only the UIDs asked for
  // are returned, one entry each, with all their responses merged.
  async fetch(set, items, { byUid = true } = {}) {
    const name = byUid ? 'UID FETCH' : 'FETCH';
    const list = Array.isArray(set) ? set.join(',') : set;
    const wanted = Array.isArray(set) ? new Set(set) : null;
    const { untagged } = await this.command(name, `${list} (${items.join(' ')})`, { timeoutMs: FETCH_TIMEOUT_MS });
    const found = new Map();
    for (const parts of untagged) {
      if (!/^\* \d+ FETCH\b/i.test(parts[0])) continue;
      const attributes = fetchAttributes(parseResponse(parts).find(Array.isArray) ?? []);
      if (!Number.isSafeInteger(attributes.uid) || (wanted && !wanted.has(attributes.uid))) continue;
      found.set(attributes.uid, { ...found.get(attributes.uid), ...attributes });
    }
    return [...found.values()];
  }

  // Waits in IDLE until the mailbox reports a new message count, `maxMs`
  // passes, or `stopped()` turns true; then ends IDLE. Returns why it woke.
  async idle(maxMs, stopped = () => false) {
    let wake;
    const woke = new Promise((resolve) => { wake = resolve; });
    const timer = setTimeout(() => wake('timeout'), maxMs);
    const poll = setInterval(() => { if (stopped()) wake('stopped'); }, 1_000);
    timer.unref?.();
    poll.unref?.();
    this.onUntagged = (parts) => { if (/^\* \d+ EXISTS/i.test(parts[0])) wake('exists'); };
    let started;
    const ready = new Promise((resolve) => { started = resolve; });
    const done = this.command('IDLE', '', { timeoutMs: maxMs + COMMAND_TIMEOUT_MS, continued: () => started() });
    try {
      // DONE is only valid once the server has accepted IDLE.
      await Promise.race([ready, done]);
      const reason = await Promise.race([woke, done.then(() => 'ended')]);
      if (reason !== 'ended') {
        if (!this.closed) this.socket.write('DONE\r\n');
        await done;
      }
      return reason;
    } finally {
      clearTimeout(timer);
      clearInterval(poll);
      this.onUntagged = null;
    }
  }

  async logout() {
    if (this.closed) return;
    try { await this.command('LOGOUT', '', { timeoutMs: 5_000, logout: true }); } catch {}
    this.close();
  }

  // Also ends a command in flight (an IDLE, while stopping).
  close() {
    this.fail(new Error('IMAP connection closed'));
  }
}
