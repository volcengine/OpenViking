// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Transport boundary of the DSH agent. The child process's stdio is the only
// channel, so this module owns JSONL framing in both directions, the JSON-RPC
// request table (correlation ids, per-request timers, pending rejection when the
// connection is lost), write backpressure, and inbound dispatch: a reply settles
// its request, a server request is answered fail-closed, a notification is handed
// to the event handlers. It never decides what a message means for a turn.
import { safeError as safe } from '../logger.mjs';

// Same bound the daemon's own line readers use: a longer line is dropped up to its
// newline so one bad peer cannot grow this process without limit. Exported
// because the bridge's framer applies the identical bound and the two must not
// drift: a frame one side refuses and the other accepts is a request dropped for
// no shared reason.
export const MAX_LINE_BYTES = 16_000_000;

export default {
  _nextRpcId() {
    this.rpcSeq += 1;
    return `dsh-${this.rpcSeq}`;
  },

  // One JSONL frame. The return value keeps the old contract (the frame was
  // buffered), while `false` from the stream is treated as backpressure rather
  // than ignored: delivery then waits for 'drain' or the bounded fallback.
  _write(child, frame) {
    if (!child?.stdin?.writable) throw new Error('dsh stdin is not writable');
    const accepted = child.stdin.write(`${JSON.stringify(frame)}\n`);
    if (accepted === false) this._awaitDrain(child);
    return true;
  },

  _awaitDrain(child) {
    if (this.writeBlocked) return this.writeBlocked;
    const blocked = { child };
    const release = () => {
      if (this.writeBlocked !== blocked) return;
      this.writeBlocked = null;
      clearTimeout(timer);
      child.stdin?.off?.('drain', release);
      this._drain();
    };
    const timer = setTimeout(() => {
      if (this.writeBlocked !== blocked) return;
      this.writeBlocked = null;
      child.stdin?.off?.('drain', release);
      this.logger.warn('dsh_stdin_drain_timeout', { queuedBatches: this.queue.length });
      this._drain();
    }, this.controlTimeoutMs);
    timer.unref?.();
    this.writeBlocked = blocked;
    this.logger.warn('dsh_stdin_backpressure', {
      queuedBatches: this.queue.length, queuedItems: this.state.queued,
    });
    child.stdin?.once?.('drain', release);
    return this.writeBlocked;
  },

  _requestOn(child, method, params, timeoutMs = this.controlTimeoutMs, extra = {}) {
    if (!child?.stdin?.writable) return Promise.reject(new Error('dsh stdin is not writable'));
    const id = this._nextRpcId();
    const entry = {
      id, child, method, timer: null, ...extra,
      resolve: null, reject: null, promise: null,
    };
    const promise = new Promise((resolve, reject) => {
      entry.resolve = resolve;
      entry.reject = reject;
      if (Number.isFinite(timeoutMs) && timeoutMs > 0) {
        entry.timer = setTimeout(() => {
          this.pendingRpc.delete(id);
          // A steer entry stays in `inflight` after a timeout: the batch was
          // written but never confirmed, so its delivery must be requeued or
          // failed instead of vanishing with the removed entry.
          const error = new Error(`dsh rpc timed out: ${method}`);
          error.code = 'DSH_RPC_TIMEOUT';
          reject(error);
        }, timeoutMs);
        entry.timer.unref?.();
      }
      this.pendingRpc.set(id, entry);
      if (extra.kind === 'steer') this.inflight.set(id, entry);
      try {
        const frame = { jsonrpc: '2.0', id, method };
        if (params !== undefined) frame.params = params;
        this._write(child, frame);
      } catch (error) {
        if (entry.timer) clearTimeout(entry.timer);
        this.pendingRpc.delete(id);
        this.inflight.delete(id);
        reject(error);
      }
    });
    entry.promise = promise;
    Object.defineProperty(promise, 'dshEntry', { value: entry });
    return promise;
  },

  _request(method, params, timeoutMs = this.controlTimeoutMs, extra = {}) {
    if (!this.connected || !this.child) return Promise.reject(this._connectionError());
    return this._requestOn(this.child, method, params, timeoutMs, extra);
  },

  _connectionError() {
    const error = new Error('dsh connection lost');
    error.code = 'DSH_CONNECTION_LOST';
    return error;
  },

  _rejectPendingRpc(error, child = null) {
    for (const [id, entry] of this.pendingRpc) {
      if (child && entry.child !== child) continue;
      this.pendingRpc.delete(id);
      this.inflight.delete(id);
      if (entry.timer) clearTimeout(entry.timer);
      try { entry.reject(error); } catch {}
    }
  },

  _sendFrame(frame, child = this.child) {
    try {
      this._write(child, { jsonrpc: '2.0', ...frame });
    } catch (error) {
      this.logger.warn('dsh_write_failed', { error: safe(error) });
    }
  },

  _onStdout(chunk, child) {
    let text = String(chunk);
    if (this.stdoutDiscarding) {
      const index = text.indexOf('\n');
      if (index < 0) return;
      this.stdoutDiscarding = false;
      text = text.slice(index + 1);
      this.stdoutBuffer = '';
    }
    this.stdoutBuffer += text;
    let index;
    while ((index = this.stdoutBuffer.indexOf('\n')) >= 0) {
      const line = this.stdoutBuffer.slice(0, index);
      this.stdoutBuffer = this.stdoutBuffer.slice(index + 1);
      if (Buffer.byteLength(line, 'utf8') > MAX_LINE_BYTES) {
        this.logger.warn('dsh_oversized_line_dropped', { bytes: Buffer.byteLength(line, 'utf8') });
        continue;
      }
      if (!line.trim()) continue;
      try { this._handleLine(line, child); } catch (error) {
        this.logger.warn('dsh_line_error', { error: safe(error) });
      }
    }
    if (Buffer.byteLength(this.stdoutBuffer, 'utf8') > MAX_LINE_BYTES) {
      this.logger.warn('dsh_oversized_line_dropped', { partial: true });
      this.stdoutBuffer = '';
      this.stdoutDiscarding = true;
    }
  },

  _onStderr(chunk) {
    this.stderrBuffer = (this.stderrBuffer + String(chunk)).slice(-4000);
    let index;
    while ((index = this.stderrBuffer.indexOf('\n')) >= 0) {
      const line = this.stderrBuffer.slice(0, index);
      this.stderrBuffer = this.stderrBuffer.slice(index + 1);
      if (line.trim()) this.logger.debug('dsh_stderr', { detail: safe(line.slice(0, 500)) });
    }
  },

  _handleLine(line, child) {
    let message;
    try { message = JSON.parse(line.replace(/\r$/, '')); } catch { return; }
    if (!message || typeof message !== 'object') return;
    if (message.id !== undefined && typeof message.method === 'string') {
      this._handleServerRequest(message);
      return;
    }
    if (message.id !== undefined && message.method === undefined) {
      const pending = this.pendingRpc.get(message.id);
      if (!pending || pending.child !== child) return;
      this.pendingRpc.delete(message.id);
      this.inflight.delete(message.id);
      if (pending.timer) clearTimeout(pending.timer);
      if (message.error) {
        const error = new Error(message.error.message || 'dsh rpc error');
        error.code = message.error.code;
        error.data = message.error.data;
        pending.reject(error);
      } else pending.resolve(message.result);
      return;
    }
    if (typeof message.method === 'string') this._handleNotification(message);
  },

  _handleServerRequest(message) {
    const { id, method, params } = message;
    if (params?.sessionId && this.sessionId && params.sessionId !== this.sessionId) {
      // A descendant approval still needs a fail-closed answer, but its activity
      // is not projected as root-session work.
      if (method === 'approval/request') this._sendFrame({ id, result: { outcome: 'rejected' } });
      else this._sendFrame({ id, error: { code: -32601, message: 'unsupported unattended request' } });
      return;
    }
    this._recordActivity('agent.interaction_declined', {
      summary: method,
      metadata: { runtime: this.runtime, method, toolName: params?.toolName },
    });
    if (method === 'approval/request') {
      this._sendFrame({ id, result: { outcome: 'rejected' } });
      return;
    }
    this._sendFrame({ id, error: { code: -32601, message: 'This unattended daemon does not support this request.' } });
  },
};
