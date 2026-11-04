// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import net from 'node:net';
import tls from 'node:tls';
import { xoauth2 } from './oauth2.mjs';

// The SMTP subset that submits one message: implicit TLS (`secure`, by default
// on port 465) or STARTTLS, AUTH XOAUTH2, PLAIN or LOGIN, MAIL/RCPT/DATA. A
// plaintext session is never authenticated.

const STEP_TIMEOUT_MS = 60_000;

class SmtpSession {
  constructor(socket) {
    this.socket = socket;
    this.buffer = '';
    this.lines = [];
    this.waiter = null;
    this.error = null;
    this.attach(socket);
  }

  attach(socket) {
    this.socket = socket;
    socket.setEncoding('utf8');
    socket.on('data', (chunk) => {
      this.buffer += chunk;
      let end;
      while ((end = this.buffer.indexOf('\r\n')) >= 0) {
        this.lines.push(this.buffer.slice(0, end));
        this.buffer = this.buffer.slice(end + 2);
        // `250-…` continues a reply; `250 …` ends it.
        if (/^\d{3}(?: |$)/.test(this.lines.at(-1))) {
          const lines = this.lines;
          this.lines = [];
          this.settle(null, { code: Number(lines.at(-1).slice(0, 3)), lines });
        }
      }
    });
    socket.on('error', (error) => this.settle(error));
    socket.on('close', () => this.settle(new Error('SMTP connection closed')));
  }

  settle(error, reply = null) {
    if (error) this.error ??= error;
    const waiter = this.waiter;
    this.waiter = null;
    if (!waiter) return;
    clearTimeout(waiter.timer);
    if (error) waiter.reject(error);
    else waiter.resolve(reply);
  }

  reply() {
    if (this.error) return Promise.reject(this.error);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.waiter = null;
        reject(new Error('SMTP server did not answer in time'));
        this.socket.destroy();
      }, STEP_TIMEOUT_MS);
      timer.unref?.();
      this.waiter = { resolve, reject, timer };
    });
  }

  // Sends a line and expects one of `codes`. `shown` replaces the line in an
  // error, so credentials never reach a message.
  async send(line, codes, shown = line.split(' ')[0]) {
    const answer = this.reply();
    this.socket.write(`${line}\r\n`);
    return this.expect(await answer, codes, shown);
  }

  expect(reply, codes, step) {
    if (!codes.includes(reply.code)) {
      throw new Error(`SMTP ${step} failed: ${reply.lines.at(-1).slice(0, 200)}`);
    }
    return reply;
  }
}

function connect({ connect: open, secureConnect = false, ...options }) {
  return new Promise((resolve, reject) => {
    const socket = open(options);
    socket.once(secureConnect ? 'secureConnect' : 'connect', () => resolve(socket));
    socket.once('error', reject);
  });
}

async function hello(session) {
  const reply = await session.send('EHLO [127.0.0.1]', [250], 'EHLO');
  return reply.lines.map((line) => line.slice(4).toUpperCase());
}

// A sign-in that failed. Nothing was offered to the server yet, so the caller
// may refresh the credential and try the same message again: unlike a failure
// after DATA, this one cannot have delivered anything.
export class SmtpAuthError extends Error {
  constructor(message) {
    super(message);
    this.name = 'SmtpAuthError';
    // Retrying with a new credential is safe: no recipient was told anything.
    this.retryable = true;
  }
}

// SASL XOAUTH2. A rejected token comes back as the error challenge first, and
// the 535 that says so only arrives once the exchange is ended with an empty
// line; the server's final text is the only useful one to report.
async function authenticateXoauth2(session, user, accessToken) {
  const answer = session.reply();
  session.socket.write(`AUTH XOAUTH2 ${xoauth2(user, accessToken)}\r\n`);
  const first = await answer;
  if (first.code === 235) return;
  if (first.code === 334) {
    const final = session.reply();
    session.socket.write('\r\n');
    const reply = await final;
    throw new SmtpAuthError(`SMTP AUTH XOAUTH2 failed: ${reply.lines.at(-1).slice(0, 200)}`);
  }
  throw new SmtpAuthError(`SMTP AUTH XOAUTH2 failed: ${first.lines.at(-1).slice(0, 200)}`);
}

// Opens an authenticated session; `run(session)` does the work, then QUIT.
export async function withSmtp({ host, port, secure = Number(port) === 465, user, password, accessToken,
  connectTls = tls.connect, connectPlain = net.connect }, run) {
  const implicit = secure;
  const socket = await connect(implicit
    ? { host, port, servername: host, connect: connectTls, secureConnect: true }
    : { host, port, connect: connectPlain });
  let session = new SmtpSession(socket);
  try {
    session.expect(await session.reply(), [220], 'greeting');
    let features = await hello(session);
    if (!implicit) {
      if (!features.includes('STARTTLS')) throw new Error('SMTP server offers no STARTTLS; refusing to send credentials in clear text');
      await session.send('STARTTLS', [220]);
      socket.removeAllListeners('data');
      socket.removeAllListeners('error');
      socket.removeAllListeners('close');
      const secure = await connect({ socket, host, servername: host, connect: connectTls, secureConnect: true });
      session = new SmtpSession(secure);
      features = await hello(session);
    }
    const auth = features.find((feature) => feature.startsWith('AUTH')) ?? '';
    // Everything from here to a successful sign-in is the authentication step:
    // no message has been offered, so any failure in it is retryable.
    try {
      if (accessToken) {
        if (!/\bXOAUTH2\b/.test(auth)) throw new SmtpAuthError('SMTP server offers no AUTH XOAUTH2');
        await authenticateXoauth2(session, user, accessToken);
      } else if (/\bPLAIN\b/.test(auth)) {
        const token = Buffer.from(`\u0000${user}\u0000${password}`, 'utf8').toString('base64');
        await session.send(`AUTH PLAIN ${token}`, [235], 'AUTH PLAIN');
      } else if (/\bLOGIN\b/.test(auth)) {
        await session.send('AUTH LOGIN', [334]);
        await session.send(Buffer.from(user, 'utf8').toString('base64'), [334], 'AUTH LOGIN user');
        await session.send(Buffer.from(password, 'utf8').toString('base64'), [235], 'AUTH LOGIN password');
      } else {
        throw new SmtpAuthError('SMTP server offers neither AUTH PLAIN nor AUTH LOGIN');
      }
    } catch (error) {
      throw error instanceof SmtpAuthError ? error : new SmtpAuthError(error.message);
    }
    const result = await run(session);
    await session.send('QUIT', [221]).catch(() => {});
    return result;
  } finally {
    session.socket.destroy();
  }
}

function envelopeAddress(address) {
  if (!/^[^\s<>@]+@[^\s<>@]+$/.test(address)) throw new Error('SMTP address is invalid');
  return `<${address}>`;
}

// Submits one message. `raw` uses CRLF line ends; lines starting with a dot
// are stuffed.
//
// Once the body has gone out the outcome is uncertain: the server may have
// queued the message even though its answer did not arrive. Such a failure is
// marked so the caller reports it instead of trying again — a retry would send
// a second copy, and SMTP has no idempotency key.
export function sendMail(account, { from, recipients, raw }) {
  return withSmtp(account, async (session) => {
    await session.send(`MAIL FROM:${envelopeAddress(from)}`, [250], 'MAIL FROM');
    for (const recipient of recipients) await session.send(`RCPT TO:${envelopeAddress(recipient)}`, [250, 251], 'RCPT TO');
    await session.send('DATA', [354]);
    const body = raw.replace(/\r?\n/g, '\r\n').replace(/^\./gm, '..');
    const accepted = session.reply();
    session.socket.write(`${body}${body.endsWith('\r\n') ? '' : '\r\n'}.\r\n`);
    try {
      session.expect(await accepted, [250], 'DATA');
    } catch (error) {
      error.uncertainDelivery = true;
      throw error;
    }
    return { accepted: recipients.length };
  });
}

export function verifySmtp(account) {
  return withSmtp(account, async () => ({ ok: true }));
}
