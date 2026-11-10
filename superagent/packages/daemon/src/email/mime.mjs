// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';

// Just enough RFC 5322 / MIME to read a mail into text and write a plain-text
// reply. The raw message is handled as a latin1 string, one char per byte, so
// structure is parsed without decoding and each part is decoded from its own
// bytes with its own charset.

// Everything below is sender-controlled, so every piece is bounded before it
// is decoded or reported, and every pass over it is linear: one crafted mail
// must not stall the mailbox.
const MAX_TEXT_CHARS = 6_000;
// The readable text keeps 6000 characters; a part is read no further than this.
const MAX_PART_BYTES = 256 * 1024;
const MAX_HEADER_CHARS = 64 * 1024;
const MAX_SUBJECT_CHARS = 300;
const MAX_NAME_CHARS = 100;
// RFC 5321: a path holds at most 256 octets, so an address at most 254.
const MAX_ADDRESS_CHARS = 254;

function bytes(binary) {
  return Buffer.from(binary, 'latin1');
}

export function decodeCharset(buffer, charset = 'utf-8') {
  const label = String(charset || 'utf-8').trim().toLowerCase().replace(/^"|"$/g, '');
  for (const candidate of [label, 'utf-8']) {
    try { return new TextDecoder(candidate).decode(buffer); } catch { /* unknown label */ }
  }
  return buffer.toString('utf8');
}

function decodeQuotedPrintable(binary, { header = false } = {}) {
  const text = header ? binary.replace(/_/g, ' ') : binary.replace(/=\r?\n/g, '');
  return text.replace(/=([0-9A-Fa-f]{2})/g, (_, hex) => String.fromCharCode(Number.parseInt(hex, 16)));
}

function decodeTransfer(binary, encoding) {
  const kind = String(encoding || '').trim().toLowerCase();
  if (kind === 'base64') return bytes(Buffer.from(binary.replace(/[^A-Za-z0-9+/=]/g, ''), 'base64').toString('latin1'));
  if (kind === 'quoted-printable') return bytes(decodeQuotedPrintable(binary));
  return bytes(binary);
}

// RFC 2047 encoded words. Adjacent words in one charset are joined as bytes
// first: a multibyte character may be split across two of them.
export function decodeWords(value) {
  const text = String(value ?? '');
  const word = /=\?([^?\s]+)\?([BbQq])\?([^?\s]*)\?=/g;
  const out = [];
  let last = 0;
  let pending = null;
  const flush = () => {
    if (pending) out.push(decodeCharset(Buffer.concat(pending.parts), pending.charset));
    pending = null;
  };
  for (let match = word.exec(text); match; match = word.exec(text)) {
    const between = text.slice(last, match.index);
    if (!(pending && /^\s*$/.test(between))) {
      flush();
      out.push(between);
    }
    const charset = match[1].split('*')[0];
    const raw = match[2].toUpperCase() === 'B'
      ? Buffer.from(match[3], 'base64')
      : bytes(decodeQuotedPrintable(match[3], { header: true }));
    if (pending && pending.charset.toLowerCase() === charset.toLowerCase()) pending.parts.push(raw);
    else {
      flush();
      pending = { charset, parts: [raw] };
    }
    last = word.lastIndex;
  }
  flush();
  out.push(text.slice(last));
  return out.join('');
}

// Header block -> lowercased name -> values in order. Raw 8-bit header bytes
// are read as UTF-8, which is what modern clients send.
function parseHeaders(binary) {
  const headers = new Map();
  const unfolded = binary.replace(/\r?\n[ \t]+/g, ' ');
  for (const line of unfolded.split(/\r?\n/)) {
    const colon = line.indexOf(':');
    if (colon <= 0) continue;
    const name = line.slice(0, colon).trim().toLowerCase();
    const value = bytes(line.slice(colon + 1, colon + 1 + MAX_HEADER_CHARS)).toString('utf8').trim();
    if (!headers.has(name)) headers.set(name, []);
    headers.get(name).push(value);
  }
  return headers;
}

function splitHeaderBody(binary) {
  const match = /\r?\n\r?\n/.exec(binary);
  if (!match) return { head: binary, body: '' };
  return { head: binary.slice(0, match.index), body: binary.slice(match.index + match[0].length) };
}

// `type/subtype; a=b; c*=utf-8''%E4%B8%AD; d*0=x; d*1=y` -> { value, params }.
export function parseParams(value) {
  const parts = [];
  let current = '';
  let quoted = false;
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (character === '"' && value[index - 1] !== '\\') quoted = !quoted;
    if (character === ';' && !quoted) {
      parts.push(current);
      current = '';
    } else {
      current += character;
    }
  }
  parts.push(current);
  const [head, ...rest] = parts;
  const pieces = new Map();
  for (const part of rest) {
    const equals = part.indexOf('=');
    if (equals < 0) continue;
    const key = part.slice(0, equals).trim().toLowerCase();
    let raw = part.slice(equals + 1).trim();
    if (raw.startsWith('"') && raw.endsWith('"')) raw = raw.slice(1, -1).replace(/\\(.)/g, '$1');
    const match = /^([^*]+)(?:\*(\d+))?(\*)?$/.exec(key);
    if (!match) continue;
    const [, name, order = '0', extended] = match;
    if (!pieces.has(name)) pieces.set(name, []);
    pieces.get(name).push({ order: Number(order), raw, extended: Boolean(extended) });
  }
  const params = {};
  for (const [name, list] of pieces) {
    list.sort((left, right) => left.order - right.order);
    if (!list.some((piece) => piece.extended)) {
      params[name] = decodeWords(list.map((piece) => piece.raw).join(''));
      continue;
    }
    // RFC 2231: charset'language'percent-encoded, on the first piece only.
    let charset = 'utf-8';
    const chunks = list.map((piece, index) => {
      let raw = piece.raw;
      if (index === 0 && piece.extended) {
        const quote = /^([^']*)'[^']*'(.*)$/.exec(raw);
        if (quote) {
          charset = quote[1] || charset;
          raw = quote[2];
        }
      }
      return piece.extended
        ? Buffer.from(raw.replace(/%([0-9A-Fa-f]{2})/g, (_, hex) => String.fromCharCode(Number.parseInt(hex, 16))), 'latin1')
        : Buffer.from(raw, 'utf8');
    });
    params[name] = decodeCharset(Buffer.concat(chunks), charset);
  }
  return { value: head.trim().toLowerCase(), params };
}

function splitAddressList(value) {
  const items = [];
  let current = '';
  let quoted = false;
  let angle = 0;
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (character === '"' && value[index - 1] !== '\\') quoted = !quoted;
    else if (!quoted && character === '<') angle += 1;
    else if (!quoted && character === '>') angle = Math.max(0, angle - 1);
    if (character === ',' && !quoted && angle === 0) {
      items.push(current);
      current = '';
    } else {
      current += character;
    }
  }
  items.push(current);
  return items.map((item) => item.trim()).filter(Boolean);
}

// `"Name" <a@b>, c@d` -> [{ name, address }]; group syntax keeps its members.
export function parseAddresses(value) {
  const result = [];
  for (const item of splitAddressList(String(value ?? '').replace(/^[^:<>"]*:(.*);\s*$/, '$1'))) {
    const angle = /<([^<>]*)>\s*$/.exec(item);
    const address = (angle ? angle[1] : item).trim().replace(/^mailto:/i, '');
    if (address.length > MAX_ADDRESS_CHARS || !/^[^\s@<>]+@[^\s@<>]+$/.test(address)) continue;
    const rawName = angle ? item.slice(0, angle.index).trim() : '';
    const name = decodeWords(rawName.replace(/^"(.*)"$/, '$1').replace(/\\(.)/g, '$1')).trim().slice(0, MAX_NAME_CHARS);
    result.push({ name, address: address.toLowerCase() });
  }
  return result;
}

// `<a@b>` -> `a@b`; a list of them (References) -> in order.
// One that could not fit a header line (RFC 5322: 998 octets) is no id.
export function messageIds(value) {
  return [...String(value ?? '').matchAll(/<([^<>\s]+)>/g)].map((match) => match[1]).filter((id) => id.length <= 998);
}

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };

// Drops script, style and head blocks in one forward pass: an unclosed block
// ends the text instead of being searched for again from every opening tag.
function withoutBlocks(html) {
  const lower = html.toLowerCase();
  const opening = /<(script|style|head)\b/g;
  let out = '';
  let from = 0;
  for (let match = opening.exec(lower); match; match = opening.exec(lower)) {
    out += html.slice(from, match.index);
    const close = lower.indexOf(`</${match[1]}`, opening.lastIndex);
    if (close < 0) return out;
    const end = lower.indexOf('>', close);
    if (end < 0) return out;
    from = end + 1;
    opening.lastIndex = from;
  }
  return out + html.slice(from);
}

// A tag never spans another `<`, which keeps each pattern linear.
export function htmlToText(html) {
  return withoutBlocks(String(html))
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|tr|li|h[1-6]|blockquote|table)\s*>/gi, '\n')
    .replace(/<li\b[^<>]*>/gi, '- ')
    .replace(/<[^<>]*>/g, '')
    .replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (entity, name) => {
      if (name[0] === '#') {
        const code = name[1].toLowerCase() === 'x' ? Number.parseInt(name.slice(2), 16) : Number(name.slice(1));
        return Number.isFinite(code) && code > 0 && code < 0x110000 ? String.fromCodePoint(code) : '';
      }
      return ENTITIES[name.toLowerCase()] ?? entity;
    })
    .replace(/[ \t\u00a0]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

// Leaf parts in document order, each with its decoded headers.
function walk(binary, parts = [], depth = 0) {
  const { head, body } = splitHeaderBody(binary);
  const headers = parseHeaders(head);
  const type = parseParams(headers.get('content-type')?.[0] ?? 'text/plain');
  const disposition = parseParams(headers.get('content-disposition')?.[0] ?? '');
  if (type.value.startsWith('multipart/') && type.params.boundary && depth < 20) {
    const delimiter = `--${type.params.boundary}`.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    // Everything after the closing delimiter is the epilogue; before the first, the preamble.
    const closing = new RegExp(`(?:^|\\r?\\n)${delimiter}--`).exec(body);
    const inner = closing ? body.slice(0, closing.index) : body;
    const sections = inner.split(new RegExp(`(?:^|\\r?\\n)${delimiter}[ \\t]*(?=\\r?\\n|$)`));
    for (const section of sections.slice(1)) {
      const content = section.replace(/^\r?\n/, '');
      if (content.trim()) walk(content, parts, depth + 1);
    }
    return parts;
  }
  parts.push({
    type: type.value || 'text/plain',
    charset: type.params.charset,
    disposition: disposition.value,
    filename: disposition.params.filename || type.params.name || '',
    data: decodeTransfer(body, headers.get('content-transfer-encoding')?.[0]),
  });
  return parts;
}

function header(headers, name) {
  return headers.get(name)?.[0] ?? '';
}

// One mail as the Connector reports it: who, what, the readable text, and the
// ids a reply threads under.
export function parseMail(raw, { maxTextChars = MAX_TEXT_CHARS } = {}) {
  const binary = Buffer.isBuffer(raw) ? raw.toString('latin1') : String(raw);
  const { head } = splitHeaderBody(binary);
  const headers = parseHeaders(head);
  const parts = walk(binary);
  const isAttachment = (part) => part.disposition === 'attachment'
    || (part.filename && !part.type.startsWith('text/'))
    || !(part.type.startsWith('text/') || part.type === 'message/delivery-status');
  const plain = parts.find((part) => part.type === 'text/plain' && !isAttachment(part));
  const html = parts.find((part) => part.type === 'text/html' && !isAttachment(part));
  const read = (part) => decodeCharset(part.data.subarray(0, MAX_PART_BYTES), part.charset);
  let text = plain ? read(plain) : html ? htmlToText(read(html)) : '';
  text = text.replace(/\r\n/g, '\n').trim();
  const partTooLong = (plain ?? html)?.data.length > MAX_PART_BYTES;
  if (text.length > maxTextChars || partTooLong) text = `${text.slice(0, maxTextChars)}\n[truncated]`;
  // `inline` marks an image the body shows (a logo, a signature) rather than a
  // file someone attached.
  const attachments = parts.filter(isAttachment).map((part) => ({
    name: part.filename || part.type, filename: part.filename, type: part.type, charset: part.charset,
    size: part.data.length, data: part.data,
    inline: part.type.startsWith('image/') && (part.disposition === 'inline' || !part.filename),
  }));
  const date = Date.parse(header(headers, 'date'));
  const precedence = header(headers, 'precedence').toLowerCase();
  const autoSubmitted = header(headers, 'auto-submitted').split(';')[0].trim().toLowerCase();
  return {
    messageId: messageIds(header(headers, 'message-id'))[0] ?? null,
    inReplyTo: messageIds(header(headers, 'in-reply-to'))[0] ?? null,
    references: messageIds(headers.get('references')?.join(' ') ?? ''),
    subject: decodeWords(header(headers, 'subject')).trim().slice(0, MAX_SUBJECT_CHARS),
    from: parseAddresses(header(headers, 'from'))[0] ?? null,
    replyTo: parseAddresses(header(headers, 'reply-to')),
    to: parseAddresses(headers.get('to')?.join(', ') ?? ''),
    cc: parseAddresses(headers.get('cc')?.join(', ') ?? ''),
    date: Number.isFinite(date) ? date : null,
    text,
    attachments,
    // An automatic answer to a mail (out of office, most bounces; RFC 3834). A
    // notice a program sends on its own (`auto-generated`, a security alert)
    // is still mail the owner may need to hear about.
    autoReplied: autoSubmitted === 'auto-replied',
    // Mailing lists, newsletters and bulk senders.
    bulk: headers.has('list-id') || headers.has('list-unsubscribe') || ['bulk', 'list', 'junk'].includes(precedence),
  };
}

function encodeWord(value) {
  return /^[\x20-\x7e]*$/.test(value) ? value : `=?UTF-8?B?${Buffer.from(value, 'utf8').toString('base64')}?=`;
}

function wrapBase64(buffer) {
  return buffer.toString('base64').replace(/.{1,76}/g, '$&\r\n');
}

function formatAddress({ name, address }) {
  if (!name) return address;
  const display = /^[\x20-\x7e]*$/.test(name) ? `"${name.replace(/["\\]/g, '\\$&')}"` : encodeWord(name);
  return `${display} <${address}>`;
}

// RFC 5322 date with a numeric zone.
function mailDate(ms) {
  const date = new Date(ms);
  const day = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][date.getUTCDay()];
  const month = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][date.getUTCMonth()];
  const pad = (value) => String(value).padStart(2, '0');
  return `${day}, ${pad(date.getUTCDate())} ${month} ${date.getUTCFullYear()} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())} +0000`;
}

// The Message-ID of a reply this Connector sends, derived from its idempotency
// key: a retry carries the same id, and the Connector can tell its own mail
// when it comes back into the inbox.
export function replyMessageId(idempotencyKey, fromAddress) {
  const domain = fromAddress.split('@')[1] || 'localhost';
  return `vika.${createHash('sha256').update(String(idempotencyKey)).digest('hex').slice(0, 32)}@${domain}`;
}

export function isOwnMessageId(messageId) {
  return /^vika\.[0-9a-f]{32}@/.test(messageId ?? '');
}

// A plain-text reply that threads under the original: Re: subject,
// In-Reply-To and References.
export function buildReply({ from, to, original, text, messageId, now = Date.now() }) {
  const subject = /^re:/i.test(original.subject) ? original.subject : `Re: ${original.subject}`;
  const references = [...original.references, ...(original.messageId ? [original.messageId] : [])].slice(-20);
  const headers = [
    `From: ${formatAddress(from)}`,
    `To: ${to.map(formatAddress).join(', ')}`,
    `Subject: ${encodeWord(subject.trim() || 'Re:')}`,
    `Date: ${mailDate(now)}`,
    `Message-ID: <${messageId}>`,
    ...(original.messageId ? [`In-Reply-To: <${original.messageId}>`] : []),
    ...(references.length ? [`References: ${references.map((id) => `<${id}>`).join(' ')}`] : []),
    'MIME-Version: 1.0',
    'Content-Type: text/plain; charset=UTF-8',
    'Content-Transfer-Encoding: base64',
  ];
  return `${headers.join('\r\n')}\r\n\r\n${wrapBase64(Buffer.from(String(text).replace(/\r?\n/g, '\r\n'), 'utf8'))}`;
}
