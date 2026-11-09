// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// OneBot v11 over a forward WebSocket (https://github.com/botuniverse/onebot-11):
// this process is the client of the implementation's WebSocket server
// (NapCat, LLOneBot, Lagrange.OneBot). One connection carries the events and
// the action calls; each call carries an `echo` that its response repeats.
export const ONEBOT_VERSION = 'onebot-v11';

// NapCat accepts frames up to 50 MB; anything larger is not a OneBot frame.
const FRAME_BYTES = 48 * 1024 * 1024;
// Every implementation greets a new event connection with a lifecycle
// `connect` event, and rejects a wrong token right after opening it.
const HELLO_MS = 15_000;
const BACKOFF_MS = [1_000, 2_000, 5_000, 10_000, 30_000];
const ACTION = /^[a-z][a-z0-9_.]{0,63}$/;
const TOKEN = /^[\x21-\x7e]{1,512}$/;
const OPEN = 1;
// NapCat and LLOneBot answer a wrong token with retcode 1403; LLOneBot also
// closes with 1008. Lagrange refuses the handshake with HTTP 403, which the
// WebSocket API cannot tell apart from an unreachable server.
const AUTH_RETCODE = 1403;
const AUTH_CLOSE = 1008;
const quiet = { debug() {}, info() {}, warn() {}, error() {} };

export class OneBotError extends Error {
  constructor(message, { retcode = null, auth = false } = {}) {
    super(message);
    this.retcode = Number.isSafeInteger(retcode) ? retcode : null;
    this.auth = auth;
    // NapCat and LLOneBot answer an unknown action with 1404, Lagrange with 404.
    this.unsupported = this.retcode === 1404 || this.retcode === 404;
  }
}

// The token travels in the Authorization header, never in the address, so
// the address can be shown and logged.
export function validUrl(value) {
  if (typeof value !== 'string' || value.length > 2048) return false;
  try {
    const url = new URL(value);
    return (url.protocol === 'ws:' || url.protocol === 'wss:') && !url.username && !url.password && !url.hash
      && !url.searchParams.has('access_token');
  } catch { return false; }
}

// NapCat runs without a token when none is configured.
export function validToken(value) {
  return value === '' || (typeof value === 'string' && TOKEN.test(value));
}

// One line of the implementation's own explanation, without addresses: file
// and media errors may quote download URLs that carry access keys.
function detail(response, token) {
  const text = [response?.wording, response?.message].find((value) => typeof value === 'string' && value.trim());
  if (!text) return '';
  let line = text.replace(/[\u0000-\u001f\u007f\u2028\u2029]+/g, ' ').replace(/\b[a-z][a-z0-9+.-]*:\/\/\S+/gi, '[url]');
  if (token) line = line.replaceAll(token, '[token]');
  return line.trim().slice(0, 200);
}

export class OneBotSocket {
  constructor({ url, token, WebSocketImpl = globalThis.WebSocket, logger = quiet, callTimeoutMs = 20_000,
    now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout, backoffMs = BACKOFF_MS }) {
    if (!validUrl(url)) throw new Error('Invalid OneBot WebSocket address');
    if (!validToken(token)) throw new Error('Invalid OneBot access token');
    Object.assign(this, { url, token, WebSocketImpl, logger, callTimeoutMs, now, setTimer, clearTimer, backoffMs });
    this.ws = null;
    this.calls = new Map();
    this.sequence = 0;
    this.running = false;
  }

  get connected() { return this.ws !== null; }

  // Runs until `signal` aborts, reconnecting with backoff; rejects on an
  // authentication failure, which reconnecting cannot fix. Without
  // `reconnect` the first connection that fails or ends rejects instead.
  async run(onEvent, signal, { onReady = () => {}, reconnect = true } = {}) {
    if (this.running) throw new Error('OneBot socket is already running');
    this.running = true;
    try {
      for (let failures = 0; !signal.aborted;) {
        const outcome = await this.session(onEvent, signal, onReady);
        if (signal.aborted) break;
        if (outcome.auth) {
          throw new OneBotError('OneBot rejected the access token; run vika login onebot <instance>', { retcode: AUTH_RETCODE, auth: true });
        }
        if (!reconnect) {
          throw new OneBotError(outcome.ready ? 'OneBot connection closed'
            : 'Could not connect to the OneBot WebSocket server; check its address, that it is running and the access token');
        }
        failures = outcome.ready ? 0 : failures + 1;
        const waitMs = this.backoffMs[Math.min(Math.max(failures - 1, 0), this.backoffMs.length - 1)];
        this.logger.warn(outcome.ready ? 'onebot_disconnected' : 'onebot_connect_failed',
          { failures, reason: outcome.reason, retryInMs: waitMs });
        await this.wait(waitMs, signal);
      }
    } finally { this.running = false; }
  }

  wait(ms, signal) {
    return new Promise((resolve) => {
      const done = () => { this.clearTimer(timer); signal.removeEventListener('abort', done); resolve(); };
      const timer = this.setTimer(done, ms);
      signal.addEventListener('abort', done, { once: true });
    });
  }

  // One connection, from opening to its end: resolves with what ended it.
  // A connection is ready, and takes calls, once the server has spoken.
  session(onEvent, signal, onReady) {
    return new Promise((resolve) => {
      let ws;
      try {
        ws = this.token ? new this.WebSocketImpl(this.url, { headers: { Authorization: `Bearer ${this.token}` } })
          : new this.WebSocketImpl(this.url);
      } catch {
        resolve({ ready: false, auth: false, reason: 'invalid' });
        return;
      }
      let ready = false;
      let ended = false;
      let watchdog = null;
      let silenceMs = 0;
      let lastFrame = this.now();
      const end = (reason, auth = false) => {
        if (ended) return;
        ended = true;
        signal.removeEventListener('abort', aborted);
        this.clearTimer(hello);
        if (watchdog) this.clearTimer(watchdog);
        if (this.ws === ws) this.ws = null;
        for (const [echo, pending] of this.calls) {
          this.calls.delete(echo);
          pending.fail(new OneBotError(`OneBot connection closed before ${pending.action} was answered; the outcome is uncertain`));
        }
        try { ws.close(1000); } catch { /* already closing */ }
        resolve({ ready, auth, reason });
      };
      const aborted = () => end('aborted');
      const hello = this.setTimer(() => end('hello'), HELLO_MS);
      // A quiet connection is normal; a missed heartbeat is not.
      const check = () => {
        const silent = this.now() - lastFrame;
        if (silent >= silenceMs) {
          this.logger.warn('onebot_heartbeat_lost', { silentMs: silent });
          end('heartbeat');
        } else watchdog = this.setTimer(check, silenceMs - silent);
      };
      signal.addEventListener('abort', aborted, { once: true });
      ws.addEventListener('close', (event) => end('closed', event.code === AUTH_CLOSE));
      // Node 22 reports a failed handshake with an error event and no close.
      ws.addEventListener('error', () => end('closed'));
      ws.addEventListener('message', (message) => {
        if (ended) return;
        const raw = message.data;
        let frame;
        try {
          if (typeof raw !== 'string' || Buffer.byteLength(raw, 'utf8') > FRAME_BYTES) throw new Error('unexpected frame');
          frame = JSON.parse(raw);
          if (!frame || typeof frame !== 'object' || Array.isArray(frame)) throw new Error('unexpected frame');
        } catch {
          this.logger.warn('onebot_frame_rejected');
          return;
        }
        if (frame.retcode === AUTH_RETCODE) {
          end('closed', true);
          return;
        }
        lastFrame = this.now();
        if (!ready) {
          ready = true;
          this.clearTimer(hello);
          this.ws = ws;
          try { onReady(); } catch (error) { this.logger.warn('onebot_ready_failed', { error }); }
        }
        if (frame.post_type === 'meta_event' && frame.meta_event_type === 'heartbeat'
          && Number.isSafeInteger(frame.interval) && frame.interval > 0 && frame.interval <= 3_600_000) {
          silenceMs = 2 * frame.interval + 10_000;
          watchdog ??= this.setTimer(check, silenceMs);
        }
        if (typeof frame.post_type === 'string') {
          try { onEvent(frame); } catch (error) { this.logger.warn('onebot_event_failed', { error }); }
        } else if (typeof frame.echo === 'string') {
          this.calls.get(frame.echo)?.answer(frame);
        }
      });
    });
  }

  // Resolves the response's `data`. The error says whether the request was
  // written: one that was written but never answered may still have
  // happened, so it says the outcome is uncertain. The Core cannot tell the
  // two apart and records every failed send as uncertain either way.
  call(action, params = {}, signal) {
    if (!ACTION.test(action)) return Promise.reject(new Error('Unsupported OneBot action'));
    const ws = this.ws;
    if (!ws || ws.readyState !== OPEN) return Promise.reject(new OneBotError(`OneBot is not connected; ${action} was not sent`));
    if (signal?.aborted) return Promise.reject(new OneBotError(`OneBot ${action} was cancelled before it was sent`));
    const echo = `vika-${++this.sequence}`;
    return new Promise((resolve, reject) => {
      try { ws.send(JSON.stringify({ action, params, echo })); } catch {
        reject(new OneBotError(`OneBot ${action} could not be sent`));
        return;
      }
      const settle = () => {
        this.calls.delete(echo);
        this.clearTimer(timer);
        signal?.removeEventListener('abort', cancelled);
      };
      const fail = (error) => { settle(); reject(error); };
      const cancelled = () => fail(new OneBotError(`OneBot ${action} was cancelled before it was answered; the outcome is uncertain`));
      const timer = this.setTimer(() => fail(new OneBotError(`OneBot ${action} got no answer in time; the outcome is uncertain`)),
        this.callTimeoutMs);
      signal?.addEventListener('abort', cancelled, { once: true });
      this.calls.set(echo, {
        action,
        fail,
        answer: (response) => {
          settle();
          if (response.retcode === 0 && response.status !== 'failed') {
            resolve(response.data ?? null);
            return;
          }
          const retcode = Number.isSafeInteger(response.retcode) ? response.retcode : null;
          const why = detail(response, this.token);
          const error = new OneBotError(`OneBot ${action} failed (retcode ${retcode ?? 'missing'})${why ? `: ${why}` : ''}`, { retcode });
          if (error.unsupported) error.message = `OneBot ${action} is not supported by this OneBot implementation`;
          reject(error);
        },
      });
    });
  }
}
