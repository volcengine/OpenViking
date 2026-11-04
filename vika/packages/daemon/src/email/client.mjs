// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { eventMessage } from '../connectors/protocol.mjs';
import { safeError } from '../logger.mjs';
import { MAILBOX, accountTag, credentialSource, loadAccount, verifyAccount } from './account.mjs';
import { MailArchive } from './archive.mjs';
import { ImapConnection } from './imap.mjs';
import { buildReply, isOwnMessageId, replyMessageId } from './mime.mjs';
import {
  MAX_ATTACHMENTS, MAX_REPLY_RECIPIENTS, attachmentFile, bodyMarkdown, label, mailText, parseStoredMail,
  replyRecipients, replySubject, sameAddresses, storedText, threadId,
} from './present.mjs';
import { SmtpAuthError, sendMail } from './smtp.mjs';
import { EmailState } from './state.mjs';

// Servers may drop an IDLE after 29 minutes; renew it well before.
const IDLE_MS = 9 * 60_000;
const POLL_MS = 60_000;
// After a long stop only the newest mails are reported; older ones are skipped.
const MAX_CATCH_UP = 50;
const FETCH_BATCH = 10;
// A larger mail is reported from its headers alone.
const MAX_BODY_BYTES = 25 * 1024 * 1024;
// How long a mail's event waits for OV to parse its attachments.
const ATTACHMENT_WAIT_MS = 60_000;

function sleep(ms, stopped) {
  return new Promise((resolve) => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (stopped() || Date.now() - started >= ms) {
        clearInterval(timer);
        resolve();
      }
    }, 1_000);
  });
}

// Watches one mailbox's INBOX read-only over IMAP and reports each new mail as
// an event; replies to a reported mail over SMTP. Never marks, moves or
// deletes mail.
//
// This class owns the run: the reading loop, its cursor and the events it
// emits. What a mail *reads like* is present.mjs; how the account signs in is
// account.mjs.
export class EmailClient {
  constructor(config, _registry, logger, { connectTls, archive = null, attachmentWaitMs = ATTACHMENT_WAIT_MS,
    fetch: fetchImpl = globalThis.fetch, now = Date.now } = {}) {
    this.config = config;
    this.logger = logger;
    this.connectTls = connectTls;
    this.fetch = fetchImpl;
    this.now = now;
    this.state = new EmailState(config.dataDir, config.instance ?? 'email');
    this.archive = archive;
    this.attachmentWaitMs = attachmentWaitMs;
    this.account = null;
    this.credential = null;
    this.tag = null;
    this.imap = null;
    this.loop = null;
    this.stopping = false;
  }

  imapConnection() {
    return new ImapConnection({ ...this.account.imap, ...(this.connectTls ? { connect: this.connectTls } : {}) });
  }

  async smtpAccount(options = {}) {
    return { ...this.account.smtp, user: this.account.user, ...await this.credential(options),
      ...(this.connectTls ? { connectTls: this.connectTls } : {}) };
  }

  // A new IMAP connection, with the same one-retry rule the SMTP path uses: a
  // rejected token means a fresh one is worth trying, since the server that
  // refused it is better evidence than our own expiry clock. A mailbox that
  // reconnects after a long run therefore never reuses a token that has aged
  // out, and never needs a restart to get a working one.
  async open() {
    let auth = await this.credential();
    for (let attempt = 0; ; attempt += 1) {
      const imap = this.imapConnection();
      try {
        await imap.connect();
        if (auth.accessToken) await imap.authenticate(this.account.user, auth.accessToken);
        else await imap.login(this.account.user, auth.password);
        return imap;
      } catch (error) {
        imap.close();
        const rejected = auth.accessToken && /AUTHENTICATE failed|Authentication failed/i.test(error.message);
        if (!rejected || attempt > 0) throw error;
        this.logger.warn('email_imap_reauth', { error: safeError(error) });
        auth = await this.credential({ stale: true });
      }
    }
  }

  // Both credentials, before anything starts.
  async preflight() {
    this.account = loadAccount(this.config.emailConfigFile);
    this.tag = accountTag(this.account.address);
    this.credential = credentialSource(this.account, { fetch: this.fetch, now: this.now, logger: this.logger });
    await verifyAccount(this.account, { connectTls: this.connectTls, credential: await this.credential() });
    // Where reported mail is kept. The daemon checked OV before starting us.
    if (!this.archive && this.config.openVikingConfigPath) {
      this.archive = MailArchive.fromConfigFile(this.config.openVikingConfigPath, { logger: this.logger });
    }
    this.state.prune(Date.now() - this.config.retentionMs);
    // A mailbox is not an IM: notices to the owner never go here.
    return { version: 'imap-smtp-v2', owner: null };
  }

  startSubscription(onLine, onUnexpectedExit) {
    if (this.loop) throw new Error('Email watcher is already running');
    this.stopping = false;
    this.loop = this.watch(onLine).catch((error) => {
      if (!this.stopping) onUnexpectedExit({ code: null, signal: null, stderr: error.message });
    }).finally(() => { this.loop = null; });
  }

  async watch(onLine) {
    const imap = await this.open();
    this.imap = imap;
    try {
      const box = await imap.examine(MAILBOX);
      const key = `${this.tag}:${MAILBOX}`;
      let cursor = this.state.cursor(key);
      if (!cursor || cursor.uidValidity !== box.uidValidity) {
        // First start, or the server renumbered the mailbox: begin with what
        // arrives next instead of reporting the whole inbox.
        if (cursor) this.logger.warn('email_mailbox_renumbered');
        cursor = { uidValidity: box.uidValidity, lastUid: box.uidNext - 1 };
        this.state.saveCursor(key, cursor);
      }
      while (!this.stopping) {
        await this.catchUp(imap, key, cursor, onLine);
        if (this.stopping) break;
        if (imap.capabilities.has('IDLE')) {
          await imap.idle(IDLE_MS, () => this.stopping);
        } else {
          await sleep(POLL_MS, () => this.stopping);
          if (!this.stopping) await imap.command('NOOP');
        }
      }
    } finally {
      this.imap = null;
      await imap.logout();
    }
  }

  async catchUp(imap, key, cursor, onLine) {
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
          await this.report(item, cursor.uidValidity, onLine);
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

  async report(item, uidValidity, onLine) {
    if (!item.body) return;
    const mail = parseStoredMail(item.body);
    if (mail.autoReplied || isOwnMessageId(mail.messageId)) {
      this.logger.info('email_skipped', { uid: item.uid, reason: mail.autoReplied ? 'auto_replied' : 'own_reply' });
      return;
    }
    if (item.truncated) mail.text = `[mail body not fetched: ${Math.round(item.size / 1_048_576)} MiB]`;
    const messageId = `mail:${this.tag}:${uidValidity}:${item.uid}`;
    const recipients = replyRecipients(mail, this.account.address);
    const files = mail.attachments.filter((attachment) => !attachment.inline).slice(0, MAX_ATTACHMENTS);
    // undefined: no OV to store in; null: OV could not take this mail.
    const archive = this.archive ? await this.store(mail, messageId, files) : undefined;
    const line = JSON.stringify(eventMessage('email', {
      instance: this.config.instance ?? 'email',
      id: `imap:${this.tag}:${uidValidity}:${item.uid}`,
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
      data: { reply: { from: this.account.address, subject: replySubject(mail.subject),
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
  async store(mail, messageId, files) {
    try {
      const folder = this.archive.folder(messageId);
      const attachments = await this.archive.storeAttachments(folder, files.map(attachmentFile),
        { deadline: Date.now() + this.attachmentWaitMs });
      const markdown = bodyMarkdown({
        mail, messageId, mailbox: this.account.address, instance: this.config.instance ?? 'email', attachments,
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
    await this.deliver({ from: own, recipients: to.map((recipient) => recipient.address), raw });
    this.logger.info('email_replied', { recipients: to.length });
    return { sent: true };
  }

  // Sends one message, retrying only what is safe to retry.
  //
  // A sign-in that failed never got as far as telling the server about this
  // message, so a new access token and one more attempt are safe — that is the
  // case a token revoked early produces. A failure after the body was written
  // is not safe to retry: the mail may already be queued, and a second attempt
  // would send it twice.
  async deliver(send) {
    try {
      return await sendMail(await this.smtpAccount(), send);
    } catch (error) {
      if (!(error instanceof SmtpAuthError) || !this.account.oauth2) throw error;
      this.logger.warn('email_smtp_reauth', { error: safeError(error) });
      const fresh = await this.smtpAccount({ stale: true });
      return await sendMail(fresh, send);
    }
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
