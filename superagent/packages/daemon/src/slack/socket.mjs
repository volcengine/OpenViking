// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { setTimeout as delay } from 'node:timers/promises';
import { SlackError } from './api.mjs';

// Socket Mode (https://docs.slack.dev/apis/events-api/using-socket-mode):
// apps.connections.open gives a one-off wss:// address; Slack says hello,
// sends envelopes that each need an acknowledgement, and asks for a new
// connection every few hours. Slack hands each envelope to any one of the
// app's open connections, so a second receiver anywhere silently takes a
// share of the messages: the first connection checks that it is alone.

const HELLO_MS = 15_000;
// Slack refreshes connections itself, but a half-open TCP connection looks
// like a quiet workspace: a fresh connection on a timer bounds that silence.
const ROTATE_MS = 30 * 60_000;
// A worker that just crashed may still be counted for a little while.
const RECHECK_MS = 15_000;
const BACKOFF_MS = [1_000, 2_000, 5_000, 10_000, 30_000];

// Reconnecting cannot fix these.
const fatal = (message) => Object.assign(new Error(message), { fatal: true });

export class SocketModeReceiver {
  constructor({ api, appId, WebSocketImpl = globalThis.WebSocket, logger, maxFrameBytes = 1_000_000,
    rotateMs = ROTATE_MS, helloMs = HELLO_MS, recheckMs = RECHECK_MS, backoffMs = BACKOFF_MS }) {
    Object.assign(this, { api, appId, WebSocketImpl, logger, maxFrameBytes, rotateMs, helloMs, recheckMs, backoffMs });
    this.sockets = new Set();
  }

  // Runs until `signal` aborts; rejects when receiving cannot go on.
  // `handle(payload)` runs before the acknowledgement and must not await:
  // if it throws, the envelope is left unacknowledged for Slack to resend.
  async run(handle, signal, { onReady = () => {} } = {}) {
    try {
      let current = await this.first(handle, signal);
      onReady();
      while (!signal.aborted) {
        const tick = new AbortController();
        const why = await Promise.race([current.ended, current.notice,
          delay(this.rotateMs, 'rotate', { signal: AbortSignal.any([signal, tick.signal]) }).catch(() => 'aborted')]);
        tick.abort();
        if (signal.aborted) break;
        if (why === 'link_disabled') throw fatal('Socket Mode was turned off for this Slack app; turn it on again in the app settings');
        if (why === 'closed') {
          current = await this.reconnect(handle, signal);
        } else {
          // A refresh: the new connection is up before the old one goes.
          const next = await this.reconnect(handle, signal);
          current.close();
          current = next;
        }
      }
    } finally {
      for (const socket of this.sockets) socket.close();
    }
  }

  async first(handle, signal) {
    for (let attempt = 0; ; attempt += 1) {
      const socket = await this.reconnect(handle, signal);
      if (socket.hello.num_connections <= 1) return socket;
      socket.close();
      if (attempt >= 1) {
        throw fatal('Another program is receiving events for this Slack app; each Vika instance needs its own Slack app');
      }
      this.logger.warn('slack_second_receiver', { connections: socket.hello.num_connections });
      await delay(this.recheckMs, undefined, { signal });
    }
  }

  async reconnect(handle, signal) {
    for (let failures = 0; ; failures += 1) {
      signal.throwIfAborted();
      try { return await this.open(handle, signal); } catch (error) {
        if (signal.aborted) throw signal.reason ?? error;
        if (error.fatal) throw error;
        if (error instanceof SlackError && error.auth) {
          throw fatal('Slack authorization failed; run vika login slack <instance>');
        }
        const waitMs = error instanceof SlackError && error.retryAfter !== null
          ? error.retryAfter * 1000 : this.backoffMs[Math.min(failures, this.backoffMs.length - 1)];
        this.logger.warn('slack_connect_failed', { failures: failures + 1, retryInMs: waitMs, error });
        await delay(waitMs, undefined, { signal });
      }
    }
  }

  async open(handle, signal) {
    const url = await this.api.connectionUrl(AbortSignal.any([signal, AbortSignal.timeout(this.helloMs)]));
    const ws = new this.WebSocketImpl(url);
    let ended, notice;
    const socket = {
      hello: null,
      ended: new Promise((resolve) => { ended = resolve; }),
      notice: new Promise((resolve) => { notice = resolve; }),
      close: () => {
        this.sockets.delete(socket);
        try { ws.close(1000); } catch { /* already closed */ }
        ended('closed');
      },
    };
    return await new Promise((resolve, reject) => {
      // Settle before closing: the close event would settle it as a plain disconnect.
      const fail = (error) => { reject(error); socket.close(); };
      const timer = setTimeout(() => fail(new Error('Slack Socket Mode did not say hello')), this.helloMs);
      const abort = () => { clearTimeout(timer); fail(signal.reason ?? new Error('aborted')); };
      signal.addEventListener('abort', abort, { once: true });
      ws.addEventListener('close', () => {
        clearTimeout(timer);
        signal.removeEventListener('abort', abort);
        this.sockets.delete(socket);
        ended('closed');
        reject(new Error('Slack Socket Mode connection closed'));
      });
      ws.addEventListener('error', () => { /* a close event follows */ });
      ws.addEventListener('message', (message) => {
        // Slack sends text frames only.
        const raw = message.data;
        let frame;
        try {
          if (typeof raw !== 'string' || Buffer.byteLength(raw, 'utf8') > this.maxFrameBytes) throw new Error('unexpected frame');
          frame = JSON.parse(raw);
        } catch {
          this.logger.warn('slack_frame_rejected');
          return;
        }
        if (frame?.type === 'hello') {
          if (frame.connection_info?.app_id !== this.appId) {
            clearTimeout(timer);
            fail(fatal('The Slack app-level token belongs to another app'));
            return;
          }
          clearTimeout(timer);
          socket.hello = { num_connections: Number(frame.num_connections) || 1 };
          if (this.sockets.size && socket.hello.num_connections > this.sockets.size + 1) {
            this.logger.warn('slack_second_receiver', { connections: socket.hello.num_connections });
          }
          this.sockets.add(socket);
          resolve(socket);
        } else if (frame?.type === 'disconnect') {
          notice(frame.reason === 'link_disabled' ? 'link_disabled' : 'refresh');
        } else if (typeof frame?.envelope_id === 'string' && frame.envelope_id.length <= 256) {
          try {
            if (frame.type === 'events_api') handle(frame.payload);
          } catch (error) {
            this.logger.warn('slack_envelope_failed', { error });
            return;
          }
          try { ws.send(JSON.stringify({ envelope_id: frame.envelope_id })); } catch { /* resent by Slack */ }
        }
      });
    });
  }
}
