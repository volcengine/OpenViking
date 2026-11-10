// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { eventMessage } from '../connectors/protocol.mjs';
import { safeError } from '../logger.mjs';
import { MailArchive, parsesAttachment } from './archive.mjs';
import { ImapConnection } from './imap.mjs';
import { configuredMailboxes, mailboxDiscovery, mailboxIdentity, mailboxPollSeconds } from './mailbox.mjs';
import { listMailboxes, selectMailboxes } from './list-mailboxes.mjs';
import { buildReply, decodeCharset, isOwnMessageId, parseMail, replyMessageId } from './mime.mjs';
import { sendMail, verifySmtp } from './smtp.mjs';
import { EmailState } from './state.mjs';

// Servers may drop an IDLE after 29 minutes; renew it well before.
const IDLE_MS = 9 * 60_000;
// After a long stop only the newest mails are reported; older ones are skipped.
const MAX_CATCH_UP = 50;
const FETCH_BATCH = 10;
// A larger mail is reported from its headers alone.
const MAX_BODY_BYTES = 25 * 1024 * 1024;
const MAX_LISTED = 10;
const MAX_ATTACHMENTS = 20;
// The whole text OV keeps of a mail (each part is read no further than mime's
// part limit); a mail OV could not take is reported with its first 6000.
const STORED_TEXT_CHARS = 1_000_000;
const INLINE_TEXT_CHARS = 6_000;
// A stored mail is reported with the start of its text; the rest is a read away.
const PREVIEW_CHARS = 500;
// How long a mail's event waits for OV to parse its attachments.
const ATTACHMENT_WAIT_MS = 60_000;
// An approval card shows every reply recipient; a mail that would be answered
// to more addresses than fit there in full cannot be answered at all.
const MAX_REPLY_RECIPIENTS = 20;

// The account file: { address, password, imap: { host, port }, smtp: { host,
// port, secure? }, username?, name? }. The password is a client (app) password.
// SMTP uses implicit TLS on port 465 unless `secure` says otherwise, else STARTTLS.
export function loadAccount(file) {
  let value;
  try { value = JSON.parse(fs.readFileSync(file, 'utf8')); } catch {
    throw new Error('email config file is unreadable or not JSON');
  }
  const address = String(value?.address ?? '').trim().toLowerCase();
  if (!/^[^\s@<>]+@[^\s@<>]+$/.test(address)) throw new Error('email config needs an address');
  if (typeof value.password !== 'string' || !value.password) throw new Error('email config needs a password');
  const server = (key) => {
    const host = String(value[key]?.host ?? '').trim();
    const port = Number(value[key]?.port);
    if (!host || !Number.isInteger(port) || port < 1 || port > 65_535) throw new Error(`email config needs ${key}.host and ${key}.port`);
    return { host, port, ...(typeof value[key].secure === 'boolean' ? { secure: value[key].secure } : {}) };
  };
  return Object.freeze({
    address,
    name: typeof value.name === 'string' ? value.name.trim() : '',
    user: typeof value.username === 'string' && value.username.trim() ? value.username.trim() : address,
    password: value.password,
    imap: { ...server('imap'), mailbox: configuredMailboxes(value.imap)[0],
      mailboxes: configuredMailboxes(value.imap), discovery: mailboxDiscovery(value.imap), pollIntervalSeconds: mailboxPollSeconds(value.imap?.pollIntervalSeconds) },
    smtp: server('smtp'),
  });
}

// Ids carry which account a mail came from: UIDs and UIDVALIDITY are only
// unique within one mailbox, and an instance may switch to another account.
export function accountTag(address) {
  return createHash('sha256').update(address).digest('hex').slice(0, 8);
}

// Both credentials: an IMAP login with a read-only look at the inbox, then
// SMTP AUTH. Sends nothing. Resolves with whether new mail is pushed (IDLE).
export async function verifyAccount(account, { connectTls } = {}) {
  const imap = new ImapConnection({ ...account.imap, ...(connectTls ? { connect: connectTls } : {}) });
  let idle;
  try {
    await imap.connect();
    await imap.login(account.user, account.password);
    const names = account.imap.discovery
      ? discoveredNames(await listMailboxes(imap), account.imap.discovery)
      : account.imap.mailboxes ?? [account.imap.mailbox];
    let selected = names.length === 0 && !!account.imap.discovery;
    let selectionError;
    for (const name of names) {
      try { await imap.examine(name); selected = true; } catch (error) {
        if (imap.closed) throw error;
        selectionError = error;
      }
    }
    if (!selected) throw selectionError ?? new Error('No configured email mailbox is accessible');
    idle = imap.capabilities.has('IDLE');
    await imap.logout();
  } finally {
    imap.close();
  }
  await verifySmtp({ ...account.smtp, user: account.user, password: account.password, ...(connectTls ? { connectTls } : {}) });
  return { idle };
}

function discoveredNames(entries, discovery) {
  const names = selectMailboxes(entries, discovery).map((entry) => entry.name);
  if (names.length > 100) throw new Error('Email discovery matched more than 100 folders; narrow imap.discovery.roots or exclude');
  return names;
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
function mailText(mail) {
  const text = mail.text.length > INLINE_TEXT_CHARS ? `${mail.text.slice(0, INLINE_TEXT_CHARS)}\n[truncated]` : mail.text;
  return [...headerLines(mail), '', text].join('\n').trim();
}

function label(value) {
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
function bodyMarkdown({ mail, messageId, mailbox, imapMailbox, instance, attachments, inline }) {
  const lines = [`# ${mail.subject || '(no subject)'}`, '', ...headerLines(mail).map((line) => `- ${line}`)];
  if (mail.date) lines.push(`- Date: ${new Date(mail.date).toISOString()}`);
  lines.push(`- Mailbox: ${mailbox} (${instance})`, `- Message: ${messageId}`);
  lines.push(`- IMAP folder: ${label(imapMailbox)}`);
  if (attachments.length || inline) {
    lines.push('', '## Attachments', '', ...attachments.map((attachment) => `- ${attachmentLine(attachment)}`));
    if (inline) lines.push(`- ${inline} inline image${inline === 1 ? '' : 's'} in the body, not stored`);
  }
  lines.push('', '## Body', '', mail.text || '(no text)');
  return `${lines.join('\n')}\n`;
}

// What the model reads of a mail in OV: the senders, where the mail and its
// attachments are, and the start of the text; read_email reads the rest.
function storedText(mail, archive) {
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
function attachmentFile(attachment) {
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

function sameAddresses(approved, recipients) {
  const sorted = (list) => [...list].sort().join('\n');
  return Array.isArray(approved) && approved.length === recipients.length
    && sorted(approved) === sorted(recipients.map((recipient) => recipient.address));
}

export function replySubject(subject) {
  return /^re:/i.test(subject) ? subject : `Re: ${subject}`;
}

// A thread as a subject id: Gmail's numeric thread id, or a digest of the
// thread's first Message-ID, which the sender controls and may make any length.
function threadId(item, mail, fallback) {
  if (/^\d{1,32}$/.test(item.thread ?? '')) return `gmail:${item.thread}`;
  const root = mail.references[0] ?? mail.inReplyTo ?? mail.messageId ?? fallback;
  return `mid:${createHash('sha256').update(root).digest('hex').slice(0, 32)}`;
}

function sleep(ms, stopped) {
  return new Promise((resolve) => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (stopped() || Date.now() - started >= ms) {
        clearInterval(timer);
        resolve();
      }
    }, Math.min(ms, 100));
  });
}

// Watches configured mailboxes read-only over IMAP and reports each new mail as
// an event; replies to a reported mail over SMTP. Never marks, moves or
// deletes mail.
export class EmailClient {
  constructor(config, _registry, logger, { connectTls, archive = null, attachmentWaitMs = ATTACHMENT_WAIT_MS, pollMs, discoveryMs, reconnectMs = 1_000 } = {}) {
    this.config = config;
    this.logger = logger;
    this.connectTls = connectTls;
    this.pollMs = pollMs;
    this.discoveryMs = discoveryMs;
    this.reconnectMs = reconnectMs;
    this.state = new EmailState(config.dataDir, config.instance ?? 'email');
    this.archive = archive;
    this.attachmentWaitMs = attachmentWaitMs;
    this.account = null;
    this.tag = null;
    this.imap = null;
    this.loop = null;
    this.stopping = false;
  }

  imapConnection() {
    return new ImapConnection({ ...this.account.imap, ...(this.connectTls ? { connect: this.connectTls } : {}) });
  }

  smtpAccount() {
    return { ...this.account.smtp, user: this.account.user, password: this.account.password,
      ...(this.connectTls ? { connectTls: this.connectTls } : {}) };
  }

  async open() {
    const imap = this.imapConnection();
    this.imap = imap;
    try {
      await imap.connect();
      await imap.login(this.account.user, this.account.password);
      return imap;
    } catch (error) {
      imap.close();
      throw error;
    }
  }

  // Both credentials, before anything starts.
  async preflight() {
    this.account = loadAccount(this.config.emailConfigFile);
    this.tag = accountTag(this.account.address);
    await verifyAccount(this.account, { connectTls: this.connectTls });
    // Where reported mail is kept. The daemon checked OV before starting us.
    if (!this.archive && this.config.openVikingConfigPath) {
      this.archive = MailArchive.fromConfigFile(this.config.openVikingConfigPath, { logger: this.logger });
    }
    this.state.prune(Date.now() - this.config.retentionMs);
    // A mailbox is not an IM: notices to the owner never go here.
    return { version: 'imap-smtp-v1', owner: null };
  }

  startSubscription(onLine, onUnexpectedExit) {
    if (this.loop) throw new Error('Email watcher is already running');
    this.stopping = false;
    this.loop = this.watch(onLine).catch((error) => {
      if (!this.stopping) onUnexpectedExit({ code: null, signal: null, stderr: error.message });
    }).finally(() => { this.loop = null; });
  }

  async watch(onLine) {
    const discovery = this.account.imap.discovery;
    let names = discovery ? [] : this.account.imap.mailboxes ?? [this.account.imap.mailbox];
    let refreshedAt = null;
    const discoveryMs = this.discoveryMs ?? (discovery?.refreshIntervalSeconds ?? 300) * 1_000;
    const pollMs = this.pollMs ?? this.account.imap.pollIntervalSeconds * 1_000;
    let next = 0;
    let failures = 0;
    while (!this.stopping) {
      let imap;
      try {
        imap = await this.open();
        if (this.stopping) break;
        while (!this.stopping) {
          let idled = false;
          if (discovery && (refreshedAt === null || Date.now() - refreshedAt >= discoveryMs)) {
            try {
              const found = discoveredNames(await listMailboxes(imap), discovery);
              names = found;
              next = names.length ? next % names.length : 0;
              this.logger.info('email_mailboxes_discovered', { count: names.length });
            } catch (error) {
              if (imap.closed) throw error;
              // Preserve the last successful snapshot on a failed LIST.
              this.logger.warn('email_mailbox_discovery_failed', { error: safeError(error) });
            }
            refreshedAt = Date.now();
          }
          // Advance before opening a folder. A broken folder or connection
          // cannot indefinitely starve the folders after it.
          for (let count = 0; count < names.length && !this.stopping; count += 1) {
            const name = names[next];
            next = (next + 1) % names.length;
            try {
              const box = await imap.examine(name);
              const key = `${this.tag}:${name}`;
              let cursor = this.state.cursor(key);
              if (!cursor || cursor.uidValidity !== box.uidValidity) {
                if (cursor) this.logger.warn('email_mailbox_renumbered', { mailbox: name });
                cursor = { uidValidity: box.uidValidity, lastUid: box.uidNext - 1 };
                this.state.saveCursor(key, cursor);
              }
              await this.catchUp(imap, key, cursor, onLine, name);
            } catch (error) {
              if (this.stopping || imap.closed) throw error;
              // A tagged NO/BAD (e.g. a removed or inaccessible folder) is
              // isolated. Never SEARCH after a failed EXAMINE.
              this.logger.warn('email_mailbox_unavailable', { mailbox: name, error: safeError(error) });
              continue;
            }
            if (!discovery && names.length === 1 && imap.capabilities.has('IDLE') && !this.stopping) {
              await imap.idle(IDLE_MS, () => this.stopping);
              idled = true;
            }
          }
          failures = 0;
          if (this.stopping) break;
          // A failed EXAMINE must also wait; no hot retry loop.
          if (!idled) await sleep(pollMs, () => this.stopping);
        }
      } catch (error) {
        if (!this.stopping) {
          this.logger.warn('email_reconnecting', { error: safeError(error) });
          failures += 1;
        }
      } finally {
        const connection = imap ?? this.imap;
        this.imap = null;
        connection?.close();
      }
      if (!this.stopping) await sleep(Math.min(60_000, this.reconnectMs * 2 ** Math.min(failures - 1, 6)), () => this.stopping);
    }
  }

  async catchUp(imap, key, cursor, onLine, imapMailbox = this.account.imap.mailbox) {
    let uids = await imap.searchUids(cursor.lastUid + 1);
    if (uids.length > MAX_CATCH_UP) {
      this.logger.warn('email_catch_up_skipped', { skipped: uids.length - MAX_CATCH_UP });
      uids = uids.slice(-MAX_CATCH_UP);
    }
    const thread = imap.capabilities.has('X-GM-EXT-1') ? ['X-GM-THRID'] : [];
    for (let index = 0; index < uids.length && !this.stopping; index += FETCH_BATCH) {
      const batch = uids.slice(index, index + FETCH_BATCH);
      const sizes = await imap.fetch(batch, ['UID', 'RFC822.SIZE']);
      const small = sizes.filter((item) => !(item.size > MAX_BODY_BYTES)).map((item) => item.uid);
      const large = sizes.filter((item) => item.size > MAX_BODY_BYTES).map((item) => item.uid);
      const fetched = [
        ...(small.length ? await imap.fetch(small, ['UID', 'INTERNALDATE', ...thread, 'BODY.PEEK[]']) : []),
        ...(large.length ? (await imap.fetch(large, ['UID', 'INTERNALDATE', 'RFC822.SIZE', ...thread, 'BODY.PEEK[HEADER]']))
          .map((item) => ({ ...item, truncated: true })) : []),
      ].sort((left, right) => left.uid - right.uid);
      for (const item of fetched) {
        // One mail that cannot be read is passed over, never retried: the
        // cursor moves on so the mail after it still arrives.
        try {
          await this.report(item, cursor.uidValidity, onLine, imapMailbox);
        } catch (error) {
          this.logger.warn('email_unreadable', { uid: item.uid, error: safeError(error) });
        }
        cursor.lastUid = Math.max(cursor.lastUid, item.uid);
        this.state.saveCursor(key, cursor);
      }
      // A UID that vanished between SEARCH and FETCH (deleted) is passed over.
      cursor.lastUid = Math.max(cursor.lastUid, ...batch);
      this.state.saveCursor(key, cursor);
    }
  }

  async report(item, uidValidity, onLine, imapMailbox = this.account.imap.mailbox) {
    if (!item.body) return;
    const mail = parseMail(item.body, { maxTextChars: STORED_TEXT_CHARS });
    if (mail.autoReplied || isOwnMessageId(mail.messageId)) {
      this.logger.info('email_skipped', { uid: item.uid, reason: mail.autoReplied ? 'auto_replied' : 'own_reply' });
      return;
    }
    if (item.truncated) mail.text = `[mail body not fetched: ${Math.round(item.size / 1_048_576)} MiB]`;
    const identity = mailboxIdentity(this.tag, imapMailbox, uidValidity, item.uid);
    const messageId = `mail:${identity}`;
    const recipients = replyRecipients(mail, this.account.address);
    const files = mail.attachments.filter((attachment) => !attachment.inline).slice(0, MAX_ATTACHMENTS);
    // undefined: no OV to store in; null: OV could not take this mail.
    const archive = this.archive ? await this.store(mail, messageId, files, imapMailbox) : undefined;
    const line = JSON.stringify(eventMessage('email', {
      instance: this.config.instance ?? 'email',
      id: `imap:${identity}`,
      kind: mail.bulk ? 'email.list' : 'email.received',
      occurred_at: item.internalDate ?? mail.date ?? Date.now(),
      subject: { type: 'thread', id: threadId(item, mail, messageId) },
      actor: mail.from ? { type: 'email', id: mail.from.address } : null,
      content: archive ? { title: mail.subject, text: storedText(mail, archive) } : {
        title: mail.subject,
        text: archive === null ? `${mailText(mail)}\n[mail not stored in OpenViking]` : mailText(mail),
        attachments: files.map((attachment) => ({ type: 'file', id: label(attachment.name) || 'attachment' })),
      },
      message: { id: messageId, type: 'email' },
      // Who a reply goes to, in full, for the owner to check on the approval
      // card; past the limit only how many. `archive` is where read_email finds it.
      data: { mailbox: imapMailbox, reply: { from: this.account.address, subject: replySubject(mail.subject),
        ...(recipients.length <= MAX_REPLY_RECIPIENTS ? { to: recipients } : { recipients: recipients.length }) },
      ...(archive ? { archive } : {}) },
    }));
    // The event is built and checked before the reply headers are kept, so a
    // mail that fails leaves nothing behind.
    this.state.remember(messageId, {
      messageId: mail.messageId, subject: mail.subject, references: mail.references,
      from: mail.from, replyTo: mail.replyTo, to: mail.to,
    });
    onLine(line);
    this.logger.info('email_reported', { uid: item.uid, bulk: mail.bulk, stored: Boolean(archive) });
  }

  // The mail and its attachments in OV, before the event goes out: { uri,
  // body, chars, attachments }. Null when OV could not take it; the event then
  // carries the text itself. A mail seen again (after a restart) is stored
  // again over the same paths.
  async store(mail, messageId, files, imapMailbox = this.account.imap.mailbox) {
    try {
      const folder = this.archive.folder(messageId);
      const attachments = await this.archive.storeAttachments(folder, files.map(attachmentFile),
        { deadline: Date.now() + this.attachmentWaitMs });
      const markdown = bodyMarkdown({
        mail, messageId, mailbox: this.account.address, imapMailbox, instance: this.config.instance ?? 'email', attachments,
        inline: mail.attachments.filter((attachment) => attachment.inline).length,
      });
      const body = await this.archive.write(`${folder}/body.md`, markdown);
      await this.archive.chunk(`${folder}/chunks`, markdown, 'body.md');
      return { uri: folder, body, chars: mail.text.length, attachments };
    } catch (error) {
      this.logger.warn('email_store_failed', { error: safeError(error) });
      return null;
    }
  }

  // A plain-text reply to a mail this Connector reported, threaded under it.
  // `to` is the recipient list the owner approved on the card; it goes out only
  // if it is still exactly this mail's reply recipients.
  async replyMessage({ message_id: messageId, content, to: approved, idempotency_key: idempotencyKey }) {
    if (content?.type !== 'markdown') throw new Error('email replies are plain text only');
    // A mail id names its account: a mail of another mailbox is never answered from this one.
    if (!String(messageId).startsWith(`mail:${this.tag}:`)) throw new Error('That mail arrived in another mailbox');
    const original = this.state.mail(messageId);
    if (!original) throw new Error('That mail is no longer available to reply to');
    const own = this.account.address;
    const to = replyRecipients(original, own);
    if (to.length === 0) throw new Error('That mail has no address to reply to');
    if (!sameAddresses(approved, to)) throw new Error('The reply recipients do not match the ones the owner approved');
    const raw = buildReply({
      from: { name: this.account.name, address: own }, to, original, text: content.text,
      messageId: replyMessageId(idempotencyKey, own),
    });
    await sendMail(this.smtpAccount(), { from: own, recipients: to.map((recipient) => recipient.address), raw });
    this.logger.info('email_replied', { recipients: to.length });
    return { sent: true };
  }

  async stopSubscription() {
    this.stopping = true;
    this.imap?.close();
    await this.loop;
  }

  async stop() {
    await this.stopSubscription();
    this.state.close();
  }
}
