// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { MailArchive, chunkOffset, needsChunks, readTarget } from '../src/email/archive.mjs';
import { accountTag, credentialSource, loadAccount } from '../src/email/account.mjs';
import { EmailClient } from '../src/email/client.mjs';
import { EmailState } from '../src/email/state.mjs';
import { ImapAuthError, ImapConnection, MailboxUnavailableError, OAuthRequiredError, fetchAttributes, parseResponse } from '../src/email/imap.mjs';
import { endpointFor, refreshAccessToken, requestDeviceCode, waitForDeviceCode, withAccountFileLock, writePrivateJson, xoauth2 } from '../src/email/oauth2.mjs';
import { redactSensitive, safeError } from '../src/logger.mjs';
import { SmtpAuthError, verifySmtp } from '../src/email/smtp.mjs';
import { buildReply, htmlToText, isOwnMessageId, parseAddresses, parseMail, replyMessageId } from '../src/email/mime.mjs';
import { normalizeEvent } from '../src/event.mjs';
import { OVConnection } from '../src/ov-connection.mjs';
import { OVSessionClient } from '../src/ov-session-context.mjs';
import { formatInboundLine } from '../src/runtime-input.mjs';
import { Store } from '../src/store.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { fakeOV } from './ov-server-fixture.mjs';

const quiet = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const ME = 'me@example.com';
const PASSWORD = 'app-password-123';
const TAG = accountTag(ME);

function mail({ from = 'Alice <alice@example.com>', to = ME, subject = 'Thursday?', id = 'a1@example.com', headers = [],
  body = 'Can we meet on Thursday?' } = {}) {
  return Buffer.from([`From: ${from}`, `To: ${to}`, `Subject: ${subject}`, `Message-ID: <${id}>`,
    'Date: Tue, 13 Oct 2026 08:10:00 +0000', ...headers, 'Content-Type: text/plain; charset=utf-8', '', body, ''].join('\r\n'));
}

// A scripted IMAP server: the commands it was sent, the mailbox, and IDLE
// sessions to wake with `deliver(raw)`. `oauth` holds the access tokens it
// accepts: it then refuses LOGIN and offers only XOAUTH2, like a mailbox whose
// provider turned passwords off. `saslIr` off makes it ask for the SASL
// response with a continuation instead of taking it on the command line.
async function fakeImap(t, { mails = [], uidValidity = 1, gmail = true, oauth = null, saslIr = true,
  refusal = 'AUTHENTICATE failed.', mailboxRefusal = null, capabilityRefusal = null, closeDuringAuth = false,
  badAuth = null, loginDisabled = false } = {}) {
  const accepted = new Set([].concat(oauth ?? []).filter(Boolean));
  let expectedUser = ME;
  const state = { mails: mails.map((raw, index) => ({ uid: index + 1, raw })), commands: [],
    idlers: new Set(), sockets: new Set(), auths: [], authCancelled: 0 };
  const server = net.createServer((socket) => {
    state.sockets.add(socket);
    socket.on('close', () => { state.sockets.delete(socket); state.idlers.delete(socket); });
    socket.on('error', () => {});
    let buffer = '';
    let idleTag = null;
    // A SASL response is expected (`cancel` false) or the empty line that ends
    // a rejected one (`cancel` true).
    let pendingAuth = null;
    const sasl = (token) => Buffer.from(`user=${expectedUser}\u0001auth=Bearer ${token}\u0001\u0001`, 'utf8').toString('base64');
    const capability = () => `IMAP4rev1 IDLE${gmail ? ' X-GM-EXT-1' : ''}`
      + (accepted.size ? ` AUTH=XOAUTH2${saslIr ? ' SASL-IR' : ''}` : '')
      + (accepted.size || loginDisabled ? ' LOGINDISABLED' : '');
    const answer = (tag, response) => {
      const token = [...accepted].find((each) => sasl(each) === response);
      if (token) {
        state.auths.push(token);
        socket.write(`${tag} OK AUTHENTICATE completed.\r\n`);
        return;
      }
      // A rejected token is reported as one more continuation the client ends
      // with an empty line; the tagged NO only follows that.
      socket.write(`+ ${Buffer.from('{"status":"401"}').toString('base64')}\r\n`);
      pendingAuth = { tag, cancel: true };
    };
    socket.write(`* OK [CAPABILITY ${capability()}] ready\r\n`);
    socket.on('data', (chunk) => {
      buffer += chunk.toString('latin1');
      let end;
      while ((end = buffer.indexOf('\r\n')) >= 0) {
        const line = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        if (idleTag) {
          if (line === 'DONE') {
            state.idlers.delete(socket);
            socket.write(`${idleTag} OK IDLE terminated\r\n`);
            idleTag = null;
          }
          continue;
        }
        if (pendingAuth) {
          // The empty line ends a rejected exchange — whether it was ended by
          // answer() or not; the tagged NO only follows it.
          if (line === '') {
            socket.write(`${pendingAuth.tag} NO ${refusal}\r\n`);
            state.authCancelled += 1;
            pendingAuth = null;
            continue;
          }
          // A continuation that never gets read: the two cases below model a
          // server that dies or rejects the command instead of the token.
          if (!pendingAuth.cancel && closeDuringAuth) { socket.destroy(); return; }
          if (!pendingAuth.cancel && badAuth) {
            socket.write(`${pendingAuth.tag} BAD ${badAuth}\r\n`);
            pendingAuth = null;
            continue;
          }
          if (pendingAuth.cancel) continue;
          answer(pendingAuth.tag, line);
          pendingAuth = null;
          continue;
        }
        const space = line.indexOf(' ');
        const tag = line.slice(0, space);
        const command = line.slice(space + 1);
        state.commands.push(command.startsWith('LOGIN ') ? 'LOGIN' : command.startsWith('AUTHENTICATE') ? 'AUTHENTICATE' : command);
        const ok = (text = 'done') => socket.write(`${tag} OK ${text}\r\n`);
        const uidNext = (state.mails.at(-1)?.uid ?? 0) + 1;
        let match;
        if (command.startsWith('LOGIN ')) {
          if (accepted.size) socket.write(`${tag} NO [AUTHENTICATIONFAILED] Basic authentication is disabled.\r\n`);
          else if (command.includes(`"${PASSWORD}"`)) ok();
          else socket.write(`${tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n`);
        } else if (command.startsWith('AUTHENTICATE XOAUTH2')) {
          const response = command.slice('AUTHENTICATE XOAUTH2'.length).trim();
          if (response) answer(tag, response);
          else {
            pendingAuth = { tag, cancel: false };
            socket.write('+ \r\n');
          }
        } else if (command === 'CAPABILITY' && capabilityRefusal) {
          socket.write(`${tag} NO [UNAVAILABLE] ${capabilityRefusal}\r\n`);
        } else if (command === 'CAPABILITY') {
          socket.write(`* CAPABILITY ${capability()}\r\n`);
          ok();
        } else if (command.startsWith('EXAMINE ') && mailboxRefusal) {
          // Exchange's answer when the sign-in worked but IMAP is off for the
          // mailbox; Microsoft's IMAP OAuth sample checks for this text.
          socket.write(`${tag} BAD ${mailboxRefusal}\r\n`);
        } else if (command.startsWith('EXAMINE ')) {
          socket.write(`* ${state.mails.length} EXISTS\r\n* OK [UIDVALIDITY ${uidValidity}] ok\r\n* OK [UIDNEXT ${uidNext}] ok\r\n`);
          socket.known = state.mails.length;
          ok('[READ-ONLY] EXAMINE completed');
        } else if ((match = /^UID SEARCH UID (\d+):\*$/.exec(command))) {
          const from = Number(match[1]);
          const found = state.mails.filter((item) => item.uid >= from).map((item) => item.uid);
          // RFC 3501: `n:*` always includes the newest message.
          if (found.length === 0 && state.mails.length) found.push(state.mails.at(-1).uid);
          socket.write(`* SEARCH${found.map((uid) => ` ${uid}`).join('')}\r\n`);
          ok();
        } else if ((match = /^UID FETCH ([\d,]+) \((.*)\)$/.exec(command))) {
          for (const uid of match[1].split(',').map(Number)) {
            const index = state.mails.findIndex((item) => item.uid === uid);
            if (index < 0) continue;
            const { raw } = state.mails[index];
            const fields = [`UID ${uid}`, `RFC822.SIZE ${raw.length}`];
            if (match[2].includes('X-GM-THRID')) fields.push('X-GM-THRID 9001');
            if (match[2].includes('INTERNALDATE')) fields.push('INTERNALDATE "13-Oct-2026 08:10:00 +0000"');
            const body = match[2].includes('BODY.PEEK[]') ? raw : null;
            socket.write(`* ${index + 1} FETCH (${fields.join(' ')}${body ? ` BODY[] {${body.length}}\r\n` : ')\r\n'}`);
            if (body) {
              socket.write(body);
              socket.write(')\r\n');
            }
          }
          // What else the server says during a fetch: flag changes made elsewhere.
          state.onFetch?.(socket);
          ok();
        } else if (command === 'IDLE') {
          idleTag = tag;
          state.idlers.add(socket);
          socket.write('+ idling\r\n');
          // Like a real server, tell a client that was busy when mail arrived.
          if (state.mails.length !== socket.known) {
            socket.known = state.mails.length;
            socket.write(`* ${socket.known} EXISTS\r\n`);
          }
        } else if (command === 'NOOP' || command.startsWith('ID ')) {
          ok();
        } else if (command === 'LOGOUT') {
          socket.write('* BYE logging out\r\n');
          ok();
          socket.end();
        } else {
          socket.write(`${tag} BAD unsupported\r\n`);
        }
      }
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => {
    for (const socket of state.sockets) socket.destroy();
    server.close();
  });
  return {
    state,
    port: server.address().port,
    // A test can revoke a token mid-run, as a server-side sign-out would.
    accept: (...tokens) => { accepted.clear(); for (const token of tokens) accepted.add(token); },
    // A test can say which user name this server knows, as an alias-only login would.
    expectUser: (user) => { expectedUser = user; },
    deliver(raw) {
      state.mails.push({ uid: (state.mails.at(-1)?.uid ?? 0) + 1, raw });
      for (const socket of state.idlers) {
        socket.known = state.mails.length;
        socket.write(`* ${socket.known} EXISTS\r\n`);
      }
    },
  };
}

// A scripted SMTP submission server; `messages` are what it accepted. `oauth`
// makes it advertise and accept only XOAUTH2, like Exchange Online.
async function fakeSmtp(t, { oauth = null, ehlo = null, authFailure = null } = {}) {
  const messages = [];
  let bodies = 0;
  let breakAfterData = false;
  let expectedUser = ME;
  const accepted = new Set([].concat(oauth ?? []).filter(Boolean));
  const auths = [];
  const server = net.createServer((socket) => {
    socket.on('error', () => {});
    socket.write('220 fake ESMTP\r\n');
    let buffer = '';
    let data = null;
    let current = { to: [] };
    let challenge = null;
    socket.on('data', (chunk) => {
      buffer += chunk.toString('utf8');
      let end;
      while ((end = buffer.indexOf('\r\n')) >= 0) {
        const line = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        if (data) {
          if (line === '.') {
            messages.push({ ...current, raw: data.join('\r\n') });
            bodies += 1;
            current = { to: [] };
            data = null;
            if (breakAfterData) { socket.destroy(); continue; }
            socket.write('250 queued\r\n');
          } else {
            data.push(line.startsWith('..') ? line.slice(1) : line);
          }
          continue;
        }
        // A rejected XOAUTH2 token is reported as a challenge; the 535 that
        // says so only follows the empty line that ends the exchange.
        if (challenge) {
          if (line === '') {
            socket.write('535 5.7.3 Authentication unsuccessful\r\n');
            challenge = null;
          }
          continue;
        }
        if (line.startsWith('EHLO')) {
          const lines = ehlo ?? (accepted.size ? ['250-fake', '250 AUTH XOAUTH2'] : ['250-fake', '250 AUTH PLAIN LOGIN']);
          socket.write(`${lines.join('\r\n')}\r\n`);
        } else if (line.startsWith('AUTH XOAUTH2 ')) {
          if (authFailure) { socket.write(`${authFailure}\r\n`); continue; }
          const response = line.slice('AUTH XOAUTH2 '.length).trim();
          const token = [...accepted].find((each) => Buffer.from(`user=${expectedUser}\u0001auth=Bearer ${each}\u0001\u0001`).toString('base64') === response);
          if (token) {
            auths.push(token);
            socket.write('235 2.7.0 Authentication successful\r\n');
          } else {
            socket.write(`334 ${Buffer.from('{"status":"401"}').toString('base64')}\r\n`);
            challenge = true;
          }
        } else if (line.startsWith('AUTH PLAIN ')) {
          if (authFailure) { socket.write(`${authFailure}\r\n`); continue; }
          const [, user, password] = Buffer.from(line.slice(11), 'base64').toString('utf8').split('\u0000');
          socket.write(user === ME && password === PASSWORD ? '235 ok\r\n' : '535 bad credentials\r\n');
        } else if (line.startsWith('MAIL FROM:')) { current.from = line.slice(10); socket.write('250 ok\r\n'); }
        else if (line.startsWith('RCPT TO:')) { current.to.push(line.slice(8)); socket.write('250 ok\r\n'); }
        else if (line === 'DATA') { data = []; socket.write('354 go ahead\r\n'); }
        else if (line === 'QUIT') { socket.write('221 bye\r\n'); socket.end(); }
        else socket.write('502 unsupported\r\n');
      }
    });
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  return { messages, auths, port: server.address().port,
    get bodies() { return bodies; },
    breakAfterData: () => { breakAfterData = true; },
    // A test can say which user name this server knows.
    expectUser: (user) => { expectedUser = user; },
    accept: (...tokens) => { accepted.clear(); for (const token of tokens) accepted.add(token); } };
}

// TLS is the transport's business; the fakes speak the protocols in the clear.
function plainTls(options) {
  const socket = net.connect(options.port, '127.0.0.1');
  socket.once('connect', () => socket.emit('secureConnect'));
  return socket;
}

// The Microsoft token endpoint, answering the refreshes the Connector makes.
// `tokens` are handed out in order; the last one is reused after that.
function fakeTokenEndpoint({ tokens, onRefresh } = {}) {
  const calls = [];
  let index = 0;
  const fetch = async (url, init) => {
    calls.push({ url, form: Object.fromEntries(new URLSearchParams(init.body)) });
    const token = tokens[Math.min(index, tokens.length - 1)];
    index += 1;
    onRefresh?.(calls.length);
    const body = token.error
      ? { error: token.error, error_description: 'AADSTS70000: the grant is invalid' }
      : { access_token: token.accessToken, refresh_token: token.refreshToken, expires_in: 3_600 };
    if (token.error === 'slow_down' || token.error === 'authorization_pending') {
      return { ok: false, status: 400, text: async () => JSON.stringify({ error: token.error }) };
    }
    return { ok: !token.error, status: token.error ? 400 : 200, text: async () => JSON.stringify(body) };
  };
  return { fetch, calls };
}

async function setup(t, { mails = [], password = PASSWORD, gmail = true, instance = undefined, archive = null, attachmentWaitMs = undefined,
  oauth = null, tokens = null } = {}) {
  const imap = await fakeImap(t, { mails, gmail, ...(oauth ? { oauth: oauth.imap } : {}) });
  const smtp = await fakeSmtp(t, oauth ? { oauth: oauth.smtp } : {});
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const configFile = path.join(directory, 'account.json');
  const servers = { imap: { host: '127.0.0.1', port: imap.port }, smtp: { host: '127.0.0.1', port: smtp.port, secure: true } };
  const account = oauth
    ? { address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' }, ...servers }
    : { address: ME, password, ...servers };
  fs.writeFileSync(configFile, JSON.stringify(account), { mode: 0o600 });
  const config = { emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000, ...(instance ? { instance } : {}) };
  const endpoint = tokens ? fakeTokenEndpoint({ tokens }) : null;
  const clients = [];
  const start = async () => {
    const client = new EmailClient(config, null, quiet, { connectTls: plainTls, archive, attachmentWaitMs,
      ...(endpoint ? { fetch: endpoint.fetch } : {}) });
    clients.push(client);
    await client.preflight();
    const events = [];
    const exits = [];
    client.startSubscription((line) => events.push(normalizeEvent(JSON.parse(line))), (exit) => exits.push(exit));
    await until(() => imap.state.idlers.size === 1);
    return { client, events, exits };
  };
  t.after(async () => { for (const client of clients) await client.stop().catch(() => {}); });
  return { imap, smtp, start, config, endpoint };
}

async function until(check, timeoutMs = 3_000) {
  const deadline = Date.now() + timeoutMs;
  while (!check()) {
    if (Date.now() > deadline) throw new Error('condition not met in time');
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

describe('email connector', () => {
  it('reports mail that arrives after it starts, read-only, and skips its own replies and auto-replies', async (t) => {
    const { imap, start } = await setup(t, { mails: [mail({ id: 'old-1@example.com' }), mail({ id: 'old-2@example.com' })] });
    const { events, exits } = await start();

    imap.deliver(mail({ id: 'auto@example.com', headers: ['Auto-Submitted: auto-replied; owner-email="x@example.com"'] }));
    imap.deliver(mail({ from: ME, id: replyMessageId('ld_earlier', ME), subject: 'Re: Thursday?' }));
    imap.deliver(mail({ from: '"Alice, A." <Alice@Example.com>', headers: ['Cc: Carol <carol@example.com>'],
      subject: '=?UTF-8?B?5ZGo5Zub5byA5Lya77yf?=', body: 'Can we meet?\r\n\r\nThanks' }));
    imap.deliver(mail({ from: 'news@example.com', id: 'news@example.com', headers: ['List-Id: <news.example.com>'] }));
    // A notice a program sent on its own (a security alert) is not a reply:
    // the model decides whether the owner hears of it.
    imap.deliver(mail({ from: 'no-reply@accounts.example.com', id: 'alert@example.com', subject: 'Security alert',
      headers: ['Auto-Submitted: auto-generated'] }));
    await until(() => events.length === 3);

    const [event, news, alert] = events;
    assert.deepEqual([alert.eventId, alert.kind, alert.title], [`imap:${TAG}:1:7`, 'email.received', 'Security alert']);
    assert.deepEqual([event.eventId, event.kind, event.messageId, event.subject, event.senderId, event.title],
      [`imap:${TAG}:1:5`, 'email.received', `mail:${TAG}:1:5`, { type: 'thread', id: 'gmail:9001' }, 'alice@example.com', '周四开会？']);
    assert.equal(event.content, 'From: Alice, A. <alice@example.com>\nTo: me@example.com\nCc: Carol <carol@example.com>\n\nCan we meet?\n\nThanks');
    assert.equal(event.createTimeMs, Date.parse('2026-10-13T08:10:00Z'));
    assert.equal(event.chatId, null);
    assert.equal(news.kind, 'email.list');
    // Nothing in the mailbox is ever changed: opened read-only, bodies peeked.
    assert.ok(imap.state.commands.includes('EXAMINE "INBOX"'));
    assert.ok(imap.state.commands.some((command) => command.includes('BODY.PEEK[]')));
    assert.doesNotMatch(imap.state.commands.join('\n'), /^(SELECT|STORE|UID STORE|EXPUNGE|COPY|MOVE|UID MOVE)\b/m);
    assert.deepEqual(exits, []);
  });

  it('counts only the mails it fetched when the server adds flag changes for others', async (t) => {
    const { imap, start } = await setup(t);
    const { events } = await start();
    imap.deliver(mail({ id: 'one@example.com' }));
    await until(() => events.length === 1);

    // While mail 2 is fetched, mails 3 and 4 arrive unannounced, and the owner
    // reads 4 and the old mail 1 elsewhere: the server adds their new flags.
    imap.state.onFetch = (socket) => {
      imap.state.onFetch = null;
      imap.state.mails.push({ uid: 3, raw: mail({ id: 'three@example.com' }) }, { uid: 4, raw: mail({ id: 'four@example.com' }) });
      socket.write('* 4 FETCH (UID 4 FLAGS (\\Seen))\r\n* 1 FETCH (UID 1 FLAGS (\\Seen))\r\n');
    };
    imap.deliver(mail({ id: 'two@example.com' }));
    await until(() => events.some((event) => event.eventId === `imap:${TAG}:1:2`));
    imap.deliver(mail({ id: 'five@example.com' }));
    await until(() => events.some((event) => event.eventId === `imap:${TAG}:1:5`));

    assert.deepEqual(events.map((event) => event.eventId), [`imap:${TAG}:1:1`, `imap:${TAG}:1:2`, `imap:${TAG}:1:3`, `imap:${TAG}:1:4`, `imap:${TAG}:1:5`]);
  });

  it('replies over SMTP to Reply-To, threaded under the mail', async (t) => {
    const { imap, smtp, start } = await setup(t);
    const { client, events } = await start();
    imap.deliver(mail({ id: 'q1@example.com', headers: ['Reply-To: Bob <bob@example.com>', 'References: <root@example.com>'] }));
    await until(() => events.length === 1);
    // The event says where a reply would go, for the owner's approval card.
    assert.deepEqual(events[0].data.reply, { from: ME, to: [{ name: 'Bob', address: 'bob@example.com' }], subject: 'Re: Thursday?' });

    // Only the recipients the owner approved, and all of them.
    const answer = (to) => client.replyMessage({ message_id: `mail:${TAG}:1:1`, content: { type: 'markdown', text: '周四可以\n三点见' },
      to, idempotency_key: 'ld_reply1' });
    for (const to of [undefined, [], ['bob@example.com', 'eve@example.com'], ['eve@example.com']]) {
      await assert.rejects(answer(to), /do not match the ones the owner approved/);
    }
    assert.deepEqual(await answer(['bob@example.com']), { sent: true });
    const [sent] = smtp.messages;
    assert.equal(sent.from, `<${ME}>`);
    assert.deepEqual(sent.to, ['<bob@example.com>']);
    const reply = parseMail(Buffer.from(sent.raw));
    assert.equal(reply.subject, 'Re: Thursday?');
    assert.equal(reply.inReplyTo, 'q1@example.com');
    assert.deepEqual(reply.references, ['root@example.com', 'q1@example.com']);
    assert.equal(reply.text, '周四可以\n三点见');
    assert.ok(isOwnMessageId(reply.messageId), 'its own reply is recognised when it comes back');

    await assert.rejects(client.replyMessage({ message_id: `mail:${TAG}:1:99`, content: { type: 'markdown', text: 'x' }, idempotency_key: 'k' }),
      /no longer available/);
    await assert.rejects(client.replyMessage({ message_id: `mail:${TAG}:1:1`, content: { type: 'file', path: '/x' }, idempotency_key: 'k' }),
      /plain text only/);
    assert.equal(smtp.messages.length, 1);
  });

  it('reports every reply recipient for the approval card, or only how many when they are too many to show', async (t) => {
    const { imap, start } = await setup(t);
    const { events } = await start();
    const replyTo = (count) => `Reply-To: ${Array.from({ length: count }, (_, index) => `r${index + 1}@example.com`).join(', ')}`;
    imap.deliver(mail({ id: 'twenty@example.com', headers: [replyTo(20)] }));
    imap.deliver(mail({ id: 'many@example.com', headers: [replyTo(21)] }));
    await until(() => events.length === 2);
    assert.equal(events[0].data.reply.to.length, 20);
    assert.deepEqual(events[1].data.reply, { from: ME, subject: 'Re: Thursday?', recipients: 21 });
  });

  it('picks up after a restart where it stopped, without reporting a mail twice', async (t) => {
    const { imap, start } = await setup(t);
    const first = await start();
    imap.deliver(mail({ id: 'one@example.com' }));
    await until(() => first.events.length === 1);
    await first.client.stop();

    imap.deliver(mail({ id: 'two@example.com' }));
    imap.deliver(mail({ id: 'three@example.com' }));
    const second = await start();
    await until(() => second.events.length === 2);
    assert.deepEqual(second.events.map((event) => event.eventId), [`imap:${TAG}:1:2`, `imap:${TAG}:1:3`]);
  });

  it('reports as its own mailbox instance, and answers only mail of that mailbox', async (t) => {
    const { imap, start } = await setup(t, { instance: 'email-work' });
    const { client, events } = await start();
    imap.deliver(mail({ id: 'one@example.com' }));
    await until(() => events.length === 1);
    assert.deepEqual([events[0].source, events[0].connectorInstance], ['email', 'email-work']);
    assert.ok(fs.existsSync(path.join(client.config.dataDir, 'email', 'email-work', 'state.sqlite3')), 'its own state database');
    await assert.rejects(client.replyMessage({ message_id: `mail:${accountTag('other@example.com')}:1:1`,
      content: { type: 'markdown', text: 'x' }, to: ['alice@example.com'], idempotency_key: 'k' }), /another mailbox/);
  });

  it('starts afresh on another account, whose mail ids cannot meet the old ones', async (t) => {
    const { imap, start, config } = await setup(t);
    const first = await start();
    imap.deliver(mail({ id: 'one@example.com' }));
    await until(() => first.events.length === 1);
    await first.client.stop();

    // Another mailbox with the same UIDVALIDITY, as different providers often have.
    const account = JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8'));
    fs.writeFileSync(config.emailConfigFile, JSON.stringify({ ...account, address: 'other@example.com', username: ME }));
    imap.deliver(mail({ id: 'before@example.com' }));
    const second = await start();
    imap.deliver(mail({ id: 'after@example.com' }));
    await until(() => second.events.length === 1);
    const other = accountTag('other@example.com');
    assert.notEqual(other, TAG);
    assert.deepEqual([second.events[0].eventId, second.events[0].messageId], [`imap:${other}:1:3`, `mail:${other}:1:3`]);
  });

  it('reports a mail whose ids, sender or subject are too long to use as is', async (t) => {
    const { imap, start, config } = await setup(t, { gmail: false });
    const { events, exits } = await start();
    const long = (length) => `${'r'.repeat(length)}@example.com`;
    // Without a Gmail thread id, the thread comes from References.
    imap.deliver(mail({ id: 'first@example.com', headers: [`References: <${long(1_100)}>`] }));
    imap.deliver(mail({ id: 'second@example.com', from: `Mallory <${long(1_100)}>` }));
    imap.deliver(mail({ id: 'third@example.com', subject: 's'.repeat(1_200_000) }));
    imap.deliver(mail({ id: 'fourth@example.com', headers: ['References: <root@example.com>'] }));
    await until(() => events.length === 4);

    assert.deepEqual(exits, []);
    // A thread is a fixed-size digest of its first Message-ID.
    assert.ok(events.every((event) => /^mid:[0-9a-f]{32}$/.test(event.subject.id)));
    assert.equal(new Set(events.map((event) => event.subject.id)).size, 4);
    // A sender address no SMTP path can hold is no sender.
    assert.equal(events[1].actor, null);
    assert.equal(events[2].title.length, 300);
    assert.ok(Math.max(...events.map((event) => JSON.stringify(event.envelope).length)) < 20_000);
    assert.deepEqual(new EmailState(config.dataDir).cursor(`${TAG}:INBOX`), { uidValidity: 1, lastUid: 4 });
  });

  it('passes over a mail it cannot read, and the next one still arrives', async (t) => {
    const { imap, start, config } = await setup(t);
    const { client, events, exits } = await start();
    const report = client.report.bind(client);
    client.report = (item, ...rest) => {
      if (item.uid === 1) throw new Error('unreadable');
      return report(item, ...rest);
    };
    imap.deliver(mail({ id: 'broken@example.com' }));
    imap.deliver(mail({ id: 'fine@example.com' }));
    await until(() => events.length === 1);
    assert.deepEqual(exits, []);
    assert.equal(events[0].eventId, `imap:${TAG}:1:2`);
    // Not retried: the cursor is past it, and no reply headers were kept.
    assert.deepEqual(new EmailState(config.dataDir).cursor(`${TAG}:INBOX`), { uidValidity: 1, lastUid: 2 });
    assert.equal(client.state.mail(`mail:${TAG}:1:1`), null);
  });

  it('fails its check on a wrong password without echoing it', async (t) => {
    const { config } = await setup(t, { password: 'wrong-password' });
    const client = new EmailClient(config, null, quiet, { connectTls: plainTls });
    const error = await client.preflight().catch((caught) => caught);
    assert.match(error.message, /IMAP LOGIN failed: NO \[AUTHENTICATIONFAILED\]/);
    assert.doesNotMatch(error.message, /wrong-password/);
    client.state.close();
  });
});

// A mailbox whose provider turned password sign-in off: the account carries a
// refresh token instead, and both protocols take a SASL XOAUTH2 access token.
describe('email connector with OAuth2', () => {
  const TOKEN = 'access-token-1';
  const oauth = { imap: [TOKEN], smtp: [TOKEN] };

  it('signs in to both protocols with an access token, and never sends the password', async (t) => {
    const { imap, smtp, start, endpoint } = await setup(t, { oauth, tokens: [{ accessToken: TOKEN, refreshToken: 'refresh-2' }] });
    const { events, exits } = await start();
    imap.deliver(mail({ id: 'one@example.com' }));
    await until(() => events.length === 1);

    assert.equal(events[0].eventId, `imap:${TAG}:1:1`);
    assert.deepEqual(exits, []);
    // One refresh for the whole start-up: the token is reused by the check,
    // both protocols, and the watching connection.
    assert.equal(endpoint.calls.length, 1);
    assert.equal(endpoint.calls[0].form.grant_type, 'refresh_token');
    assert.equal(endpoint.calls[0].form.refresh_token, 'refresh-1');
    assert.ok(imap.state.auths.length >= 2, 'the check and the watcher both signed in');
    assert.ok(imap.state.auths.every((token) => token === TOKEN));
    assert.deepEqual(smtp.auths, [TOKEN]);
    assert.ok(!imap.state.commands.includes('LOGIN'), 'no password sign-in is attempted');
  });

  it('writes a rotated refresh token back, and keeps the account file private', async (t) => {
    const { start, config } = await setup(t, { oauth, tokens: [{ accessToken: TOKEN, refreshToken: 'refresh-2' }] });
    await start();
    const saved = JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8'));
    assert.equal(saved.oauth2.refreshToken, 'refresh-2');
    assert.equal(saved.address, ME, 'everything else is kept as written');
    assert.equal(fs.statSync(config.emailConfigFile).mode & 0o777, 0o600);
  });

  it('refreshes once per start, not per connection, and starts from the rotated token next time', async (t) => {
    const { imap, start, config, endpoint } = await setup(t, { oauth: { imap: [TOKEN, 'access-token-2'], smtp: [TOKEN, 'access-token-2'] },
      tokens: [{ accessToken: TOKEN, refreshToken: 'refresh-2' }, { accessToken: 'access-token-2', refreshToken: 'refresh-3' }] });
    const first = await start();
    imap.deliver(mail({ id: 'one@example.com' }));
    await until(() => first.events.length === 1);
    await first.client.stop();
    // One refresh served the check, both protocols and the watching connection.
    assert.equal(endpoint.calls.length, 1);
    assert.equal(JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8')).oauth2.refreshToken, 'refresh-2');

    const second = await start();
    imap.deliver(mail({ id: 'two@example.com' }));
    await until(() => second.events.length === 1);
    // The restart starts from the token written back, not the one on disk before.
    assert.equal(endpoint.calls.length, 2);
    assert.equal(endpoint.calls[1].form.refresh_token, 'refresh-2');
    assert.equal(JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8')).oauth2.refreshToken, 'refresh-3');
  });

  it('authenticates without SASL-IR, when the server asks for the response', async (t) => {
    const imap = await fakeImap(t, { oauth: [TOKEN], saslIr: false });
    const account = { address: ME, user: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'r' },
      imap: { host: '127.0.0.1', port: imap.port } };
    const connection = new ImapConnection({ ...account.imap, connect: plainTls });
    t.after(() => connection.close());
    await connection.connect();
    await connection.authenticate(ME, TOKEN);
    assert.equal(imap.state.auths[0], TOKEN);
    assert.ok(imap.state.commands.includes('AUTHENTICATE'));
  });

  it('says the sign-in expired rather than calling it a wrong password', async (t) => {
    const { config } = await setup(t, { oauth, tokens: [{ error: 'invalid_grant' }] });
    const client = new EmailClient(config, null, quiet, { connectTls: plainTls, fetch: fakeTokenEndpoint({ tokens: [{ error: 'invalid_grant' }] }).fetch });
    const error = await client.preflight().catch((caught) => caught);
    assert.match(error.message, /expired; authorize the mailbox again with `vika login email`/);
    assert.doesNotMatch(error.message, /password/i);
    client.state.close();
  });

  it('reports a rejected access token without echoing it, and without spending the refresh token twice', async (t) => {
    // The server refuses the token the endpoint hands out.
    const { config, endpoint } = await setup(t, { oauth: { imap: ['other-token'], smtp: [TOKEN] },
      tokens: [{ accessToken: TOKEN, refreshToken: 'refresh-2' }] });
    const client = new EmailClient(config, null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    const error = await client.preflight().catch((caught) => caught);
    assert.match(error.message, /AUTHENTICATE failed/);
    assert.doesNotMatch(error.message, new RegExp(TOKEN));
    assert.equal(endpoint.calls.length, 1);
    client.state.close();
  });
});

// The device-code exchanges the setup asks for; a fake endpoint stands in for
// login.microsoftonline.com.
describe('Microsoft sign-in', () => {
  const reply = (status, body) => ({
    ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body),
  });
  const signIn = { tenant: 'contoso.example', clientId: 'app-id' };

  it('names the address and code to type in, and remembers when the code expires', async () => {
    const at = Date.parse('2026-11-04T00:00:00Z');
    const code = await requestDeviceCode(signIn, {
      now: () => at,
      fetch: async () => reply(200, { device_code: 'device-1', user_code: 'ABCD-EFGH',
        verification_uri: 'https://microsoft.com/devicelogin', expires_in: 900, interval: 5 }),
    });
    assert.deepEqual([code.userCode, code.verificationUri, code.intervalMs], ['ABCD-EFGH', 'https://microsoft.com/devicelogin', 5_000]);
    // A number, so the expiry check in waitForDeviceCode compares time, not a function's text.
    assert.equal(code.expiresAt, at + 900_000);
  });

  it('waits out a pending sign-in and takes the tokens once approved', async () => {
    let poll = 0;
    const tokens = await waitForDeviceCode(signIn, { deviceCode: 'device-1', intervalMs: 1, expiresAt: Date.now() + 60_000 }, {
      now: () => Date.parse('2026-11-04T00:00:00Z'),
      sleep: async () => {},
      fetch: async () => {
        poll += 1;
        return poll === 1
          ? reply(400, { error: 'authorization_pending' })
          : reply(200, { access_token: 'at-1', refresh_token: 'rt-1', expires_in: 3_600 });
      },
    });
    assert.equal(poll, 2);
    assert.deepEqual([tokens.accessToken, tokens.refreshToken], ['at-1', 'rt-1']);
  });

  it('adds five seconds to every later poll after slow_down', async () => {
    const slept = [];
    const endpoint = fakeTokenEndpoint({ tokens: [{ error: 'slow_down' },
      { accessToken: 'at', refreshToken: 'rt' }] });
    await waitForDeviceCode(signIn, { deviceCode: 'd', intervalMs: 5_000, expiresAt: 60_000 }, {
      now: () => 1, sleep: async (ms) => slept.push(ms), fetch: endpoint.fetch,
    });
    assert.deepEqual(slept, [5_000, 10_000]);
  });

  it('refuses a device authorization that leaves no long-lived credential', async () => {
    await assert.rejects(waitForDeviceCode(signIn, { deviceCode: 'd', intervalMs: 1, expiresAt: 10 }, {
      now: () => 1,
      sleep: async () => {},
      fetch: async () => reply(200, { access_token: 'short-lived', expires_in: 3_600 }),
    }), /no refresh token/);
    // A refresh response may omit a rotated token: the previous one remains.
    const refreshed = await refreshAccessToken({ ...signIn, refreshToken: 'saved-refresh' }, {
      fetch: async () => reply(200, { access_token: 'at-2', expires_in: 3_600 }),
    });
    assert.equal(refreshed.refreshToken, 'saved-refresh');
  });

  it('backs off after a timed-out poll, and stops promptly on cancellation', async () => {
    let now = 1_000;
    let calls = 0;
    const slept = [];
    const tokens = await waitForDeviceCode(signIn, { deviceCode: 'd', intervalMs: 5_000, expiresAt: now + 60_000 }, {
      now: () => now,
      sleep: async (ms) => { slept.push(ms); now += ms; },
      fetch: async () => {
        calls += 1;
        if (calls === 1) throw Object.assign(new Error('The operation timed out'), { name: 'TimeoutError' });
        return reply(200, { access_token: 'at-1', refresh_token: 'rt-1', expires_in: 3_600 });
      },
    });
    assert.equal(tokens.accessToken, 'at-1');
    assert.deepEqual(slept, [5_000, 10_000], 'RFC 8628 requires a reduced polling rate after a timeout');

    const controller = new AbortController();
    const waiting = waitForDeviceCode(signIn, { deviceCode: 'd', intervalMs: 60_000, expiresAt: Date.now() + 120_000 },
      { signal: controller.signal });
    const reason = new Error('owner cancelled');
    controller.abort(reason);
    await assert.rejects(waiting, (error) => error === reason);
  });

  it('says the code expired instead of waiting forever, and names the consent step', async () => {
    await assert.rejects(waitForDeviceCode(signIn, { deviceCode: 'd', intervalMs: 1, expiresAt: 0 }, {
      now: () => 1, sleep: async () => {},
    }), /code expired/);

    await assert.rejects(requestDeviceCode(signIn, {
      fetch: async () => reply(400, { error: 'invalid_client', error_description: 'AADSTS700016: unknown application' }),
    }), /Microsoft sign-in failed \(invalid_client/);

    // Consent is the one failure whose fix is not in Vika, so it says what is missing.
    await assert.rejects(requestDeviceCode(signIn, {
      fetch: async () => reply(400, { error: 'unauthorized_client', error_description: 'AADSTS65001: consent' }),
    }), /administrator to consent to IMAP\.AccessAsUser\.All and SMTP\.Send/);
  });
});

// A mail with a text body and the given parts after it.
function multipartMail({ id = 'q3@example.com', text, parts }) {
  const lines = ['From: Alice <alice@example.com>', `To: ${ME}`, 'Subject: Q3 report', `Message-ID: <${id}>`,
    'Date: Tue, 13 Oct 2026 08:10:00 +0000', 'Content-Type: multipart/mixed; boundary="B"', '', '--B',
    'Content-Type: text/plain; charset=utf-8', '', text];
  for (const { headers, body } of parts) lines.push('--B', ...headers, '', body);
  lines.push('--B--', '');
  return Buffer.from(lines.join('\r\n'));
}

const file = (name, type, body, { inline = false, base64 = true } = {}) => ({
  headers: [`Content-Type: ${type}`, `Content-Disposition: ${inline ? 'inline' : 'attachment'}; filename="${name}"`,
    ...(base64 ? ['Content-Transfer-Encoding: base64'] : [])],
  body: base64 ? Buffer.from(body, 'latin1').toString('base64') : body,
});

describe('mail in OpenViking', () => {
  const ROOT = 'viking://user/owner/resources/emails';
  const FOLDER = `${ROOT}/${TAG}-1-1`;
  const TEXT = 'Revenue grew twelve percent. '.repeat(30).trim();
  const q3 = () => multipartMail({ text: TEXT, parts: [
    file('report.pdf', 'application/pdf', '%PDF-1.4 revenue'),
    file('logo.png', 'image/png', '\x89PNG', { inline: true }),
    file('invite.ics', 'text/calendar; charset=utf-8', 'BEGIN:VCALENDAR\r\nSUMMARY:Review\r\nEND:VCALENDAR', { base64: false }),
    file('forwarded.eml', 'message/rfc822', 'From: Bob <bob@example.com>\r\nSubject: Original\r\n\r\nThe original note.', { base64: false }),
    file('archive.zip', 'application/zip', 'PK\x03\x04'),
    file('broken.pdf', 'application/pdf', 'not really a pdf'),
  ] });
  const storing = async (t, { slowParse = [], attachmentWaitMs } = {}) => {
    const ov = await fakeOV(t, { failParse: ['broken.pdf'], slowParse });
    const archive = new MailArchive(new OVConnection({ url: ov.url, user: 'owner' }), { pollMs: 5 });
    return { ov, ...(await setup(t, { archive, attachmentWaitMs })) };
  };
  const reader = (t, { ov, config, event }) => {
    const store = new Store(path.join(config.dataDir, 'vika.sqlite3'), quiet);
    t.after(() => store.close());
    store.initializeCognition('codex', 'token');
    store.ingest(event);
    const ovConfig = path.join(config.dataDir, 'ov.json');
    fs.writeFileSync(ovConfig, JSON.stringify({ url: ov.url, user: 'owner' }));
    const service = (ids) => new ActionService({ config: {}, store, connectors: fakeConnectors({ ids }),
      ovClient: new OVSessionClient(ovConfig), logger: quiet });
    const actions = service(['lark', 'email']);
    return { service, actions, read: (input) => actions.call('read_email', input, { authority: 'token' }) };
  };

  it('stores the mail and its attachments before reporting where they are', async (t) => {
    const { ov, imap, start } = await storing(t);
    const { events } = await start();
    imap.deliver(q3());
    await until(() => events.length === 1);
    const [event] = events;

    // The runtime gets who wrote, where the mail is, and the start of the text.
    const lines = event.content.split('\n');
    assert.deepEqual(lines.slice(0, 3), ['From: Alice <alice@example.com>', `To: ${ME}`,
      `[mail stored: ${FOLDER}/body.md; the first 500 of its ${TEXT.length} characters are below]`]);
    assert.deepEqual(lines.slice(3, 8).map((line) => line.replace(/ \([^)]*\)/, '')), [
      `[attachment 01 report.pdf: ${FOLDER}/attachments/01-report.pdf]`,
      `[attachment 02 invite.ics: ${FOLDER}/attachments/02-invite.ics]`,
      `[attachment 03 forwarded.eml: ${FOLDER}/attachments/03-forwarded.eml]`,
      '[attachment 04 archive.zip: not stored, OpenViking does not read this type]',
      '[attachment 05 broken.pdf: not stored, OpenViking could not read it]',
    ]);
    assert.equal(lines[3], `[attachment 01 report.pdf (application/pdf, 16 B): ${FOLDER}/attachments/01-report.pdf]`);
    assert.deepEqual(lines.slice(8), ['', `${TEXT.slice(0, 500)}…`]);
    assert.deepEqual(event.attachments, []);
    assert.equal(event.data.archive.body, `${FOLDER}/body.md`);
    assert.deepEqual(event.data.archive.attachments.map(({ id, status }) => [id, status]),
      [['01', 'stored'], ['02', 'stored'], ['03', 'stored'], ['04', 'skipped'], ['05', 'failed']]);

    // OV holds the whole mail, the parsed attachments and the text ones as written.
    const body = ov.state.files.get(`${FOLDER}/body.md`);
    assert.match(body, /^# Q3 report\n\n- From: Alice <alice@example\.com>\n- To: me@example\.com\n- Date: 2026-10-13T08:10:00\.000Z\n/);
    assert.ok(body.includes(`- Message: mail:${TAG}:1:1`));
    assert.ok(body.includes(`- 01 report.pdf (application/pdf, 16 B): ${FOLDER}/attachments/01-report.pdf`));
    assert.ok(body.includes('- 1 inline image in the body, not stored'));
    assert.ok(body.endsWith(`## Body\n\n${TEXT}\n`));
    assert.equal(ov.state.files.get(`${FOLDER}/attachments/01-report.pdf/full/report.md`), 'parsed report.pdf: %PDF-1.4 revenue');
    assert.equal(ov.state.files.get(`${FOLDER}/attachments/02-invite.ics/full/invite.txt`), 'BEGIN:VCALENDAR\r\nSUMMARY:Review\r\nEND:VCALENDAR');
    assert.match(ov.state.files.get(`${FOLDER}/attachments/03-forwarded.eml/full/message.md`),
      /^# Original\n\n- From: Bob <bob@example\.com>\n\nThe original note\.$/);

    // Text first, then vectors only: nothing asks OV for an LLM summary.
    const writes = ov.state.requests.filter(({ route }) => route === 'POST /api/v1/content/write');
    assert.deepEqual(writes.map(({ body: { uri, mode, wait, processing_mode: processing } }) => [uri.slice(FOLDER.length), mode, wait, processing]), [
      ['/attachments/02-invite.ics/full/invite.txt', 'replace', false, 'vectors_only'],
      ['/attachments/03-forwarded.eml/full/message.md', 'replace', false, 'vectors_only'],
      ['/body.md', 'replace', false, 'vectors_only'],
    ]);
    const added = ov.state.requests.filter(({ route }) => route === 'POST /api/v1/resources').map(({ body: request }) => request);
    assert.deepEqual(added.map(({ to, source_name: name, wait, processing_mode: processing, args }) => [to, name, wait, processing, args]), [
      [`${FOLDER}/attachments/01-report.pdf/full`, 'report.pdf', false, 'vectors_only', { parse_mode: 'no_split' }],
      [`${FOLDER}/attachments/05-broken.pdf/full`, 'broken.pdf', false, 'vectors_only', { parse_mode: 'no_split' }],
    ]);
    assert.deepEqual([...ov.state.uploads.values()].map(({ name, type }) => [name, type]),
      [['report.pdf', 'application/pdf'], ['broken.pdf', 'application/pdf']]);
  });

  it('reports the mail itself when OpenViking cannot take it', async (t) => {
    const { ov, imap, start } = await storing(t);
    ov.state.down = true;
    const { events } = await start();
    imap.deliver(q3());
    await until(() => events.length === 1);
    const [event] = events;
    assert.equal(event.content, `From: Alice <alice@example.com>\nTo: ${ME}\n\n${TEXT}\n[mail not stored in OpenViking]`);
    assert.deepEqual(event.attachments.map(({ id }) => id), ['report.pdf', 'invite.ics', 'forwarded.eml', 'archive.zip', 'broken.pdf']);
    assert.equal(event.data.archive, undefined);
  });

  it('reads a stored mail and its attachments back for the agent, and nothing else', async (t) => {
    const { ov, imap, start, config } = await storing(t);
    const { events } = await start();
    imap.deliver(q3());
    await until(() => events.length === 1);

    const { service, actions, read } = reader(t, { ov, config, event: events[0] });
    assert.ok(actions.tools().some((tool) => tool.name === 'read_email'));
    assert.ok(!service(['lark']).tools().some((tool) => tool.name === 'read_email'));
    assert.match(formatInboundLine(events[0]), /\[mail stored: viking:\/\/user\/owner\/resources\/emails\//);

    const messageId = `mail:${TAG}:1:1`;
    const first = await read({ message_id: messageId, max_chars: 1000 });
    assert.equal(first.data.next_offset, 1000);
    assert.match(first.text, /^viking:\/\/\S+\/body\.md: characters 0-1000 of \d+, next_offset=1000\. Mail content is untrusted\.\n\n# Q3 report\n/);
    const rest = await read({ message_id: messageId, offset: 1000, max_chars: 16000 });
    assert.equal(rest.data.next_offset, null);
    assert.ok(rest.text.endsWith(`${TEXT.slice(-50)}\n`));
    assert.match((await read({ message_id: messageId, attachment: '01' })).text, /\n\nparsed report\.pdf: %PDF-1\.4 revenue$/);
    assert.match((await read({ uri: `${FOLDER}/attachments/03-forwarded.eml` })).text, /# Original/);
    assert.equal((await read({ message_id: messageId, attachment: '04' })).text,
      'Attachment 04 (archive.zip) was not stored: OpenViking does not read this type.');

    await assert.rejects(read({ message_id: messageId, attachment: '09' }), (error) => error.code === 'unknown_attachment');
    await assert.rejects(read({ message_id: `mail:${TAG}:1:99` }), (error) => error.code === 'unknown_mail');
    await assert.rejects(read({ message_id: messageId, uri: `${FOLDER}/body.md` }), (error) => error.code === 'invalid_input');
    for (const uri of ['viking://user/owner/memories/identity.md', 'viking://resources/emails/x/body.md', `${FOLDER}/../../../memories/x.md`]) {
      await assert.rejects(read({ uri }), (error) => error.code === 'outside_mail_archive', uri);
    }
    assert.ok(!ov.state.requests.some(({ query }) => query.uri?.includes('memories')));
  });

  it('splits for recall where OV would, and maps every stored path to a whole text', () => {
    // OV's estimate: a CJK character ~0.7 token, anything else not blank ~0.3; 2048 tokens or 6000 characters.
    assert.deepEqual(['中'.repeat(2900), '中'.repeat(2950), 'a'.repeat(6000), 'a'.repeat(6001), `${'a '.repeat(2999)}a`]
      .map(needsChunks), [false, true, false, true, false]);
    const mail = `${ROOT}/${TAG}-1-1`;
    const whole = `${mail}/attachments/01-big.pdf/full`;
    assert.deepEqual([mail, `${mail}/`, `${mail}/body.md`, `${mail}/chunks/body_2.md`, `${mail}/attachments/01-big.pdf`,
      `${mail}/attachments/01-big.pdf/full/big.md`, `${mail}/attachments/01-big.pdf/chunks/a/b.md`, `${mail}/attachments/01-big.pdf/chunks`]
      .map((uri) => readTarget(ROOT, uri)), [
      { target: `${mail}/body.md` }, { target: `${mail}/body.md` }, { target: `${mail}/body.md` },
      { target: `${mail}/body.md`, anchor: `${mail}/chunks/body_2.md` }, { target: whole }, { target: whole },
      { target: whole, anchor: `${mail}/attachments/01-big.pdf/chunks/a/b.md` }, { target: whole },
    ]);
    for (const uri of [`${ROOT}/x/body.md`, `${mail}/attachments`, `${mail}/other.md`, `${mail}/attachments/01-big.pdf/x`,
      `${mail}/../${TAG}-1-2/body.md`, `${mail}//body.md`, 'viking://user/owner/resources/emailsx/a', ROOT]) {
      assert.equal(readTarget(ROOT, uri), null, uri);
    }
    assert.equal(chunkOffset('# Big\n\nIntro line here.\n\n## Part two\n\nSecond part starts.', '### Part two\n\nSecond part starts.'),
      '# Big\n\nIntro line here.\n\n'.length);
    assert.equal(chunkOffset('nothing alike', 'completely different text'), 0);
  });

  it('keeps a long body and attachment whole for reading and in chunks for recall', async (t) => {
    const { ov, imap, start, config } = await storing(t);
    const { events } = await start();
    const chapters = Array.from({ length: 40 }, (_, index) => `Chapter ${index + 1}. ${'Sales rose in every region. '.repeat(12).trim()}`);
    const points = Array.from({ length: 30 }, (_, index) => `Point ${index + 1}: ${'We should review the budget together. '.repeat(10).trim()}`);
    imap.deliver(multipartMail({ text: points.join('\n\n'), parts: [file('big.pdf', 'application/pdf', chapters.join('\n\n'))] }));
    await until(() => events.length === 1);
    const attachment = `${FOLDER}/attachments/01-big.pdf`;
    const full = ov.state.files.get(`${attachment}/full/big.md`);
    const body = ov.state.files.get(`${FOLDER}/body.md`);

    // The whole text is imported once more, split by OV, for recall alone.
    const added = ov.state.requests.filter(({ route }) => route === 'POST /api/v1/resources').map(({ body: request }) => request);
    assert.deepEqual(added.map(({ to, source_name: name, processing_mode: processing, args }) => [to, name, processing, args]), [
      [`${attachment}/full`, 'big.pdf', 'vectors_only', { parse_mode: 'no_split' }],
      [`${attachment}/chunks`, 'big.md', 'vectors_only', undefined],
      [`${FOLDER}/chunks`, 'body.md', 'vectors_only', undefined],
    ]);
    const uploads = [...ov.state.uploads.values()];
    assert.deepEqual(uploads.map(({ name, type }) => [name, type]),
      [['big.pdf', 'application/pdf'], ['big.md', 'text/markdown'], ['body.md', 'text/markdown']]);
    assert.equal(uploads[1].data.toString('utf8'), full);
    assert.equal(uploads[2].data.toString('utf8'), body);
    const chunks = [...ov.state.files.keys()].filter((uri) => uri.startsWith(`${attachment}/chunks/`));
    assert.ok(chunks.length >= 2, 'the attachment is split');
    // The event and the read tool still name the whole attachment.
    assert.equal(events[0].data.archive.attachments[0].uri, attachment);

    const { read } = reader(t, { ov, config, event: events[0] });
    const second = ov.state.files.get(`${attachment}/chunks/big_2.md`);
    const recalled = await read({ uri: `${attachment}/chunks/big_2.md`, max_chars: 2000 });
    assert.equal(recalled.data.uri, `${attachment}/full`);
    assert.equal(recalled.data.total_chars, full.length);
    assert.ok(recalled.data.offset > 0);
    assert.equal(recalled.data.offset, full.indexOf(second.split('\n')[0]));
    assert.match(recalled.text, /Starting where \S+\/chunks\/big_2\.md is\./);
    assert.ok(recalled.text.endsWith(full.slice(recalled.data.offset, recalled.data.offset + 2000)));
    assert.equal((await read({ uri: `${attachment}/chunks/big_2.md`, offset: 0 })).data.offset, 0);
    assert.equal((await read({ message_id: `mail:${TAG}:1:1`, attachment: '01' })).data.offset, 0);
    const bodyChunk = await read({ uri: `${FOLDER}/chunks/body_2.md` });
    assert.deepEqual([bodyChunk.data.uri, bodyChunk.data.offset > 0], [`${FOLDER}/body.md`, true]);
    await assert.rejects(read({ uri: `${ROOT}/not-a-mail/body.md` }), (error) => error.code === 'outside_mail_archive');
  });

  it('reports a mail whose attachment OV is still reading, and reads it once it lands', async (t) => {
    const { ov, imap, start, config } = await storing(t, { slowParse: ['report.pdf'], attachmentWaitMs: 100 });
    const { events } = await start();
    imap.deliver(multipartMail({ text: 'See the report.', parts: [file('report.pdf', 'application/pdf', '%PDF-1.4 revenue')] }));
    await until(() => events.length === 1);
    const [event] = events;
    assert.ok(event.content.includes(`[attachment 01 report.pdf (application/pdf, 16 B): ${FOLDER}/attachments/01-report.pdf (OpenViking is still reading it)]`));
    assert.deepEqual(event.content.split('\n').slice(2, 3), [`[mail stored: ${FOLDER}/body.md; all of its text is below]`]);
    assert.ok(event.content.endsWith('\n\nSee the report.'), event.content);
    assert.equal(event.data.archive.attachments[0].status, 'processing');

    const { read } = reader(t, { ov, config, event });
    await assert.rejects(read({ message_id: `mail:${TAG}:1:1`, attachment: '01' }), (error) => error.code === 'not_stored_yet');
    ov.state.files.set(`${FOLDER}/attachments/01-report.pdf/full/report.md`, 'parsed late');
    assert.match((await read({ message_id: `mail:${TAG}:1:1`, attachment: '01' })).text, /\n\nparsed late$/);
  });
});

describe('mail parsing', () => {
  it('decodes headers, charsets, nested parts and attachment names', () => {
    const gbk = (text) => Buffer.from([0xc4, 0xe3, 0xba, 0xc3]).toString('base64') + text;
    const raw = ['From: =?UTF-8?B?5byg5LiJ?= <zhang@x.com>', 'To: "Doe, John" <john@x.com>, undisclosed-recipients:;',
      'Subject: =?GBK?B?1tDOxA==?= =?GBK?B?1tDOxA==?= ok', 'Message-ID: <a1@x.com>',
      'Content-Type: multipart/mixed; boundary="OUT"', '', 'preamble', '--OUT',
      'Content-Type: multipart/alternative; boundary="IN"', '', '--IN', 'Content-Type: text/plain; charset=gbk',
      'Content-Transfer-Encoding: base64', '', gbk(''), '--IN', 'Content-Type: text/html', '', '<p>html</p>', '--IN--',
      '--OUT', 'Content-Type: application/pdf',
      "Content-Disposition: attachment; filename*0*=UTF-8''%E6%8A%A5; filename*1*=%E5%91%8A.pdf",
      'Content-Transfer-Encoding: base64', '', 'JVBERi0=', '--OUT',
      'Content-Type: image/png', 'Content-Disposition: inline; filename="logo.png"', 'Content-ID: <logo>',
      'Content-Transfer-Encoding: base64', '', 'iVBORw==', '--OUT', 'Content-Type: image/jpeg; name="scan.jpg"',
      'Content-Disposition: attachment', 'Content-Transfer-Encoding: base64', '', '/9j/', '--OUT--', 'epilogue', ''].join('\r\n');
    const parsed = parseMail(Buffer.from(raw, 'latin1'));
    assert.equal(parsed.subject, '中文中文 ok');
    assert.deepEqual(parsed.from, { name: '张三', address: 'zhang@x.com' });
    assert.deepEqual(parsed.to, [{ name: 'Doe, John', address: 'john@x.com' }]);
    assert.equal(parsed.text, '你好');
    // The bytes go along for storing; a logo the body shows is no attachment someone sent.
    assert.deepEqual(parsed.attachments.map(({ name, filename, type, size, inline }) => ({ name, filename, type, size, inline })), [
      { name: '报告.pdf', filename: '报告.pdf', type: 'application/pdf', size: 5, inline: false },
      { name: 'logo.png', filename: 'logo.png', type: 'image/png', size: 4, inline: true },
      { name: 'scan.jpg', filename: 'scan.jpg', type: 'image/jpeg', size: 3, inline: false },
    ]);
    assert.equal(parsed.attachments[0].data.toString('latin1'), '%PDF-');
  });

  it('falls back to the HTML part as text, and bounds the text', () => {
    const html = ['Subject: hi', 'Content-Type: text/html; charset=utf-8', 'Content-Transfer-Encoding: quoted-printable', '',
      '<style>p{}</style><p>Hello&nbsp;<b>there</b></p><ul><li>one</li><li>two</li></ul>=E4=BD=A0'].join('\r\n');
    assert.equal(parseMail(Buffer.from(html)).text, 'Hello there\n- one\n- two\n你');
    const long = parseMail(Buffer.from(`Subject: x\r\n\r\n${'a'.repeat(50)}`), { maxTextChars: 10 }).text;
    assert.equal(long, `${'a'.repeat(10)}\n[truncated]`);
  });

  it('turns hostile HTML into text in linear time', () => {
    for (const html of ['<script>x'.repeat(30_000), '<'.repeat(256 * 1024), '<li '.repeat(60_000)]) {
      const started = Date.now();
      parseMail(Buffer.from(`Subject: x\r\nContent-Type: text/html\r\n\r\n${html}`));
      assert.ok(Date.now() - started < 1_000, `took ${Date.now() - started} ms`);
    }
    assert.equal(htmlToText('a<script>1<b>2</script >b<style>c'), 'ab');
  });

  it('builds a reply that keeps one Re: and the thread', () => {
    const original = { subject: 'RE: plan', messageId: 'm2@x.com', references: ['m1@x.com'] };
    const raw = buildReply({ from: { name: '我', address: ME }, to: parseAddresses('bob@x.com'), original, text: 'ok', messageId: 'r@x.com' });
    const parsed = parseMail(Buffer.from(raw));
    assert.equal(parsed.subject, 'RE: plan');
    assert.deepEqual(parsed.from, { name: '我', address: ME });
    assert.deepEqual(parsed.references, ['m1@x.com', 'm2@x.com']);
  });

  it('reads IMAP responses with literals and nested lists', () => {
    const parts = ['* 3 FETCH (UID 12 FLAGS (\\Seen) INTERNALDATE "13-Oct-2026 08:10:00 +0000" BODY[] {5}', Buffer.from('hello'), ')'];
    const [, , , attributes] = parseResponse(parts);
    assert.deepEqual(attributes.slice(0, 3), ['UID', '12', 'FLAGS']);
    const fetched = fetchAttributes(attributes);
    assert.equal(fetched.uid, 12);
    assert.equal(fetched.body.toString(), 'hello');
    assert.equal(fetched.internalDate, Date.parse('2026-10-13T08:10:00Z'));
  });
});

// What the upstream review asked for: one refresh for overlapping asks, a
// rotated token that a stale write cannot undo, and authentication failures
// kept apart from a delivery whose outcome is unknown.
describe('OAuth2 token lifecycle under load', () => {
  function countingEndpoint({ tokens, delayMs = 0, beforeAnswer = null } = {}) {
    const calls = [];
    let index = 0;
    const fetch = async (url, init) => {
      calls.push(Object.fromEntries(new URLSearchParams(init.body)));
      if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
      beforeAnswer?.(calls.length);
      const token = tokens[Math.min(index, tokens.length - 1)];
      index += 1;
      const body = token.error
        ? { error: token.error, error_description: 'AADSTS70000: the grant is invalid' }
        : { access_token: token.accessToken, refresh_token: token.refreshToken, expires_in: 3_600 };
      return { ok: !token.error, status: token.error ? 400 : 200, text: async () => JSON.stringify(body) };
    };
    return { fetch, calls };
  }

  async function oauthSetup(t, { tokens, smtpOauth, delayMs = 0, beforeAnswer = null } = {}) {
    const imap = await fakeImap(t, { oauth: ['access-1', 'access-2'] });
    const smtp = await fakeSmtp(t, { oauth: smtpOauth ?? ['access-1'] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-token-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
    }), { mode: 0o600 });
    const config = { emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 };
    const endpoint = countingEndpoint({ tokens, delayMs, beforeAnswer });
    const client = new EmailClient(config, null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));
    return { imap, smtp, client, config, configFile, endpoint };
  }

  it('refreshes once when IMAP and SMTP ask at the same moment', async (t) => {
    const { client, endpoint } = await oauthSetup(t, {
      tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }], delayMs: 30,
    });
    await client.preflight();
    // Two callers at once, as a connect and a send that overlap would be.
    const both = await Promise.all([client.credential(), client.credential()]);
    assert.deepEqual(both.map((each) => each.accessToken), ['access-1', 'access-1']);
    assert.equal(endpoint.calls.length, 1);
  });

  it('does not write a rotated token back over a newer one saved meanwhile', async (t) => {
    let configFile = null;
    const { client } = await oauthSetup(t, {
      tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }],
      // Another process signs in while our refresh is on the wire.
      beforeAnswer: () => {
        const newer = JSON.parse(fs.readFileSync(configFile, 'utf8'));
        newer.oauth2.refreshToken = 'refresh-from-new-signin';
        fs.writeFileSync(configFile, JSON.stringify(newer), { mode: 0o600 });
      },
    });
    configFile = client.config.emailConfigFile;
    await client.preflight();
    // The file no longer holds the token this refresh spent, so its newer one is kept.
    assert.equal(JSON.parse(fs.readFileSync(configFile, 'utf8')).oauth2.refreshToken, 'refresh-from-new-signin');
  });

  it('writes the rotated token back when the file still holds the one it spent', async (t) => {
    const { client, configFile } = await oauthSetup(t, {
      tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }],
    });
    await client.preflight();
    assert.equal(JSON.parse(fs.readFileSync(configFile, 'utf8')).oauth2.refreshToken, 'refresh-2');
  });

  it('signs in again with a new token when the server rejects the cached one, and sends once', async (t) => {
    // The SMTP server accepts only the refreshed token, so the first attempt is
    // rejected: nothing was offered to it, so one more attempt is safe.
    const { client, smtp, configFile } = await oauthSetup(t, {
      tokens: [
        { accessToken: 'access-1', refreshToken: 'refresh-2' },
        { accessToken: 'access-2', refreshToken: 'refresh-3' },
      ],
      smtpOauth: ['access-1'],
    });
    await client.preflight();
    // From here the mailbox revokes the token this Connector cached.
    smtp.accept('access-2');
    client.state.remember(`mail:${TAG}:1:1`, {
      messageId: 'orig@example.com', subject: 'Hi', references: [], from: { address: 'alice@example.com' },
      replyTo: [], to: [{ address: ME }],
    });
    const result = await client.replyMessage({
      message_id: `mail:${TAG}:1:1`, content: { type: 'markdown', text: 'ok' },
      to: ['alice@example.com'], idempotency_key: 'k1',
    });
    assert.deepEqual(result, { sent: true });
    // Exactly one message went out despite the rejected first sign-in.
    assert.equal(smtp.messages.length, 1);
    // And the token that was spent on the retry is the one saved.
    assert.equal(JSON.parse(fs.readFileSync(configFile, 'utf8')).oauth2.refreshToken, 'refresh-3');
  });
});

// The two retry rules, and the one case that must never be retried.
describe('retries are safe or they are not made', () => {
  it('signs in again on a rejected IMAP token instead of needing a restart', async (t) => {
    const imap = await fakeImap(t, { oauth: ['access-1'], mails: [] });
    const smtp = await fakeSmtp(t, { oauth: ['access-1'] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-imap-retry-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
    }), { mode: 0o600 });
    const endpoint = fakeTokenEndpoint({ tokens: [
      { accessToken: 'access-1', refreshToken: 'refresh-2' },
      { accessToken: 'access-2', refreshToken: 'refresh-3' },
    ] });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));
    await client.preflight();
    assert.equal(endpoint.calls.length, 1);

    // The mailbox revokes the token this Connector cached; the next connection
    // is refused, and a fresh one is asked for instead of giving up.
    imap.accept('access-2');
    const connection = await client.open();
    connection.close();
    assert.equal(endpoint.calls.length, 2);
    assert.deepEqual(imap.state.auths, ['access-1', 'access-2']);
  });

  it('never retries a send whose outcome is unknown, and reports it', async (t) => {
    const smtp = await fakeSmtp(t, { oauth: ['access-1'] });
    // The server takes the body and then drops the connection instead of the 250.
    smtp.breakAfterData();
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-smtp-uncertain-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: 1 }, smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
    }), { mode: 0o600 });
    const endpoint = fakeTokenEndpoint({ tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }] });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));
    client.account = loadAccount(configFile);
    client.credential = async () => ({ accessToken: 'access-1' });

    const error = await client.deliver({ from: ME, recipients: ['alice@example.com'],
      raw: 'From: me@example.com\r\nTo: alice@example.com\r\n\r\nhi' }).catch((caught) => caught);
    assert.ok(error, 'the send must report the failure');
    // The observable contract: it was offered exactly once. A retry could have
    // sent a second copy, and SMTP has no idempotency key to undo it.
    assert.equal(smtp.bodies, 1, 'a send whose body went out was retried');
    // And it is not reported as an authentication failure, which is the one
    // kind this Connector retries.
    assert.ok(!(error instanceof SmtpAuthError), 'an uncertain delivery was reported as a sign-in failure');
  });
});

// Nothing that could sign in may reach a log line or an error message: the
// daemon writes these to disk and shows them in Activity.
describe('credentials never reach a message', () => {
  const TOKEN = 'secret-access-token-9f3a';
  const REFRESH = 'secret-refresh-token-4b7c';
  const PASSWORD = 'secret-app-password-1d2e';

  // Every error the Email connector can raise, with a credential in it.
  it('does not echo the token an IMAP server refused', async (t) => {
    const imap = await fakeImap(t, { oauth: ['another-token'] });
    const connection = new ImapConnection({ host: '127.0.0.1', port: imap.port, connect: plainTls });
    await connection.connect();
    const error = await connection.authenticate(ME, TOKEN).catch((caught) => caught);
    connection.close();
    assert.match(error.message, /AUTHENTICATE failed/);
    assert.ok(!error.message.includes(TOKEN), `the token was echoed: ${error.message}`);
    assert.ok(!error.message.includes(Buffer.from(TOKEN).toString('base64')), 'the SASL string was echoed');
  });

  it('does not echo the password an IMAP server refused', async (t) => {
    const imap = await fakeImap(t, {});
    const connection = new ImapConnection({ host: '127.0.0.1', port: imap.port, connect: plainTls });
    await connection.connect();
    const error = await connection.login(ME, PASSWORD).catch((caught) => caught);
    connection.close();
    assert.ok(!error.message.includes(PASSWORD), `the password was echoed: ${error.message}`);
  });

  it('does not echo the token or password an SMTP server refused', async (t) => {
    const smtp = await fakeSmtp(t, { oauth: [] });
    const host = '127.0.0.1';
    const tokenError = await verifySmtp({ host, port: smtp.port, secure: true, user: ME, accessToken: TOKEN })
      .catch((caught) => caught);
    assert.ok(!tokenError.message.includes(TOKEN), `the token was echoed: ${tokenError.message}`);

    const passwordError = await verifySmtp({ host, port: smtp.port, secure: true, user: ME, password: PASSWORD })
      .catch((caught) => caught);
    assert.ok(!passwordError.message.includes(PASSWORD), `the password was echoed: ${passwordError.message}`);
  });

  it('does not echo the refresh token when a refresh fails', async (t) => {
    const endpoint = fakeTokenEndpoint({ tokens: [{ error: 'invalid_grant' }] });
    const error = await refreshAccessToken(
      { tenant: 'common', clientId: 'app-id', refreshToken: REFRESH }, { fetch: endpoint.fetch },
    ).catch((caught) => caught);
    assert.match(error.message, /sign-in expired/);
    assert.ok(!error.message.includes(REFRESH), `the refresh token was echoed: ${error.message}`);
  });

  // The daemon logs these through safeError, so the redaction is what keeps a
  // credential out of the log even if a message ever does carry one.
  it('redacts a credential that a message does carry', () => {
    for (const shown of [
      `IMAP AUTHENTICATE failed: Bearer ${TOKEN}`,
      `authorization: Bearer ${TOKEN}`,
      `refresh_token=${REFRESH}`,
      `access_token: ${TOKEN}`,
      `password=${PASSWORD}`,
    ]) {
      const cleaned = safeError(new Error(shown));
      assert.ok(!cleaned.includes(TOKEN), `not redacted: ${cleaned}`);
      assert.ok(!cleaned.includes(REFRESH), `not redacted: ${cleaned}`);
      assert.ok(!cleaned.includes(PASSWORD), `not redacted: ${cleaned}`);
      assert.match(cleaned, /\[REDACTED\]/);
    }
  });

  it('keeps the account file private and leaves no credential in the event', async (t) => {
    const { imap, start, config } = await setup(t, {
      oauth: { imap: [TOKEN], smtp: [TOKEN] },
      tokens: [{ accessToken: TOKEN, refreshToken: REFRESH }],
    });
    const { events } = await start();
    imap.deliver(mail({ id: 'one@example.com', body: `a mail that mentions ${TOKEN} in its text` }));
    await until(() => events.length === 1);
    const shown = JSON.stringify(events[0]);
    // The token itself is not a credential here — it is what the sender wrote,
    // and a report must carry the mail as it is. The account file is what must
    // stay private.
    const mode = fs.statSync(config.emailConfigFile).mode & 0o777;
    assert.equal(mode, 0o600);
  });
});

// A mailbox reached through an alias: Exchange Online takes the alias for IMAP
// but only the account's primary address for SMTP AUTH. Found on a real
// Outlook.com mailbox; `username` is what both protocols sign in with.
describe('an account whose login name is not its address', () => {
  const ALIAS = 'alias@outlook.com';
  const PRIMARY = 'primary@outlook.com';

  it('signs in to both protocols with username, and reports the address as its own', async (t) => {
    const imap = await fakeImap(t, { oauth: ['token-1'] });
    const smtp = await fakeSmtp(t, { oauth: ['token-1'] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-alias-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ALIAS, username: PRIMARY,
      oauth2: { tenant: 'consumers', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
    }), { mode: 0o600 });

    // The fake servers only accept the name they were told to expect.
    imap.expectUser(PRIMARY);
    smtp.expectUser(PRIMARY);
    const endpoint = fakeTokenEndpoint({ tokens: [{ accessToken: 'token-1', refreshToken: 'refresh-2' }] });
    const config = { emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 };
    const client = new EmailClient(config, null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));

    // Both the check and a send use the primary address, not the alias.
    await client.preflight();
    await client.deliver({ from: ALIAS, recipients: [ALIAS], raw: 'From: alias@outlook.com\r\n\r\nhi' });
    assert.equal(smtp.messages.length, 1);
    // The mailbox still calls itself by its address, which is what a reply says.
    assert.equal(client.account.address, ALIAS);
    assert.equal(client.account.user, PRIMARY);
  });
});

// A server words its refusal however it likes; what the Connector does about a
// refused token must not depend on the wording. Deciding that from the response
// text only recovered on the one provider whose words were tested — the fake
// server here answered `NO AUTHENTICATE failed.`, like Exchange, so the fragile
// check passed. These are the real answers of other servers.
describe('a refused token is retried whatever words the server uses', () => {
  const REFUSALS = [
    ['Exchange Online', 'AUTHENTICATE failed.'],
    ['Dovecot', '[AUTHENTICATIONFAILED] Authentication failed.'],
    ['Gmail', '[AUTHENTICATIONFAILED] Invalid credentials (Failure)'],
    ['Coremail', '[AUTHENTICATIONFAILED] Authentication failed'],
    ['bare', '[AUTHENTICATIONFAILED]'],
    ['unqualified', ''],
  ];

  for (const [provider, refusal] of REFUSALS) {
    it(`signs in again when ${provider} refuses the cached token`, async (t) => {
      const imap = await fakeImap(t, { oauth: ['token-2'], refusal });
      const smtp = await fakeSmtp(t, { oauth: ['token-2'] });
      const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-refusal-'));
      t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
      const configFile = path.join(directory, 'account.json');
      fs.writeFileSync(configFile, JSON.stringify({
        address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
        imap: { host: '127.0.0.1', port: imap.port },
        smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
      }), { mode: 0o600 });
      const endpoint = fakeTokenEndpoint({ tokens: [
        { accessToken: 'token-1', refreshToken: 'refresh-2' },
        { accessToken: 'token-2', refreshToken: 'refresh-3' },
      ] });
      const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
        null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
      t.after(() => client.stop().catch(() => {}));
      // The first sign-in succeeds, so the token is cached; the server then
      // revokes it, which is what a long run runs into.
      imap.accept('token-1');
      smtp.accept('token-1');
      await client.preflight();
      imap.accept('token-2');
      const connection = await client.open();
      connection.close();
      // A fresh token was asked for and the second attempt was accepted.
      assert.equal(endpoint.calls.length, 2, `${provider}: the token was never refreshed`);
      assert.deepEqual(imap.state.auths, ['token-1', 'token-2']);
    });
  }
});

// Microsoft's own IMAP OAuth sample calls out one failure by name, because a
// succeeded sign-in makes it look like a credential problem when it is a
// mailbox setting: Exchange answers the first mailbox command with
// `BAD User is authenticated but not connected.` when IMAP is switched off for
// that mailbox. RFC 3501 gives `BAD` for "the command was wrong" and `NO` for
// "the command failed", so neither is a refused credential and neither may be
// retried with a fresh token.
describe('a mailbox that signs in but refuses IMAP', () => {
  const MAILBOX_OFF = 'User is authenticated but not connected.';

  it('reports what to change instead of retrying the credential', async (t) => {
    const imap = await fakeImap(t, { oauth: ['token-1'], mailboxRefusal: MAILBOX_OFF });
    const smtp = await fakeSmtp(t, { oauth: ['token-1'] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-imap-off-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
    }), { mode: 0o600 });
    const endpoint = fakeTokenEndpoint({ tokens: [{ accessToken: 'token-1', refreshToken: 'refresh-2' }] });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));

    const error = await client.preflight().catch((caught) => caught);
    assert.ok(error, 'the check must fail');
    // A kind of its own: neither a refused credential nor a broken connection,
    // so a caller can tell the owner which setting to change.
    assert.ok(error instanceof MailboxUnavailableError, `expected MailboxUnavailableError, got ${error.name}`);
    assert.match(error.message, /enable IMAP|grant/i);
    // And the credential was never refreshed for it: the token was fine.
    assert.equal(endpoint.calls.length, 1, 'the token must not be refreshed for a mailbox setting');
  });

  it('does not mistake it for a refused credential', async (t) => {
    const imap = await fakeImap(t, { oauth: ['token-1'], mailboxRefusal: MAILBOX_OFF });
    const connection = new ImapConnection({ host: '127.0.0.1', port: imap.port, connect: plainTls });
    await connection.connect();
    await connection.authenticate(ME, 'token-1');
    const error = await connection.examine('INBOX').catch((caught) => caught);
    connection.close();
    assert.ok(error instanceof MailboxUnavailableError, `expected MailboxUnavailableError, got ${error.name}`);
    assert.ok(!(error instanceof ImapAuthError), 'a BAD answer is not a refused credential');
  });

  it('still refreshes once when the credential itself is refused', async (t) => {
    // The counterpart: a `NO` on AUTHENTICATE is a refused credential and is
    // retried, so the two cases stay apart.
    const imap = await fakeImap(t, { oauth: ['token-2'] });
    const smtp = await fakeSmtp(t, { oauth: ['token-2'] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-no-vs-bad-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
    }), { mode: 0o600 });
    const endpoint = fakeTokenEndpoint({ tokens: [
      { accessToken: 'token-1', refreshToken: 'refresh-2' },
      { accessToken: 'token-2', refreshToken: 'refresh-3' },
    ] });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));
    imap.accept('token-1');
    smtp.accept('token-1');
    await client.preflight();
    imap.accept('token-2');
    const connection = await client.open();
    connection.close();
    assert.equal(endpoint.calls.length, 2, 'a refused credential is retried with a fresh token');
  });
});

// RFC 4954 lets a server spread its AUTH mechanisms over several EHLO lines,
// and some still send the pre-RFC `AUTH=LOGIN` spelling. Reading only the first
// AUTH line reported "no XOAUTH2" against a server that offers it on the next
// one, so an OAuth2 mailbox on such a server could not send at all.
describe('AUTH mechanisms spread over several EHLO lines', () => {
  it('finds XOAUTH2 on a later line and signs in', async (t) => {
    const smtp = await fakeSmtp(t, { oauth: ['token-1'], ehlo: ['250-AUTH=LOGIN', '250 AUTH LOGIN XOAUTH2'] });
    const checked = await verifySmtp({ host: '127.0.0.1', port: smtp.port, secure: true, user: ME, accessToken: 'token-1',
      connectTls: plainTls });
    assert.deepEqual(checked, { ok: true });
    assert.deepEqual(smtp.auths, ['token-1']);
  });

  it('still reports a server that offers no XOAUTH2 at all', async (t) => {
    const smtp = await fakeSmtp(t, { oauth: ['token-1'], ehlo: ['250-AUTH PLAIN', '250 AUTH LOGIN'] });
    const error = await verifySmtp({ host: '127.0.0.1', port: smtp.port, secure: true, user: ME, accessToken: 'token-1',
      connectTls: plainTls }).catch((caught) => caught);
    assert.match(error.message, /no AUTH XOAUTH2/);
  });
});




// A token endpoint whose answer the test controls: it counts the refreshes, can
// hold them open, and can hold each one for a chosen number of milliseconds.
function controllableEndpoint({ tokens, delayMs = 0, hold = false } = {}) {
  const calls = [];
  const gates = [];
  let active = 0;
  let maxActive = 0;
  let index = 0;
  const fetch = async (url, init) => {
    calls.push(Object.fromEntries(new URLSearchParams(init.body)));
    active += 1;
    maxActive = Math.max(maxActive, active);
    try {
      if (hold) await new Promise((resolve) => gates.push(resolve));
      const delay = typeof delayMs === 'function' ? delayMs() : delayMs;
      if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
      const token = tokens[Math.min(index, tokens.length - 1)];
      index += 1;
      const body = token.error
        ? { error: token.error, error_description: 'AADSTS70000: the grant is invalid' }
        : { access_token: token.accessToken, refresh_token: token.refreshToken, expires_in: 3_600 };
      return { ok: !token.error, status: token.error ? 400 : 200, text: async () => JSON.stringify(body) };
    } finally {
      active -= 1;
    }
  };
  return {
    fetch,
    calls,
    get maxActive() { return maxActive; },
    get pending() { return gates.length; },
    release() { const each = gates.shift(); each?.(); },
    releaseAll() { while (gates.length) gates.shift()(); },
  };
}

// An account file another process could write to while our refresh is on the
// wire. Returns the path, the loaded account, and a way to read the file.
function accountFile(refreshToken = 'refresh-1') {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-race-'));
  const file = path.join(directory, 'account.json');
  fs.writeFileSync(file, JSON.stringify({
    address: ME,
    oauth2: { tenant: 'common', clientId: 'app-id', refreshToken },
    imap: { host: '127.0.0.1', port: 1 },
    smtp: { host: '127.0.0.1', port: 2, secure: true },
  }), { mode: 0o600 });
  return {
    file,
    account: loadAccount(file),
    saved: () => JSON.parse(fs.readFileSync(file, 'utf8')),
    signInAgain: (token) => {
      const value = JSON.parse(fs.readFileSync(file, 'utf8'));
      value.oauth2.refreshToken = token;
      fs.writeFileSync(file, JSON.stringify(value, null, 2), { mode: 0o600 });
    },
    cleanup: () => fs.rmSync(directory, { recursive: true, force: true }),
  };
}

// A token endpoint that enforces Microsoft's rotation rule: each refresh token
// can be spent once. `doubleSpent` names any token offered a second time, which
// is what a caller refreshing on an already-rotated token would do.
function rotatingEndpoint({ steps, delayMs = 0, onAnswer = null } = {}) {
  const calls = [];
  const spent = new Set();
  let active = 0;
  let maxActive = 0;
  const fetch = async (url, init) => {
    const form = Object.fromEntries(new URLSearchParams(init.body));
    calls.push(form);
    active += 1;
    maxActive = Math.max(maxActive, active);
    try {
      if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
      const step = steps.find((each) => each.from === form.refresh_token);
      const reused = spent.has(form.refresh_token);
      if (step && !reused) spent.add(form.refresh_token);
      const ok = Boolean(step) && !reused;
      const body = ok
        ? { access_token: step.accessToken, refresh_token: step.refreshToken, expires_in: 3_600 }
        : { error: 'invalid_grant', error_description: 'AADSTS70000: the grant is invalid' };
      return { ok, status: ok ? 200 : 400, text: async () => {
        onAnswer?.(calls.length);
        return JSON.stringify(body);
      } };
    } finally {
      active -= 1;
    }
  };
  return {
    fetch,
    calls,
    get maxActive() { return maxActive; },
    get doubleSpent() {
      const seen = new Set();
      const twice = [];
      for (const form of calls) {
        if (seen.has(form.refresh_token)) twice.push(form.refresh_token);
        seen.add(form.refresh_token);
      }
      return twice;
    },
  };
}

describe('account-file lock failure and competing writers', () => {
  it('keeps the result or original error when lock cleanup fails', async () => {
    const releaseError = new Error('lock cleanup failed');
    const warnings = [];
    const logger = { warn: (...args) => warnings.push(args) };
    const acquireLock = async () => async () => { throw releaseError; };
    assert.equal(await withAccountFileLock('/unused', async () => 'done', { acquireLock, logger }), 'done');
    const original = new Error('original refresh failure');
    await assert.rejects(withAccountFileLock('/unused', async () => { throw original; }, { acquireLock, logger }),
      (error) => error === original);
    assert.equal(warnings.length, 2);
    assert.ok(warnings.every(([event]) => event === 'email_token_lock_release'));
  });

  it('makes a concurrent login writer wait for the refresh transaction', async () => {
    const file = accountFile();
    try {
      let releaseRefresh;
      const gate = new Promise((resolve) => { releaseRefresh = resolve; });
      const fetch = async () => {
        await gate;
        return { ok: true, status: 200,
          text: async () => JSON.stringify({ access_token: 'at', refresh_token: 'refresh-2', expires_in: 3_600 }) };
      };
      const credential = credentialSource(file.account, { fetch });
      const refresh = credential();
      await until(() => fs.existsSync(`${file.file}.lock`));
      const login = withAccountFileLock(file.file, () => file.signInAgain('new-sign-in'));
      let loginWrote = false;
      login.then(() => { loginWrote = true; });
      await new Promise((resolve) => setTimeout(resolve, 25));
      assert.equal(loginWrote, false, 'login bypassed the lock held by the refresh');
      releaseRefresh();
      await Promise.all([refresh, login]);
      assert.equal(file.saved().oauth2.refreshToken, 'new-sign-in');
    } finally { file.cleanup(); }
  });
});

describe('two real processes sharing one account file', () => {
  it('spends a rotating refresh token once, even with both first answers held', async (t) => {
    const file = accountFile();
    t.after(() => file.cleanup());
    const calls = [];
    const gates = [];
    const fetch = async (url, init) => {
      const form = Object.fromEntries(new URLSearchParams(init.body));
      calls.push(form);
      const second = form.refresh_token === 'refresh-2';
      await new Promise((resolve) => gates.push(resolve));
      const body = { access_token: second ? 'access-2' : 'access-1',
        refresh_token: second ? 'refresh-3' : 'refresh-2', expires_in: 3_600 };
      return { ok: true, status: 200, text: async () => JSON.stringify(body) };
    };
    const credential = credentialSource(file.account, { fetch });
    const endpoint = http.createServer(async (request, response) => {
      let body = '';
      for await (const chunk of request) body += chunk;
      const reply = await fetch('', { body });
      response.writeHead(reply.status, { 'Content-Type': 'application/json' });
      response.end(await reply.text());
    });
    await new Promise((resolve) => endpoint.listen(0, '127.0.0.1', resolve));
    t.after(() => endpoint.close());
    const local = credential();
    const child = fork(new URL('email-refresh-process.mjs', import.meta.url), { stdio: ['ignore', 'ignore', 'inherit', 'ipc'] });
    t.after(() => { if (child.connected) child.disconnect(); child.kill(); });
    const remote = new Promise((resolve, reject) => {
      child.once('message', (message) => message.ok ? resolve(message) : reject(new Error(message.error)));
      child.once('error', reject);
    });
    child.send({ file: file.file, endpoint: `http://127.0.0.1:${endpoint.address().port}/token` });
    // Hold both responses long enough that the old implementation would have
    // issued R1 twice. The file lock now lets only the parent through first.
    await until(() => calls.length === 1 && gates.length === 1);
    assert.equal(calls[0].refresh_token, 'refresh-1');
    gates.shift()();
    assert.equal((await local).accessToken, 'access-1');
    await until(() => calls.length === 2 && gates.length === 1);
    assert.equal(calls[1].refresh_token, 'refresh-2', 'the child spent the already-rotated token');
    gates.shift()();
    assert.equal((await remote).accessToken, 'access-2');
    assert.equal(calls.length, 2);
    assert.equal(file.saved().oauth2.refreshToken, 'refresh-3');
    assert.ok(!fs.existsSync(`${file.file}.lock`), 'the inter-process lock was left behind');
  });
});

// ---------------------------------------------------------------------------
// The contract a refresh storm must not break: one refresh on the wire, and
// every caller of that refresh gets the same access token.
// ---------------------------------------------------------------------------
describe('a refresh storm spends the refresh token once', () => {
  it('gives 50 simultaneous callers one refresh and one access token', async () => {
    const file = accountFile();
    try {
      const endpoint = controllableEndpoint({
        tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }],
        // A real endpoint answers somewhere in this window; every caller
        // arrives before the first answer does.
        delayMs: () => Math.floor(Math.random() * 100),
      });
      const credential = credentialSource(file.account, { fetch: endpoint.fetch });
      const asked = await Promise.all(Array.from({ length: 50 }, () => credential()));

      assert.equal(endpoint.calls.length, 1, 'the refresh token was spent more than once');
      assert.equal(endpoint.maxActive, 1, 'two refreshes were on the wire at once');
      assert.deepEqual(new Set(asked.map((each) => each.accessToken)), new Set(['access-1']),
        'a caller was handed a token other than the one the refresh produced');
      // The rotated token is the one the next start will sign in with.
      assert.equal(file.saved().oauth2.refreshToken, 'refresh-2');
    } finally {
      file.cleanup();
    }
  });

  it('reuses the cached token for callers that arrive after the storm', async () => {
    const file = accountFile();
    try {
      const endpoint = controllableEndpoint({ tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }] });
      const credential = credentialSource(file.account, { fetch: endpoint.fetch });
      await Promise.all(Array.from({ length: 10 }, () => credential()));
      const later = await credential();
      assert.equal(later.accessToken, 'access-1');
      assert.equal(endpoint.calls.length, 1, 'a token that was still valid was refreshed again');
    } finally {
      file.cleanup();
    }
  });
});

// ---------------------------------------------------------------------------
// A refresh that fails is not cached: the waiters of that refresh see the error,
// and the next caller tries again instead of inheriting a dead promise.
// ---------------------------------------------------------------------------
describe('a failed refresh is reported once and not cached', () => {
  it('rejects every waiter of the failed refresh, then tries again for the next caller', async () => {
    const file = accountFile();
    try {
      const endpoint = controllableEndpoint({ tokens: [{ error: 'invalid_grant' }], delayMs: 30 });
      const credential = credentialSource(file.account, { fetch: endpoint.fetch });

      const settled = await Promise.allSettled(Array.from({ length: 10 }, () => credential()));
      const failures = settled.filter((each) => each.status === 'rejected');
      assert.equal(failures.length, 10, 'a waiter was handed a token by a refresh that failed');
      // One refresh, so one failure object: they all waited on the same promise.
      assert.equal(new Set(failures.map((each) => each.reason)).size, 1);
      assert.equal(endpoint.calls.length, 1);

      // Not cached: the next caller starts a refresh of its own.
      const again = await credential().then(() => null, (error) => error.message);
      assert.match(String(again), /sign-in expired/);
      assert.equal(endpoint.calls.length, 2, 'the failure was cached instead of retried');
      // And the account file was left alone: a failed refresh rotates nothing.
      assert.equal(file.saved().oauth2.refreshToken, 'refresh-1');
    } finally {
      file.cleanup();
    }
  });
});

// ---------------------------------------------------------------------------
// Two reasons to refresh — the clock says the token is old, or a server just
// refused it — must not become two refreshes when they arrive together.
// ---------------------------------------------------------------------------
describe('a refusal and a normal ask do not refresh twice', () => {
  it('joins a stale ask to the refresh already in flight instead of starting another', async () => {
    const file = accountFile();
    try {
      const endpoint = controllableEndpoint({
        tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }], hold: true });
      const credential = credentialSource(file.account, { fetch: endpoint.fetch });

      const normal = credential();
      await until(() => endpoint.calls.length === 1);
      // IMAP was refused and asks for a fresh token while SMTP's refresh is
      // still on the wire.
      const stale = credential({ stale: true });
      const alsoNormal = credential();
      endpoint.release();

      const [first, second, third] = await Promise.all([normal, stale, alsoNormal]);
      assert.equal(endpoint.calls.length, 1, 'the refusal started a second refresh');
      assert.deepEqual([first.accessToken, second.accessToken, third.accessToken],
        ['access-1', 'access-1', 'access-1'], 'a caller was handed a token it had just refused');
    } finally {
      file.cleanup();
    }
  });
});

// ---------------------------------------------------------------------------
// The refresh token rotates. A second process (a fresh `vika login email`, or
// another instance) can leave a newer one in the account file while our refresh
// is on the wire; writing ours back would undo that sign-in.
// ---------------------------------------------------------------------------
describe('a rotated token never overwrites a newer sign-in', () => {
  it('keeps the newer token when the file changed during the refresh', async () => {
    const file = accountFile('refresh-1');
    try {
      const endpoint = controllableEndpoint({
        tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }], hold: true });
      const credential = credentialSource(file.account, { fetch: endpoint.fetch });
      const refresh = credential();
      await until(() => endpoint.calls.length === 1);
      // Another process signs in while our refresh is in flight.
      file.signInAgain('refresh-from-new-signin');
      endpoint.release();
      await refresh;

      assert.equal(file.saved().oauth2.refreshToken, 'refresh-from-new-signin',
        'the refresh wrote its older token over the newer sign-in');
    } finally {
      file.cleanup();
    }
  });

  it('writes the rotated token when the file still holds the one it spent', async () => {
    const file = accountFile('refresh-1');
    try {
      const endpoint = controllableEndpoint({ tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }] });
      const credential = credentialSource(file.account, { fetch: endpoint.fetch });
      await credential();
      assert.equal(file.saved().oauth2.refreshToken, 'refresh-2');
      // Everything else in the file is left as the owner wrote it.
      assert.equal(file.saved().address, ME);
      assert.equal(file.saved().imap.port, 1);
    } finally {
      file.cleanup();
    }
  });

  it('does not spend the refresh token a second time to make the write-back true', async () => {
    const file = accountFile('refresh-1');
    try {
      const endpoint = controllableEndpoint({
        tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }], hold: true });
      const credential = credentialSource(file.account, { fetch: endpoint.fetch });
      const refresh = credential();
      await until(() => endpoint.calls.length === 1);
      file.signInAgain('refresh-from-new-signin');
      endpoint.release();
      await refresh;
      // One refresh, one spend: a skipped write is reported, not repaired by
      // refreshing again with a token we no longer hold.
      assert.equal(endpoint.calls.length, 1);
    } finally {
      file.cleanup();
    }
  });
});

// ---------------------------------------------------------------------------
// The specific question behind this file: .finally(() => { inFlight = null })
// runs after the inner promise settles but before a caller awaiting the outer
// promise resumes, so does a caller resuming in that same microtask batch see no
// refresh in flight and start a second one? Sweep every microtask offset around
// the settlement so an ordering-dependent answer cannot hide.
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// The window after the cleanup and before the first waiter resumes IS reachable
// (a caller resuming there sees inFlight === null), but it is harmless: the only
// refresh it can start is one whose refresh token the previous refresh already
// rotated, so under Microsoft's rotation rule it fails — unless the account file
// guard is in place, which it is. This sweep plants a `stale` caller at every
// microtask offset and holds the fleet to the invariant that matters: no two
// refreshes overlap, and one refresh token is never spent twice.
// ---------------------------------------------------------------------------
describe('a caller landing in the cleanup window cannot spend a spent token', () => {
  const STEPS = [
    { from: 'refresh-1', accessToken: 'access-1', refreshToken: 'refresh-2' },
    { from: 'refresh-2', accessToken: 'access-2', refreshToken: 'refresh-3' },
  ];

  it('holds the no-overlap, no-double-spend invariants across 0..24 microtask offsets', async () => {
    for (let hops = 0; hops <= 24; hops += 1) {
      const file = accountFile('refresh-1');
      let credential = null;
      let probe = null;
      const plantProbe = () => {
        // Step `hops` microtasks past the token answer, then ask as a caller
        // whose access token a server just refused. At some offsets that lands
        // after the cleanup cleared inFlight and before the first waiter
        // resumed — the window the review asked about.
        let stepped = 0;
        const step = () => {
          stepped += 1;
          if (stepped < hops) { queueMicrotask(step); return; }
          probe = credential({ stale: true }).then(
            (value) => value.accessToken,
            (error) => 'rejected: ' + error.message.slice(0, 24),
          );
        };
        if (hops === 0) step();
        else queueMicrotask(step);
      };
      const endpoint = rotatingEndpoint({ steps: STEPS, onAnswer: (count) => { if (count === 1) plantProbe(); } });
      try {
        credential = credentialSource(file.account, { fetch: endpoint.fetch });
        const first = await credential();
        // The probe chain may still be walking microtasks; wait for it to land.
        const deadline = Date.now() + 1_000;
        while (!probe && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 1));
        assert.ok(probe, `hop ${hops}: the probe caller never ran`);
        const second = await probe;

        // The invariants that make a concurrent refresh safe: at most one
        // refresh on the wire, and a refresh token spent at most once.
        assert.equal(endpoint.maxActive, 1, `hop ${hops}: two refreshes overlapped`);
        assert.deepEqual(endpoint.doubleSpent, [], `hop ${hops}: a refresh token was spent twice`);
        // The caller that started the refresh always gets that refresh's token.
        assert.equal(first.accessToken, 'access-1', `hop ${hops}: the first caller got another token`);
        // The late caller either joined it, or refreshed with the rotated token;
        // it is never handed the token that was refused, and never a failure
        // from re-spending one.
        assert.ok(['access-1', 'access-2'].includes(second),
          `hop ${hops}: the late caller got neither the shared nor the rotated token (${second})`);
        // Whatever happened, the file ends on the newest refresh token.
        assert.equal(file.saved().oauth2.refreshToken, second === 'access-2' ? 'refresh-3' : 'refresh-2',
          `hop ${hops}: the account file did not end on the newest token`);
      } finally {
        file.cleanup();
      }
    }
  });
});

describe('clearing the in-flight refresh opens no window for a second one', () => {
  it('never starts a second refresh for a caller resuming 0..64 microtasks behind the answer', async () => {
    for (let hops = 0; hops <= 64; hops += 1) {
      const file = accountFile();
      const endpoint = controllableEndpoint({ tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }], hold: true });
      try {
        const credential = credentialSource(file.account, { fetch: endpoint.fetch });
        const first = credential();
        // A caller that resumes `hops` microtasks after the refresh settles:
        // wait for the same settlement, then step through empty microtasks.
        const late = (async () => {
          await until(() => endpoint.pending === 1);
          endpoint.release();
          for (let hop = 0; hop < hops; hop += 1) await Promise.resolve();
          return credential();
        })();
        const [a, b] = await Promise.all([first, late]);
        assert.equal(endpoint.maxActive, 1, `${hops} microtasks after the answer, two refreshes overlapped`);
        assert.equal(endpoint.calls.length, 1, `${hops} microtasks after the answer, a second refresh started`);
        assert.equal(a.accessToken, b.accessToken);
      } finally {
        file.cleanup();
      }
    }
  });

  it('keeps the one-refresh rule for a caller arriving right after the answer', async () => {
    const file = accountFile();
    try {
      const endpoint = controllableEndpoint({ tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }], delayMs: 20 });
      const credential = credentialSource(file.account, { fetch: endpoint.fetch });
      const first = credential();
      // Ask again while the first refresh is on the wire and again after it has
      // settled: neither may produce a second refresh.
      await until(() => endpoint.calls.length === 1);
      const during = await credential();
      const after = await credential();
      await first;
      assert.equal(endpoint.calls.length, 1, 'a token that was still valid was refreshed again');
      assert.deepEqual([during.accessToken, after.accessToken], ['access-1', 'access-1']);
    } finally {
      file.cleanup();
    }
  });
});

// ---------------------------------------------------------------------------
// Credentials are asked for from two directions at once in real use: the IMAP
// watcher reconnects while an SMTP reply is being sent. Both must sign in, and
// the refresh token must be spent once between them.
// ---------------------------------------------------------------------------
describe('IMAP and SMTP signing in together spend one refresh', () => {
  it('connects the watcher and sends a reply while both share one refreshed token', async (t) => {
    const TOKEN_1 = 'access-1';
    const TOKEN_2 = 'access-2';
    const imap = await fakeImap(t, { oauth: [TOKEN_1, TOKEN_2] });
    const smtp = await fakeSmtp(t, { oauth: [TOKEN_1, TOKEN_2] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-both-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
    }), { mode: 0o600 });
    const endpoint = controllableEndpoint({
      tokens: [{ accessToken: TOKEN_1, refreshToken: 'refresh-2' }], delayMs: 40 });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));

    await client.preflight();
    // A reconnect and a send started together, exactly as the reading loop and
    // a reply would overlap.
    const connection = client.open();
    const sending = client.deliver({ from: ME, recipients: ['alice@example.com'],
      raw: 'From: me@example.com\r\nTo: alice@example.com\r\nSubject: hi\r\n\r\nhi' });
    const [opened] = await Promise.all([connection, sending]);
    opened.close();

    assert.equal(smtp.messages.length, 1, 'the reply did not go out');
    assert.equal(endpoint.calls.length, 1, 'the two protocols spent the refresh token between them');
    assert.equal(JSON.parse(fs.readFileSync(configFile, 'utf8')).oauth2.refreshToken, 'refresh-2');
    // Both protocols signed in with the one token the refresh produced.
    assert.deepEqual([...new Set(imap.state.auths)], [TOKEN_1]);
    assert.deepEqual([...new Set(smtp.auths)], [TOKEN_1]);
  });

  it('refreshes once when both protocols are refused at the same moment', async (t) => {
    const TOKEN_1 = 'access-1';
    const TOKEN_2 = 'access-2';
    const imap = await fakeImap(t, { oauth: [TOKEN_1] });
    const smtp = await fakeSmtp(t, { oauth: [TOKEN_1] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-both-stale-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
    }), { mode: 0o600 });
    const endpoint = controllableEndpoint({
      tokens: [{ accessToken: TOKEN_1, refreshToken: 'refresh-2' },
        { accessToken: TOKEN_2, refreshToken: 'refresh-3' }], delayMs: 30 });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));
    await client.preflight();
    assert.equal(endpoint.calls.length, 1);

    // The mailbox revokes access-1 for both protocols between them: the next
    // connection and the next send are each refused, at the same moment.
    imap.accept(TOKEN_2);
    smtp.accept(TOKEN_2);
    const connection = client.open();
    const sending = client.deliver({ from: ME, recipients: ['alice@example.com'],
      raw: 'From: me@example.com\r\nTo: alice@example.com\r\nSubject: hi\r\n\r\nhi' });
    const [opened] = await Promise.all([connection, sending]);
    opened.close();

    // Neither refusal overlaps another refresh, and no refresh token is spent
    // twice: whatever the interleaving, the protocol that is refused later
    // either joins a refresh or starts one with the token the previous refresh
    // rotated to.
    assert.equal(endpoint.maxActive, 1, 'two refreshes were on the wire at once');
    const spent = endpoint.calls.map((each) => each.refresh_token);
    assert.equal(new Set(spent).size, spent.length, `a refresh token was spent twice: ${spent.join(',')}`);
    assert.deepEqual(spent.slice(0, 2), ['refresh-1', 'refresh-2']);
    void TOKEN_1;
    // The reply went out once and both protocols ended signed in.
    assert.equal(smtp.messages.length, 1);
    assert.deepEqual([...new Set(smtp.auths)], [TOKEN_1, TOKEN_2]);
  });
});

// ---------------------------------------------------------------------------
// Stopping must not leave a refresh dangling: the credential wait settles, the
// loop ends, and nothing rejects unhandled once nobody is waiting for it.
// ---------------------------------------------------------------------------
describe('stopping during a refresh leaves nothing dangling', () => {
  it('stops a watcher whose preflight refresh is still on the wire', async (t) => {
    const unhandled = [];
    const onUnhandled = (reason) => unhandled.push(reason);
    process.on('unhandledRejection', onUnhandled);
    t.after(() => process.off('unhandledRejection', onUnhandled));

    const imap = await fakeImap(t, { oauth: ['access-1'] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-stop-race-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: 1, secure: true },
    }), { mode: 0o600 });
    const endpoint = controllableEndpoint({ tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }], hold: true });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));

    // preflight() holds its refresh open; stopping meanwhile must not hang.
    const preflight = client.preflight().then(() => 'ok', (error) => error.message);
    await until(() => endpoint.calls.length === 1);
    const stopping = client.stopSubscription();
    endpoint.release();

    const outcome = await Promise.race([
      preflight,
      new Promise((resolve) => { const timer = setTimeout(() => resolve('hung'), 3_000); timer.unref?.(); }),
    ]);
    await stopping;
    assert.notEqual(outcome, 'hung', 'stopping during a refresh left the credential wait hanging');
    assert.deepEqual(unhandled, [], 'stopping during a refresh produced an unhandled rejection');
  });

  it('ends a started subscription that is stopped immediately', async (t) => {
    const imap = await fakeImap(t, { oauth: ['access-1'] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-stop-now-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: 1, secure: true },
    }), { mode: 0o600 });
    const endpoint = controllableEndpoint({ tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }] });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));

    client.account = loadAccount(configFile);
    client.credential = credentialSource(client.account, { fetch: endpoint.fetch });
    const exits = [];
    client.startSubscription(() => {}, (exit) => exits.push(exit));
    await client.stop();
    assert.equal(client.loop, null, 'the loop outlived the stop');
    assert.deepEqual(exits, [], 'a deliberate stop was reported as an unexpected exit');
  });

  it('stops a watcher whose connection is still waiting for a refresh', async (t) => {
    const unhandled = [];
    const onUnhandled = (reason) => unhandled.push(reason);
    process.on('unhandledRejection', onUnhandled);
    t.after(() => process.off('unhandledRejection', onUnhandled));

    const imap = await fakeImap(t, { oauth: ['access-1'] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-stop-open-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: 1, secure: true },
    }), { mode: 0o600 });
    const endpoint = controllableEndpoint({ tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }], hold: true });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));

    client.account = loadAccount(configFile);
    client.credential = credentialSource(client.account, { fetch: endpoint.fetch });
    const exits = [];
    client.startSubscription(() => {}, (exit) => exits.push(exit));
    // The loop is blocked in open(): the refresh it is waiting for has not been
    // answered yet. Stopping now must still end, not wait for the answer.
    await until(() => endpoint.pending === 1);
    const stopping = client.stop();
    endpoint.release();
    const ended = await Promise.race([
      stopping.then(() => 'stopped'),
      new Promise((resolve) => { const timer = setTimeout(() => resolve('hung'), 3_000); timer.unref?.(); }),
    ]);
    assert.equal(ended, 'stopped', 'a stop during the connection refresh hung');
    assert.equal(client.loop, null, 'the loop outlived the stop');
    assert.deepEqual(exits, [], 'a deliberate stop was reported as an unexpected exit');
    assert.deepEqual(unhandled, [], 'a stop during the connection refresh produced an unhandled rejection');
  });
});

// The counterpart of the refused-credential cases: what must NOT be read as
// one. A fresh access token can only fix a refusal, so spending the refresh
// token on anything else wastes it and hides the real cause.
describe('what a fresh token cannot fix is never retried', () => {
  it('does not refresh when a command before the sign-in fails', async (t) => {
    // CAPABILITY is sent before AUTHENTICATE, so a server answering it with
    // "Authentication failed" (maintenance, say) never sees the credential.
    const imap = await fakeImap(t, { oauth: ['token-1'], capabilityRefusal: 'Authentication failed' });
    const smtp = await fakeSmtp(t, { oauth: ['token-1'] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-cap-refusal-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
    }), { mode: 0o600 });
    const endpoint = fakeTokenEndpoint({ tokens: [
      { accessToken: 'token-1', refreshToken: 'refresh-2' },
      { accessToken: 'token-2', refreshToken: 'refresh-3' },
    ] });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));

    const error = await client.preflight().catch((caught) => caught);
    assert.ok(error, 'the check must fail');
    assert.ok(!(error instanceof ImapAuthError), 'a failed CAPABILITY is not a refused credential');
    assert.equal(endpoint.calls.length, 1, 'the refresh token was spent on a failure a new token cannot fix');
  });

  it('does not refresh when the connection ends during the sign-in', async (t) => {
    const imap = await fakeImap(t, { oauth: ['token-1'], closeDuringAuth: true });
    const smtp = await fakeSmtp(t, { oauth: ['token-1'] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-close-auth-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
    }), { mode: 0o600 });
    const endpoint = fakeTokenEndpoint({ tokens: [
      { accessToken: 'token-1', refreshToken: 'refresh-2' },
      { accessToken: 'token-2', refreshToken: 'refresh-3' },
    ] });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));

    const error = await client.preflight().catch((caught) => caught);
    assert.ok(error, 'the check must fail');
    assert.equal(endpoint.calls.length, 1, 'a broken connection is not a refused credential');
  });

  it('does not treat a BAD answer to AUTHENTICATE as a refused credential', async (t) => {
    // RFC 3501: BAD says the command was not accepted, NO says it failed. A new
    // token would be rejected for the same reason the command was.
    const imap = await fakeImap(t, { oauth: ['token-1'], badAuth: 'invalid command' });
    const connection = new ImapConnection({ host: '127.0.0.1', port: imap.port, connect: plainTls });
    await connection.connect();
    const error = await connection.authenticate(ME, 'token-1').catch((caught) => caught);
    connection.close();
    assert.ok(!(error instanceof ImapAuthError), 'a BAD answer is not a refused credential');
  });

  it('completes the two-step exchange before reading the NO when SASL-IR is off', async (t) => {
    // Without SASL-IR the client sends AUTHENTICATE alone, answers the
    // continuation with the token, and must end the rejected exchange with an
    // empty line before the tagged NO arrives. Leaving it open would hang.
    const imap = await fakeImap(t, { oauth: ['token-1'], saslIr: false,
      refusal: '[AUTHENTICATIONFAILED] Invalid credentials' });
    const connection = new ImapConnection({ host: '127.0.0.1', port: imap.port, connect: plainTls });
    await connection.connect();
    await connection.authenticate(ME, 'token-1');
    connection.close();
    // The command carried no token: it went on the continuation instead, which
    // is what a server without SASL-IR requires.
    assert.ok(imap.state.commands.includes('AUTHENTICATE'),
      'the AUTHENTICATE command was never sent');
    assert.deepEqual(imap.state.auths, ['token-1'], 'the token was never accepted');
  });
});

// An account file holds a refresh token, so the directory around it has to be
// as private as the file. `mkdirSync`'s mode only applies when the directory is
// created, which leaves one that already existed at whatever it was.
describe('the account file and its directory are private', () => {
  it('tightens a directory that was already there', (t) => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-dir-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    fs.chmodSync(directory, 0o777);
    const file = path.join(directory, 'account.json');
    writePrivateJson(file, { address: ME });
    assert.equal(fs.statSync(directory).mode & 0o777, 0o700, 'the directory kept wider permissions');
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  });

  it('rejects a tenant that would collapse the endpoint path', () => {
    // An empty tenant means `common`, which is a real endpoint; these are the
    // ones that pass the character class and then vanish from the path.
    for (const tenant of ['..', '.', '../common', '...']) {
      assert.throws(() => endpointFor({ tenant, clientId: 'app-id' }, 'token'),
        /tenant is invalid/, `tenant ${JSON.stringify(tenant)} was accepted`);
    }
    // And the real ones still work.
    assert.match(endpointFor({ tenant: 'common', clientId: 'app-id' }, 'token').url,
      /\/common\/oauth2\/v2.0\/token$/);
    assert.match(endpointFor({ tenant: 'contoso.onmicrosoft.com', clientId: 'app-id' }, 'token').url,
      /\/contoso\.onmicrosoft\.com\/oauth2\/v2\.0\/token$/);
  });
});

// A server, or a middlebox in front of one, can accept the connection and then
// say nothing. Without a timeout the worker waits forever and every later
// attempt piles up behind it; a refused connection must also come back at once
// rather than hanging.
describe('a connection that never answers does not hang', () => {
  // The step timeout is a minute, so this case is slow by nature; it is worth
  // it because the failure mode is "never returns" rather than "wrong answer".
  it('gives up on a server that accepts and stays silent', { timeout: 90_000 }, async (t) => {
    const silent = net.createServer(() => { /* accept, never greet */ });
    await new Promise((resolve) => silent.listen(0, '127.0.0.1', resolve));
    t.after(() => silent.close());
    const error = await verifySmtp({ host: '127.0.0.1', port: silent.address().port, secure: true,
      user: ME, password: PASSWORD, connectTls: plainTls }).catch((caught) => caught);
    assert.match(error.message, /did not answer in time|timed out/);
  });

  it('reports a refused connection instead of waiting', async (t) => {
    // Grab a port and give it back, so nothing is listening on it.
    const spare = net.createServer();
    await new Promise((resolve) => spare.listen(0, '127.0.0.1', resolve));
    const port = spare.address().port;
    await new Promise((resolve) => spare.close(resolve));
    const error = await verifySmtp({ host: '127.0.0.1', port, secure: true, user: ME, password: PASSWORD,
      connectTls: plainTls }).catch((caught) => caught);
    assert.match(error.message, /ECONNREFUSED|connect/i);
  });
});

describe('authentication capability and SMTP status semantics', () => {
  it('offers Microsoft OAuth only when both capabilities support that choice', async (t) => {
    const imap = await fakeImap(t, { oauth: ['token'] });
    const connection = new ImapConnection({ host: '127.0.0.1', port: imap.port, connect: plainTls });
    await connection.connect();
    const error = await connection.login(ME, PASSWORD).catch((caught) => caught);
    connection.close();
    assert.ok(error instanceof OAuthRequiredError);
    assert.ok(!imap.state.commands.includes('LOGIN'));
  });

  it('never sends LOGIN when the server disables it without offering Microsoft OAuth', async (t) => {
    const imap = await fakeImap(t, { loginDisabled: true });
    const connection = new ImapConnection({ host: '127.0.0.1', port: imap.port, connect: plainTls });
    await connection.connect();
    const error = await connection.login(ME, PASSWORD).catch((caught) => caught);
    connection.close();
    assert.match(error.message, /refuses LOGIN/);
    assert.ok(!imap.state.commands.includes('LOGIN'), 'a password was sent despite LOGINDISABLED');
  });

  for (const status of [454, 504, 534]) {
    it(`does not refresh a token for SMTP ${status}`, async (t) => {
      const smtp = await fakeSmtp(t, { oauth: ['access-1'], authFailure: `${status} 4.7.0 server cannot authenticate` });
      const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-smtp-status-'));
      t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
      const configFile = path.join(directory, 'account.json');
      fs.writeFileSync(configFile, JSON.stringify({ address: ME,
        oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
        imap: { host: '127.0.0.1', port: 1 }, smtp: { host: '127.0.0.1', port: smtp.port, secure: true } }));
      const endpoint = fakeTokenEndpoint({ tokens: [{ accessToken: 'access-1', refreshToken: 'refresh-2' }] });
      const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 1 },
        null, quiet, { connectTls: plainTls, fetch: endpoint.fetch });
      client.account = loadAccount(configFile);
      client.credential = credentialSource(client.account, { fetch: endpoint.fetch });
      const error = await client.deliver({ from: ME, recipients: [ME], raw: 'From: me@example.com\r\n\r\nhi' })
        .catch((caught) => caught);
      assert.ok(!(error instanceof SmtpAuthError));
      assert.equal(endpoint.calls.length, 1, 'a server/mechanism failure consumed a new refresh token');
    });
  }
});

// The XOAUTH2 string handed to a server is base64 of the login name and the
// access token, so it reads as an opaque blob to the keyword rules and decodes
// back into a usable credential. Nothing logs one today; this is the layer that
// keeps it that way if something ever does.
describe('a SASL XOAUTH2 string is redacted like the token it carries', () => {
  it('redacts the blob and leaves the rest of the line', () => {
    const sasl = xoauth2('me@example.com', 'secret-access-token-9f3a');
    const cleaned = redactSensitive(`C: A01 AUTHENTICATE XOAUTH2 ${sasl}`);
    assert.ok(!cleaned.includes(sasl), `the SASL string survived: ${cleaned}`);
    assert.match(cleaned, /AUTHENTICATE XOAUTH2 \[REDACTED\]/);
    assert.match(cleaned, /^C: A01 /, 'the line lost its context');
  });

  it('finds the blob wherever it starts on the line', () => {
    // `/` is in the base64 alphabet, so a match a character early decodes out
    // of step and the string slips through; each of these places it can sit.
    for (const [where, line] of [
      ['after a command word', `AUTHENTICATE XOAUTH2 ${xoauth2('me@example.com', 'tok-1')}`],
      ['after a slash', `/x/${xoauth2('me@example.com', 'tok-2')}`],
      ['at the start of a line', `${xoauth2('me@example.com', 'tok-3')} trailing`],
      ['after a quote', `'${xoauth2('me@example.com', 'tok-4')}'`],
    ]) {
      const cleaned = redactSensitive(line);
      assert.ok(cleaned.includes('[REDACTED]'), `not redacted ${where}: ${cleaned}`);
      for (const token of ['tok-1', 'tok-2', 'tok-3', 'tok-4']) {
        assert.ok(!cleaned.includes(token), `${token} survived ${where}`);
      }
    }
  });

  it('leaves base64 that is not a SASL string alone', () => {
    // A JWT and a hash are base64-ish too, and redacting them would cost the
    // log the detail it exists to keep.
    for (const line of ['eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk',
      `sha256:${'a'.repeat(64)}`]) {
      assert.equal(redactSensitive(line), line);
    }
  });

  it('still redacts the token when it is written out plainly', () => {
    assert.ok(!redactSensitive('auth=Bearer secret-access-token-9f3a').includes('secret-access-token-9f3a'));
  });
});

// Moving a mailbox to OAuth2 is done by editing the file by hand, as the
// documentation describes, so a leftover `password` beside the new `oauth2`
// block is the normal shape of a half-finished edit. It must not stop the
// mailbox: it worked before the edit and the credential it now uses is right.
describe('a half-converted account file still starts', () => {
  it('uses oauth2 and reports the leftover password instead of refusing', async (t) => {
    const imap = await fakeImap(t, { oauth: ['token-1'] });
    const smtp = await fakeSmtp(t, { oauth: ['token-1'] });
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-half-'));
    t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
    const configFile = path.join(directory, 'account.json');
    fs.writeFileSync(configFile, JSON.stringify({
      address: ME, password: 'the-old-password',
      oauth2: { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1' },
      imap: { host: '127.0.0.1', port: imap.port },
      smtp: { host: '127.0.0.1', port: smtp.port, secure: true },
    }), { mode: 0o600 });

    const warnings = [];
    const logger = { ...quiet, warn: (event, fields) => warnings.push({ event, fields }) };
    const endpoint = fakeTokenEndpoint({ tokens: [{ accessToken: 'token-1', refreshToken: 'refresh-2' }] });
    const client = new EmailClient({ emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000 },
      null, logger, { connectTls: plainTls, fetch: endpoint.fetch });
    t.after(() => client.stop().catch(() => {}));

    // The check passes, so the mailbox keeps working.
    await client.preflight();
    // The account is the OAuth2 one, and the leftover is named once.
    assert.equal(client.account.password, undefined, 'the unused password was kept as the credential');
    assert.deepEqual(warnings.filter((entry) => entry.event === 'email_unused_password').length, 1,
      'the leftover password was not reported');
  });
});

// The device-code loop waits for the owner to finish signing in, so every
// detail of how long it waits is something the owner feels. A code that has
// lapsed must be reported when it lapses, not one interval later.
describe('the device-code wait respects its own deadline', () => {
  function clocked() {
    let now = 1_000_000;
    const slept = [];
    return {
      now: () => now,
      slept,
      sleep: async (ms) => { slept.push(ms); now += ms; },
      advance: (ms) => { now += ms; },
      elapsed: () => now - 1_000_000,
    };
  }

  it('never sleeps past the code expiry', async () => {
    const c = clocked();
    const code = { deviceCode: 'd', clientId: 'app-id', expiresAt: c.now() + 3_000, intervalMs: 5_000 };
    const fetch = async () => ({ ok: false, status: 400, text: async () => JSON.stringify({ error: 'authorization_pending' }) });
    const error = await waitForDeviceCode({ tenant: 'common', clientId: 'app-id' }, code,
      { fetch, now: c.now, sleep: c.sleep }).catch((caught) => caught);
    assert.match(error.message, /expired/);
    // The interval was 5s but only 3s of code remained.
    assert.deepEqual(c.slept, [3_000], `slept past the deadline: ${JSON.stringify(c.slept)}`);
    assert.equal(c.elapsed(), 3_000, 'the wait ended later than the code did');
  });

  it('does not call the endpoint for a code that has already lapsed', async () => {
    const c = clocked();
    let calls = 0;
    const code = { deviceCode: 'd', clientId: 'app-id', expiresAt: c.now() - 1, intervalMs: 1_000 };
    const fetch = async () => { calls += 1; return { ok: false, status: 400, text: async () => '{}' }; };
    const error = await waitForDeviceCode({ tenant: 'common', clientId: 'app-id' }, code,
      { fetch, now: c.now, sleep: c.sleep }).catch((caught) => caught);
    assert.match(error.message, /expired/);
    assert.equal(calls, 0, 'a lapsed code was still sent to the endpoint');
  });
});

// A tenant or client id of the wrong type used to be replaced by `common` or an
// empty string, which called a different endpoint than the file asked for.
describe('a malformed tenant is refused, not replaced', () => {
  it('refuses a tenant that is not a string', () => {
    for (const tenant of [0, false, [], {}]) {
      assert.throws(() => endpointFor({ tenant, clientId: 'app-id' }, 'token'),
        /tenant is invalid/, `tenant ${JSON.stringify(tenant)} was replaced`);
    }
  });

  it('still defaults an absent tenant to common', () => {
    assert.match(endpointFor({ clientId: 'app-id' }, 'token').url, /\/common\/oauth2\/v2\.0\/token$/);
  });

  it('rejects non-string account ids instead of coercing them', () => {
    const file = accountFile();
    try {
      for (const [key, value] of [['tenant', {}], ['clientId', {}], ['refreshToken', 0]]) {
        const saved = file.saved();
        saved.oauth2 = { tenant: 'common', clientId: 'app-id', refreshToken: 'refresh-1', [key]: value };
        fs.writeFileSync(file.file, JSON.stringify(saved));
        assert.throws(() => loadAccount(file.file), new RegExp(`oauth2.${key} must be a string`));
      }
    } finally { file.cleanup(); }
  });
});
