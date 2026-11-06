// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';
import { parsesAttachment } from './archive.mjs';
import { decodeCharset, parseMail } from './mime.mjs';

// How one mail is written for the model, for OpenViking and for the approval
// card. Reading and watching decide *what* is reported; this module decides how
// it reads, so the wording can change without touching the protocol paths.

const MAX_LISTED = 10;
// The whole text OV keeps of a mail (each part is read no further than mime's
// part limit); a mail OV could not take is reported with its first 6000.
const STORED_TEXT_CHARS = 1_000_000;
const INLINE_TEXT_CHARS = 6_000;
// A stored mail is reported with the start of its text; the rest is a read away.
const PREVIEW_CHARS = 500;
// An approval card shows every reply recipient; a mail that would be answered
// to more addresses than fit there in full cannot be answered at all.
export const MAX_REPLY_RECIPIENTS = 20;
// More attachments than this are listed but not stored.
export const MAX_ATTACHMENTS = 20;

// Parses a mail as the archive and the report both read it: the same limit for
// every consumer, so a mail is never reported with text OV did not keep.
export function parseStoredMail(body) {
  return parseMail(body, { maxTextChars: STORED_TEXT_CHARS });
}

function people(list) {
  const shown = list.slice(0, MAX_LISTED).map(({ name, address }) => (name ? `${name} <${address}>` : address));
  if (list.length > MAX_LISTED) shown.push(`+${list.length - MAX_LISTED} more`);
  return shown.join(', ');
}

// Who wrote to whom. Display names are untrusted labels, like the rest of the mail.
function headerLines(mail) {
  return [
    ...(mail.from ? [`From: ${people([mail.from])}`] : []),
    ...(mail.to.length ? [`To: ${people(mail.to)}`] : []),
    ...(mail.cc.length ? [`Cc: ${people(mail.cc)}`] : []),
  ];
}

// What the model reads of a mail that is not in OV: the senders, then the body.
export function mailText(mail) {
  const text = mail.text.length > INLINE_TEXT_CHARS ? `${mail.text.slice(0, INLINE_TEXT_CHARS)}\n[truncated]` : mail.text;
  return [...headerLines(mail), '', text].join('\n').trim();
}

export function label(value) {
  return String(value).replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 200);
}

function formatSize(bytes) {
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

// One attachment as body.md and the event both list it.
function attachmentLine({ id, name, type, size, uri, status, reason }) {
  const head = `${id} ${name} (${type}, ${formatSize(size)})`;
  if (status === 'stored') return `${head}: ${uri}`;
  if (status === 'processing') return `${head}: ${uri} (OpenViking is still reading it)`;
  return `${head}: not stored, ${reason}`;
}

// The mail as OV keeps it.
export function bodyMarkdown({ mail, messageId, mailbox, instance, attachments, inline }) {
  const lines = [`# ${mail.subject || '(no subject)'}`, '', ...headerLines(mail).map((line) => `- ${line}`)];
  if (mail.date) lines.push(`- Date: ${new Date(mail.date).toISOString()}`);
  lines.push(`- Mailbox: ${mailbox} (${instance})`, `- Message: ${messageId}`);
  if (attachments.length || inline) {
    lines.push('', '## Attachments', '', ...attachments.map((attachment) => `- ${attachmentLine(attachment)}`));
    if (inline) lines.push(`- ${inline} inline image${inline === 1 ? '' : 's'} in the body, not stored`);
  }
  lines.push('', '## Body', '', mail.text || '(no text)');
  return `${lines.join('\n')}\n`;
}

// What the model reads of a mail in OV: the senders, where the mail and its
// attachments are, and the start of the text; read_email reads the rest.
export function storedText(mail, archive) {
  const { text } = mail;
  const extent = !text ? 'it has no text'
    : text.length > PREVIEW_CHARS ? `the first ${PREVIEW_CHARS} of its ${text.length} characters are below`
      : 'all of its text is below';
  return [
    ...headerLines(mail),
    `[mail stored: ${archive.body}; ${extent}]`,
    ...archive.attachments.map((attachment) => `[attachment ${attachmentLine(attachment)}]`),
    ...(text ? ['', text.length > PREVIEW_CHARS ? `${text.slice(0, PREVIEW_CHARS)}…` : text] : []),
  ].join('\n');
}

// A forwarded mail (message/rfc822) as Markdown.
function forwardedMarkdown(data) {
  const inner = parseMail(data, { maxTextChars: STORED_TEXT_CHARS });
  return [`# ${inner.subject || '(no subject)'}`, '', ...headerLines(inner).map((line) => `- ${line}`),
    ...(inner.date ? [`- Date: ${new Date(inner.date).toISOString()}`] : []), '', inner.text || '(no text)'].join('\n');
}

// What the archive gets of one attachment: text it writes as is, or bytes for
// OV to parse. An attachment that cannot be read gets neither and is skipped.
export function attachmentFile(attachment) {
  const file = { name: label(attachment.name), filename: attachment.filename, type: attachment.type, size: attachment.size };
  try {
    if (attachment.type === 'message/rfc822') return { ...file, text: forwardedMarkdown(attachment.data) };
    if (attachment.type.startsWith('text/') && !parsesAttachment(attachment)) {
      return { ...file, text: decodeCharset(attachment.data, attachment.charset).slice(0, STORED_TEXT_CHARS) };
    }
    return { ...file, data: attachment.data };
  } catch {
    return file;
  }
}

// Who a reply goes to: Reply-To, else From. A mail the owner sent goes to its
// other recipients instead. The Core shows the same list on the approval card.
export function replyRecipients(mail, own) {
  let to = mail.replyTo.length ? mail.replyTo : mail.from ? [mail.from] : [];
  const others = mail.to.filter((recipient) => recipient.address !== own);
  if (to.every((recipient) => recipient.address === own) && others.length > 0) to = others;
  return to;
}

export function sameAddresses(approved, recipients) {
  const sorted = (list) => [...list].sort().join('\n');
  return Array.isArray(approved) && approved.length === recipients.length
    && sorted(approved) === sorted(recipients.map((recipient) => recipient.address));
}

export function replySubject(subject) {
  return /^re:/i.test(subject) ? subject : `Re: ${subject}`;
}

// A thread as a subject id: Gmail's numeric thread id, or a digest of the
// thread's first Message-ID, which the sender controls and may make any length.
export function threadId(item, mail, fallback) {
  if (/^\d{1,32}$/.test(item.thread ?? '')) return `gmail:${item.thread}`;
  const root = mail.references[0] ?? mail.inReplyTo ?? mail.messageId ?? fallback;
  return `mid:${createHash('sha256').update(root).digest('hex').slice(0, 32)}`;
}
