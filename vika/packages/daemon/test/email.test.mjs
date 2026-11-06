// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { MailArchive, chunkOffset, needsChunks, readTarget, mailFolder } from '../src/email/archive.mjs';
import { EmailClient, accountTag, loadAccount } from '../src/email/client.mjs';
import { mailboxIdentity, mailboxWireName } from '../src/email/mailbox.mjs';
import { EmailState } from '../src/email/state.mjs';
import { fetchAttributes, parseResponse } from '../src/email/imap.mjs';
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
// sessions to wake with `deliver(raw)`.
async function fakeImap(t, { mails = [], uidValidity = 1, gmail = true } = {}) {
  const state = { folders: new Map(), mails: mails.map((raw, index) => ({ uid: index + 1, raw })), unavailable: new Set(), disconnectOnExamine: new Set(), validities: new Map(), commands: [], idlers: new Set(), sockets: new Set() };
  state.folders.set('INBOX', state.mails);
  const server = net.createServer((socket) => {
    socket.mailbox = 'INBOX';
    state.sockets.add(socket);
    socket.on('close', () => { state.sockets.delete(socket); state.idlers.delete(socket); });
    socket.on('error', () => {});
    let buffer = '';
    let idleTag = null;
    socket.write('* OK [CAPABILITY IMAP4rev1 IDLE] ready\r\n');
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
        const space = line.indexOf(' ');
        const tag = line.slice(0, space);
        const command = line.slice(space + 1);
        state.commands.push(command.startsWith('LOGIN ') ? 'LOGIN' : command);
        const ok = (text = 'done') => socket.write(`${tag} OK ${text}\r\n`);
        if (command.startsWith('EXAMINE ')) {
          if (state.disconnectOnExamine.has(JSON.parse(command.slice(8)))) { socket.destroy(); return; }
          if (state.unavailable.has(JSON.parse(command.slice(8)))) {
            socket.write(`${tag} NO no such mailbox\r\n`);
            continue;
          }
          socket.mailbox = JSON.parse(command.slice(8));
          if (!state.folders.has(socket.mailbox)) state.folders.set(socket.mailbox, []);
        }
        const selected = state.folders.get(socket.mailbox) ?? [];
        const uidNext = (selected.at(-1)?.uid ?? 0) + 1;
        let match;
        if (command.startsWith('LOGIN ')) {
          if (command.includes(`"${PASSWORD}"`)) ok();
          else socket.write(`${tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n`);
        } else if (command === 'CAPABILITY') {
          socket.write(`* CAPABILITY IMAP4rev1 IDLE${gmail ? ' X-GM-EXT-1' : ''}\r\n`);
          ok();
        } else if (command === 'LIST "" "*"') {
          if (state.listFailure) { socket.write(`${tag} NO temporary LIST failure\r\n`); continue; }
          for (const name of state.folders.keys()) {
            socket.write(`* LIST () "/" ${JSON.stringify(mailboxWireName(name))}\r\n`);
          }
          ok();
        } else if (command.startsWith('EXAMINE ')) {
          socket.write(`* ${selected.length} EXISTS\r\n* OK [UIDVALIDITY ${state.validities.get(socket.mailbox) ?? uidValidity}] ok\r\n* OK [UIDNEXT ${uidNext}] ok\r\n`);
          socket.known = selected.length;
          ok('[READ-ONLY] EXAMINE completed');
        } else if ((match = /^UID SEARCH UID (\d+):\*$/.exec(command))) {
          const from = Number(match[1]);
          const found = selected.filter((item) => item.uid >= from).map((item) => item.uid);
          // RFC 3501: `n:*` always includes the newest message.
          if (found.length === 0 && selected.length) found.push(selected.at(-1).uid);
          socket.write(`* SEARCH${found.map((uid) => ` ${uid}`).join('')}\r\n`);
          ok();
        } else if ((match = /^UID FETCH ([\d,]+) \((.*)\)$/.exec(command))) {
          for (const uid of match[1].split(',').map(Number)) {
            const index = selected.findIndex((item) => item.uid === uid);
            if (index < 0) continue;
            const { raw } = selected[index];
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
          if (selected.length !== socket.known) {
            socket.known = selected.length;
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
    deliver(raw, mailbox = 'INBOX') {
      if (!state.folders.has(mailbox)) state.folders.set(mailbox, []);
      const selected = state.folders.get(mailbox);
      selected.push({ uid: (selected.at(-1)?.uid ?? 0) + 1, raw });
      for (const socket of state.idlers) {
        if (socket.mailbox !== mailbox) continue;
        socket.known = selected.length;
        socket.write(`* ${socket.known} EXISTS\r\n`);
      }
    },
  };
}

// A scripted SMTP submission server; `messages` are what it accepted.
async function fakeSmtp(t) {
  const messages = [];
  const server = net.createServer((socket) => {
    socket.on('error', () => {});
    socket.write('220 fake ESMTP\r\n');
    let buffer = '';
    let data = null;
    let current = { to: [] };
    socket.on('data', (chunk) => {
      buffer += chunk.toString('utf8');
      let end;
      while ((end = buffer.indexOf('\r\n')) >= 0) {
        const line = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        if (data) {
          if (line === '.') {
            messages.push({ ...current, raw: data.join('\r\n') });
            current = { to: [] };
            data = null;
            socket.write('250 queued\r\n');
          } else {
            data.push(line.startsWith('..') ? line.slice(1) : line);
          }
          continue;
        }
        if (line.startsWith('EHLO')) socket.write('250-fake\r\n250 AUTH PLAIN LOGIN\r\n');
        else if (line.startsWith('AUTH PLAIN ')) {
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
  return { messages, port: server.address().port };
}

// TLS is the transport's business; the fakes speak the protocols in the clear.
function plainTls(options) {
  const socket = net.connect(options.port, '127.0.0.1');
  socket.once('connect', () => socket.emit('secureConnect'));
  return socket;
}

async function setup(t, { mails = [], password = PASSWORD, gmail = true, instance = undefined, archive = null, attachmentWaitMs = undefined, pollMs = undefined } = {}) {
  const imap = await fakeImap(t, { mails, gmail });
  const smtp = await fakeSmtp(t);
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'email-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const configFile = path.join(directory, 'account.json');
  fs.writeFileSync(configFile, JSON.stringify({ address: ME, password,
    imap: { host: '127.0.0.1', port: imap.port }, smtp: { host: '127.0.0.1', port: smtp.port, secure: true } }), { mode: 0o600 });
  const config = { emailConfigFile: configFile, dataDir: directory, retentionMs: 86_400_000, ...(instance ? { instance } : {}) };
  const clients = [];
  const start = async () => {
    const client = new EmailClient(config, null, quiet, { connectTls: plainTls, archive, attachmentWaitMs, pollMs, discoveryMs: pollMs, reconnectMs: 10 });
    clients.push(client);
    await client.preflight();
    const events = [];
    const exits = [];
    client.startSubscription((line) => events.push(normalizeEvent(JSON.parse(line))), (exit) => exits.push(exit));
    const names = client.account.imap.mailboxes;
    await until(() => client.account.imap.discovery ? imap.state.commands.filter((command) => command === 'LIST "" "*"').length >= 2 : names.length === 1 ? imap.state.idlers.size === 1
      : names.filter((name) => !imap.state.unavailable.has(name)).every((name) => client.state.cursor(`${TAG}:${name}`)));
    return { client, events, exits };
  };
  t.after(async () => { for (const client of clients) await client.stop().catch(() => {}); });
  return { imap, smtp, start, config };
}

async function until(check, timeoutMs = 3_000) {
  const deadline = Date.now() + timeoutMs;
  while (!check()) {
    if (Date.now() > deadline) throw new Error('condition not met in time');
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

describe('email connector', () => {
  it('isolates a configured folder from INBOX while retaining old mail and cursor identities', async (t) => {
    const bodies = new Map();
    const archive = {
      folder: (id) => mailFolder('viking://~/resources/emails', id),
      storeAttachments: async () => [],
      write: async (uri, body) => { bodies.set(uri, body); return uri; },
      chunk: async () => {},
    };
    const { imap, start, config } = await setup(t, { archive });
    const first = await start();
    imap.deliver(mail());
    await until(() => first.events.length === 1);
    await first.client.stopSubscription();
    const account = JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8'));
    account.imap.mailbox = 'Projects';
    fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
    const second = await start();
    assert.ok(imap.state.commands.includes('EXAMINE "Projects"'));
    imap.deliver(mail(), 'Projects');
    await until(() => second.events.length === 1);
    const a = first.events[0].messageId;
    const b = second.events[0].messageId;
    assert.equal(a, `mail:${TAG}:1:1`);
    assert.equal(b, `mail:${mailboxIdentity(TAG, 'Projects', 1, 1)}`);
    assert.notEqual(first.events[0].eventId, second.events[0].eventId);
    assert.ok(second.client.state.mail(a));
    assert.ok(second.client.state.mail(b));
    assert.equal(second.events[0].data.mailbox, 'Projects');
    assert.deepEqual(second.client.state.cursor(`${TAG}:INBOX`), { uidValidity: 1, lastUid: 1 });
    const root = 'viking://~/resources/emails';
    const oldFolder = mailFolder(root, a);
    const newFolder = mailFolder(root, b);
    assert.equal(oldFolder, `${root}/${TAG}-1-1`);
    assert.notEqual(oldFolder, newFolder);
    assert.match(bodies.get(`${newFolder}/body.md`), /IMAP folder: Projects/);
    for (const folder of [oldFolder, newFolder]) {
      assert.deepEqual(readTarget(root, `${folder}/body.md`), { target: `${folder}/body.md` });
    }
  });

  it('polls folders independently, isolates missing folders, resumes and resets only the changed folder', async (t) => {
    const { imap, config, start } = await setup(t, { pollMs: 20 });
    const account = JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8'));
    account.imap.mailboxes = ['INBOX', 'Missing', 'Projects'];
    fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
    imap.state.unavailable.add('Missing');
    const first = await start();
    imap.deliver(mail(), 'INBOX');
    imap.deliver(mail(), 'Projects');
    await until(() => first.events.length === 2);
    assert.equal(new Set(first.events.map((event) => event.messageId)).size, 2);
    assert.equal(imap.state.idlers.size, 0);
    assert.equal(first.exits.length, 0);
    for (let i = 0; i < imap.state.commands.length - 1; i += 1) {
      if (imap.state.commands[i] === 'EXAMINE "Missing"') assert.doesNotMatch(imap.state.commands[i + 1], /^UID (SEARCH|FETCH)/);
    }
    await first.client.stopSubscription();
    imap.deliver(mail({ id: 'after-stop@example.com' }), 'Projects');
    const second = await start();
    await until(() => second.events.length === 1);
    assert.equal(second.events[0].data.mailbox, 'Projects');
    imap.state.validities.set('Projects', 2);
    await until(() => second.client.state.cursor(`${TAG}:Projects`)?.uidValidity === 2);
    assert.deepEqual(second.client.state.cursor(`${TAG}:INBOX`), { uidValidity: 1, lastUid: 1 });
    imap.deliver(mail({ id: 'after-reset@example.com' }), 'Projects');
    imap.deliver(mail({ id: 'inbox-continues@example.com' }));
    await until(() => second.events.length === 3);
    await second.client.stopSubscription();
    await until(() => imap.state.sockets.size === 0);
  });

  it('reconnects after a lost socket without losing per-folder cursors', async (t) => {
    const { imap, config, start } = await setup(t, { pollMs: 20 });
    const account = JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8'));
    account.imap.mailboxes = ['INBOX', 'Projects'];
    fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
    const running = await start();
    for (const socket of imap.state.sockets) socket.destroy();
    imap.deliver(mail(), 'Projects');
    await until(() => running.events.length === 1);
    assert.equal(running.events[0].data.mailbox, 'Projects');
    assert.equal(running.exits.length, 0);
    await running.client.stopSubscription();
    await until(() => imap.state.sockets.size === 0);
  });

  it('a folder that repeatedly disconnects cannot starve the following folder', async (t) => {
    const { imap, config, start } = await setup(t, { pollMs: 20 });
    const account = JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8'));
    account.imap.mailboxes = ['INBOX', 'Broken', 'Projects'];
    fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
    const running = await start();
    imap.state.disconnectOnExamine.add('Broken');
    imap.deliver(mail(), 'Projects');
    await until(() => running.events.length === 1);
    imap.deliver(mail({ id: 'second@example.com' }), 'Projects');
    await until(() => running.events.length === 2);
    assert.ok(imap.state.commands.filter((command) => command === 'LOGIN').length >= 3);
    assert.equal(running.exits.length, 0);
  });

  it('validates and deduplicates explicit mailbox sets and polling intervals', async (t) => {
    const { config } = await setup(t);
    const account = JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8'));
    account.imap.mailboxes = ['inbox', 'INBOX', 'Projects', 'projects'];
    fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
    assert.deepEqual(loadAccount(config.emailConfigFile).imap.mailboxes, ['INBOX', 'Projects', 'projects']);
    for (const names of [[], 'INBOX', [null], Array(101).fill('INBOX')]) {
      account.imap.mailboxes = names;
      fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
      assert.throws(() => loadAccount(config.emailConfigFile), /imap.mailbox/);
    }
    account.imap.mailboxes = ['INBOX'];
    account.imap.mailbox = 'INBOX';
    fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
    assert.throws(() => loadAccount(config.emailConfigFile), /either/);
    delete account.imap.mailbox;
    account.imap.pollIntervalSeconds = 0;
    fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
    assert.throws(() => loadAccount(config.emailConfigFile), /pollIntervalSeconds/);
  });

  it('discovers new folders, excludes subtrees, and preserves its snapshot when LIST fails', async (t) => {
    const { imap, config, start } = await setup(t, { pollMs: 20 });
    imap.state.folders.set('Work', []);
    imap.state.folders.set('Work/Trash', []);
    imap.state.folders.set('Work/Trash/Old', []);
    imap.state.folders.set('Workshops', []);
    const account = JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8'));
    account.imap.discovery = { roots: ['Work'], exclude: ['Work/Trash'] };
    fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
    const running = await start();
    await until(() => running.client.state.cursor(`${TAG}:Work`));
    assert.equal(running.client.state.cursor(`${TAG}:Workshops`), null);
    assert.equal(running.client.state.cursor(`${TAG}:Work/Trash`), null);
    assert.equal(running.client.state.cursor(`${TAG}:Work/Trash/Old`), null);
    imap.deliver(mail(), 'Work/New'); // first discovery skips historical messages
    await until(() => running.client.state.cursor(`${TAG}:Work/New`)?.lastUid === 1);
    assert.equal(running.events.length, 0);
    imap.deliver(mail({ id: 'new-folder@example.com' }), 'Work/New');
    await until(() => running.events.length === 1);
    imap.state.listFailure = true;
    imap.deliver(mail({ id: 'list-failed@example.com' }), 'Work');
    await until(() => running.events.length === 2);
    assert.equal(running.events[1].data.mailbox, 'Work');
    assert.equal(running.exits.length, 0);
    imap.state.listFailure = false;
    // A removed folder stops being selected but its cursor stays for reappearance.
    const saved = imap.state.folders.get('Work/New');
    imap.state.folders.delete('Work/New');
    imap.state.unavailable.add('Work/New');
    const lists = imap.state.commands.filter((command) => command === 'LIST "" "*"').length;
    await until(() => imap.state.commands.filter((command) => command === 'LIST "" "*"').length > lists);
    assert.equal(running.client.state.cursor(`${TAG}:Work/New`).lastUid, 2);
    imap.state.unavailable.delete('Work/New');
    imap.state.folders.set('Work/New', saved);
    imap.deliver(mail({ id: 'reappeared@example.com' }), 'Work/New');
    await until(() => running.events.length === 3);
    assert.equal(running.events[2].data.mailbox, 'Work/New');
  });

  it('discovers from an empty list and retains a usable snapshot if the folder limit is exceeded', async (t) => {
    const { imap, config, start } = await setup(t, { pollMs: 20 });
    imap.state.folders.clear();
    const account = JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8'));
    account.imap.discovery = {};
    fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
    const running = await start();
    imap.state.folders.set('Later', []);
    await until(() => running.client.state.cursor(`${TAG}:Later`));
    for (let i = 0; i < 101; i += 1) imap.state.folders.set(`Extra${i}`, []);
    const lists = imap.state.commands.filter((command) => command === 'LIST "" "*"').length;
    await until(() => imap.state.commands.filter((command) => command === 'LIST "" "*"').length > lists);
    imap.deliver(mail(), 'Later');
    await until(() => running.events.length === 1);
    assert.equal(running.events[0].data.mailbox, 'Later');
    assert.equal(running.client.state.cursor(`${TAG}:Extra0`), null);
  });

  it('rejects ambiguous or invalid discovery configuration', async (t) => {
    const { config } = await setup(t);
    const account = JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8'));
    for (const discovery of [true, [], null, { roots: 'Work' }, { exclude: [null] }, { refreshIntervalSeconds: 0 }]) {
      account.imap.discovery = discovery;
      fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
      assert.throws(() => loadAccount(config.emailConfigFile), /discovery|mailbox/);
    }
    account.imap.discovery = {};
    account.imap.mailboxes = ['INBOX'];
    fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
    assert.throws(() => loadAccount(config.emailConfigFile), /cannot be combined/);
  });

  it('validates mailbox configuration and encodes rev1 mailbox names', async (t) => {
    const { config } = await setup(t);
    const account = JSON.parse(fs.readFileSync(config.emailConfigFile, 'utf8'));
    assert.equal(loadAccount(config.emailConfigFile).imap.mailbox, 'INBOX');
    for (const [value, expected] of [['inbox', 'INBOX'], [' Projects ', ' Projects '], ['收件箱', '收件箱']]) {
      account.imap.mailbox = value;
      fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
      assert.equal(loadAccount(config.emailConfigFile).imap.mailbox, expected);
    }
    for (const value of ['', null, [], 'x\r\nLOGIN', 'x\u0000']) {
      account.imap.mailbox = value;
      fs.writeFileSync(config.emailConfigFile, JSON.stringify(account));
      assert.throws(() => loadAccount(config.emailConfigFile), /imap.mailbox/);
    }
    assert.equal(mailboxWireName('台北&'), '&U,BTFw-&-');
    assert.equal(mailboxWireName('台北&', true), '台北&');
    assert.notEqual(mailboxIdentity(TAG, 'Projects', 1, 1), mailboxIdentity(TAG, 'projects', 1, 1));
    assert.equal(mailboxIdentity(TAG, 'inbox', 1, 1), `${TAG}:1:1`);
  });

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
  const LINK_FOLDER = `viking://~/resources/emails/${TAG}-1-1`;
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
      `[mail stored: ${LINK_FOLDER}/body.md; the first 500 of its ${TEXT.length} characters are below]`]);
    assert.deepEqual(lines.slice(3, 8).map((line) => line.replace(/ \([^)]*\)/, '')), [
      `[attachment 01 report.pdf: ${LINK_FOLDER}/attachments/01-report.pdf]`,
      `[attachment 02 invite.ics: ${LINK_FOLDER}/attachments/02-invite.ics]`,
      `[attachment 03 forwarded.eml: ${LINK_FOLDER}/attachments/03-forwarded.eml]`,
      '[attachment 04 archive.zip: not stored, OpenViking does not read this type]',
      '[attachment 05 broken.pdf: not stored, OpenViking could not read it]',
    ]);
    assert.equal(lines[3], `[attachment 01 report.pdf (application/pdf, 16 B): ${LINK_FOLDER}/attachments/01-report.pdf]`);
    assert.deepEqual(lines.slice(8), ['', `${TEXT.slice(0, 500)}…`]);
    assert.deepEqual(event.attachments, []);
    assert.equal(event.data.archive.body, `${LINK_FOLDER}/body.md`);
    assert.deepEqual(event.data.archive.attachments.map(({ id, status }) => [id, status]),
      [['01', 'stored'], ['02', 'stored'], ['03', 'stored'], ['04', 'skipped'], ['05', 'failed']]);

    // OV holds the whole mail, the parsed attachments and the text ones as written.
    const body = ov.state.files.get(`${FOLDER}/body.md`);
    assert.match(body, /^# Q3 report\n\n- From: Alice <alice@example\.com>\n- To: me@example\.com\n- Date: 2026-10-13T08:10:00\.000Z\n/);
    assert.ok(body.includes(`- Message: mail:${TAG}:1:1`));
    assert.ok(body.includes(`- 01 report.pdf (application/pdf, 16 B): ${LINK_FOLDER}/attachments/01-report.pdf`));
    assert.ok(body.includes('- 1 inline image in the body, not stored'));
    assert.ok(body.endsWith(`## Body\n\n${TEXT}\n`));
    assert.equal(ov.state.files.get(`${FOLDER}/attachments/01-report.pdf/full/report.md`), 'parsed report.pdf: %PDF-1.4 revenue');
    assert.equal(ov.state.files.get(`${FOLDER}/attachments/02-invite.ics/full/invite.txt`), 'BEGIN:VCALENDAR\r\nSUMMARY:Review\r\nEND:VCALENDAR');
    assert.match(ov.state.files.get(`${FOLDER}/attachments/03-forwarded.eml/full/message.md`),
      /^# Original\n\n- From: Bob <bob@example\.com>\n\nThe original note\.$/);

    // Text first, then vectors only: nothing asks OV for an LLM summary.
    const writes = ov.state.requests.filter(({ route }) => route === 'POST /api/v1/content/write');
    assert.deepEqual(writes.map(({ body: { uri, mode, wait, processing_mode: processing } }) => [uri.slice(LINK_FOLDER.length), mode, wait, processing]), [
      ['/attachments/02-invite.ics/full/invite.txt', 'replace', false, 'vectors_only'],
      ['/attachments/03-forwarded.eml/full/message.md', 'replace', false, 'vectors_only'],
      ['/body.md', 'replace', false, 'vectors_only'],
    ]);
    const added = ov.state.requests.filter(({ route }) => route === 'POST /api/v1/resources').map(({ body: request }) => request);
    assert.deepEqual(added.map(({ to, source_name: name, wait, processing_mode: processing, args }) => [to, name, wait, processing, args]), [
      [`${LINK_FOLDER}/attachments/01-report.pdf/full`, 'report.pdf', false, 'vectors_only', { parse_mode: 'no_split' }],
      [`${LINK_FOLDER}/attachments/05-broken.pdf/full`, 'broken.pdf', false, 'vectors_only', { parse_mode: 'no_split' }],
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
    assert.match(formatInboundLine(events[0]), /\[mail stored: viking:\/\/~\/resources\/emails\//);

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
      [`${LINK_FOLDER}/attachments/01-big.pdf/full`, 'big.pdf', 'vectors_only', { parse_mode: 'no_split' }],
      [`${LINK_FOLDER}/attachments/01-big.pdf/chunks`, 'big.md', 'vectors_only', undefined],
      [`${LINK_FOLDER}/chunks`, 'body.md', 'vectors_only', undefined],
    ]);
    const uploads = [...ov.state.uploads.values()];
    assert.deepEqual(uploads.map(({ name, type }) => [name, type]),
      [['big.pdf', 'application/pdf'], ['big.md', 'text/markdown'], ['body.md', 'text/markdown']]);
    assert.equal(uploads[1].data.toString('utf8'), full);
    assert.equal(uploads[2].data.toString('utf8'), body);
    const chunks = [...ov.state.files.keys()].filter((uri) => uri.startsWith(`${attachment}/chunks/`));
    assert.ok(chunks.length >= 2, 'the attachment is split');
    // The event and the read tool still name the whole attachment.
    assert.equal(events[0].data.archive.attachments[0].uri, `${LINK_FOLDER}/attachments/01-big.pdf`);

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
    assert.ok(event.content.includes(`[attachment 01 report.pdf (application/pdf, 16 B): ${LINK_FOLDER}/attachments/01-report.pdf (OpenViking is still reading it)]`));
    assert.deepEqual(event.content.split('\n').slice(2, 3), [`[mail stored: ${LINK_FOLDER}/body.md; all of its text is below]`]);
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
