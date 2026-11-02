// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { sanitizeNonArkChildEnv, terminateProcessTree } from '../process-utils.mjs';
import { safeError } from '../logger.mjs';
import { connectorManifest, parseProtocolLine, requestMessage } from './protocol.mjs';
import { mentionTarget } from '../runtime-input.mjs';

const defaultWorkerPath = fileURLToPath(new URL('./worker.mjs', import.meta.url));
// Backoff between restarts of one failed Connector; the last delay repeats.
// A restarted Connector counts as recovered once it has run this long, and its
// backoff starts over. An expired login starts fine and fails seconds later.
const RESTART_DELAYS_MS = [5_000, 15_000, 60_000, 300_000];
const STABLE_AFTER_MS = 30_000;

function unavailable(id) {
  return Object.assign(new Error(`connector ${id} is temporarily unavailable`), { code: 'connector_unavailable' });
}

// The owner as a Connector reported it at check: `id` is how the owner appears
// as an event actor there, `target` the message.send input that reaches them.
function ownerIdentity(value) {
  if (!value || typeof value !== 'object') return null;
  const { id, target } = value;
  if (typeof id !== 'string' || !id || !target || typeof target !== 'object' || Array.isArray(target)) return null;
  return Object.freeze({ id, target: Object.freeze({ ...target }) });
}

// The bot itself as a Connector reported it at check: `id` is how the bot
// appears in an inbound event's mentions there.
function selfIdentity(value) {
  if (!value || typeof value !== 'object' || typeof value.id !== 'string' || !value.id) return null;
  return Object.freeze({ id: value.id });
}

function appendTail(current, chunk, limit = 8_000) {
  const next = current + chunk;
  return next.length <= limit ? next : next.slice(-limit);
}

// The Connector processes an instance runs: one per IM, and one per mailbox
// (an email instance is `email-<name>`). `type` picks the worker's client.
export function connectorInstances(config) {
  return config.connectors.flatMap((type) => (type === 'email'
    ? (config.emailAccounts ?? []).map(({ id, configFile }) => ({ id, type, emailConfigFile: configFile }))
    : [{ id: type, type }]));
}

function childConfig(config, instance) {
  return {
    channel: instance.type,
    instance: instance.id,
    projectDir: config.projectDir,
    dataDir: config.dataDir,
    tempDir: config.tempDir,
    profile: config.profile,
    larkCliBin: config.larkCliBin,
    slackCliBin: config.slackCliBin,
    weixinLongPollTimeoutMs: config.weixinLongPollTimeoutMs,
    // Slack shows a mention of the bot as this name, the one the prompt knows.
    agentName: instance.type === 'slack' ? config.agentName ?? null : null,
    emailConfigFile: instance.emailConfigFile ?? null,
    // The Lark Connector of the instance that stores meeting notes.
    meetingNotes: instance.type === 'lark' && Boolean(config.meetingNotes),
    // A mailbox stores each mail in OV before reporting it; so does the Lark
    // Connector each meeting note.
    openVikingConfigPath: instance.type === 'email' || (instance.type === 'lark' && config.meetingNotes)
      ? config.openVikingConfigPath || null : null,
    retentionMs: config.retentionMs,
    maxEventBytes: config.maxEventBytes,
    sendTimeoutMs: config.sendTimeoutMs,
    dryRun: config.dryRun,
    logLevel: config.logLevel,
  };
}

export class ConnectorProcess {
  constructor(instance, config, registry, logger) {
    this.instance = typeof instance === 'string' ? { id: instance, type: instance } : instance;
    this.id = this.instance.id;
    this.type = this.instance.type;
    this.config = config;
    this.registry = registry;
    this.logger = logger;
    this.child = null;
    this.stdoutBuffer = '';
    this.stderrTail = '';
    this.stderrBuffer = '';
    this.pending = new Map();
    this.manifest = null;
    this.profile = this.type === 'lark' ? config.profile : null;
    // Kept across restarts: the last owner and bot identity this Connector reported.
    this.owner = null;
    this.self = null;
    this.checked = false;
    this.started = false;
    this.stopping = false;
    this.onEvent = null;
    this.onUnexpectedExit = null;
    this.reportedError = null;
    // Supervision state, kept by ConnectorManager.
    this.failures = 0;
    this.lastError = null;
    this.retryAt = null;
    this.retryTimer = null;
    this.stableTimer = null;
    this.runningSince = null;
  }

  spawn() {
    if (this.child) return;
    if (this.stopping) throw new Error(`connector ${this.id} is stopping`);
    this.stdoutBuffer = '';
    this.stderrTail = '';
    this.stderrBuffer = '';
    this.reportedError = null;
    const workerPath = this.config.connectorWorkerPath || defaultWorkerPath;
    const env = sanitizeNonArkChildEnv(process.env);
    env.PERSONAL_AGENT_CONNECTOR_CONFIG = JSON.stringify(childConfig(this.config, this.instance));
    const child = this.registry.register(spawn(process.execPath, [workerPath, this.type], {
      cwd: this.config.projectDir,
      env,
      shell: false,
      detached: process.platform !== 'win32',
      stdio: ['pipe', 'pipe', 'pipe'],
    }));
    this.child = child;
    child.stdin?.on('error', () => {});
    child.stdout?.setEncoding('utf8');
    child.stderr?.setEncoding('utf8');
    child.stdout?.on('data', (chunk) => this.handleStdout(chunk));
    child.stderr?.on('data', (chunk) => {
      this.stderrTail = appendTail(this.stderrTail, chunk);
      this.forwardLogs(chunk);
    });
    child.once('error', (error) => this.failPending(error));
    child.once('exit', (code, signal) => {
      // Already discarded: a failed attempt that is not reported as an exit.
      if (this.child !== child) return;
      const wasStopping = this.stopping;
      const onUnexpectedExit = this.onUnexpectedExit;
      this.child = null;
      this.checked = false;
      this.started = false;
      this.onUnexpectedExit = null;
      // The worker's own failure report says why (an expired login, say);
      // the stderr tail is the fallback for a crash.
      const detail = this.reportedError || this.stderrTail.trim().slice(-2_000);
      const error = new Error(`connector ${this.id} exited ${code ?? signal ?? 'unknown'}${detail ? `: ${detail}` : ''}`);
      this.failPending(error);
      if (!wasStopping) {
        terminateProcessTree(child, 'SIGKILL');
        onUnexpectedExit?.({ code, signal, stderr: safeError(error) });
      }
    });
  }

  // The worker's own log records (JSON lines on stderr) go on in the daemon
  // log, through its redaction; anything else is only kept for a crash report.
  forwardLogs(chunk) {
    this.stderrBuffer += chunk;
    let newline;
    while ((newline = this.stderrBuffer.indexOf('\n')) >= 0) {
      const line = this.stderrBuffer.slice(0, newline);
      this.stderrBuffer = this.stderrBuffer.slice(newline + 1);
      let record = null;
      try { record = JSON.parse(line); } catch {}
      const { time: _time, level, message, ...fields } = record && typeof record === 'object' ? record : {};
      if (typeof message === 'string' && ['debug', 'info', 'warn', 'error'].includes(level)) {
        this.logger[level]?.(message, { ...fields, connector: this.id });
      }
    }
    if (this.stderrBuffer.length > 64_000) this.stderrBuffer = '';
  }

  handleStdout(chunk) {
    this.stdoutBuffer += chunk;
    while (true) {
      const newline = this.stdoutBuffer.indexOf('\n');
      if (newline < 0) break;
      const line = this.stdoutBuffer.slice(0, newline);
      this.stdoutBuffer = this.stdoutBuffer.slice(newline + 1);
      if (line.trim()) this.handleLine(line);
    }
    if (Buffer.byteLength(this.stdoutBuffer, 'utf8') > this.config.maxEventBytes) {
      this.logger.error('connector_protocol_line_oversize', { connector: this.id });
      terminateProcessTree(this.child, 'SIGKILL');
    }
  }

  handleLine(line) {
    let message;
    try { message = parseProtocolLine(line); } catch (error) {
      this.logger.warn('connector_protocol_invalid', { connector: this.id, error: safeError(error) });
      return;
    }
    if (message.type === 'RESULT') {
      const pending = this.pending.get(message.request_id);
      if (!pending) return;
      this.pending.delete(message.request_id);
      clearTimeout(pending.timer);
      if (message.ok) pending.resolve(message.result);
      else pending.reject(new Error(message.error || `connector ${this.id} request failed`));
      return;
    }
    if (message.type === 'EVENT') {
      if (message.connector !== this.type || message.instance !== this.id) {
        this.logger.warn('connector_event_invalid', { connector: this.id });
        return;
      }
      this.onEvent?.(message);
      return;
    }
    if (message.type === 'STATUS' && message.status === 'failed' && !this.stopping) {
      if (typeof message.detail?.error === 'string') this.reportedError = message.detail.error.slice(-2_000);
      this.logger.error('connector_reported_failure', {
        connector: this.id,
        error: message.detail?.error,
      });
    }
  }

  failPending(error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  // Only preflight starts a worker. Any other request to a worker that is gone
  // fails here instead of starting one that never passed check.
  request(operation, payload = {}, timeoutMs = 30_000) {
    const message = requestMessage(operation, payload);
    return new Promise((resolve, reject) => {
      if (!this.child) {
        reject(unavailable(this.id));
        return;
      }
      const timer = setTimeout(() => {
        this.pending.delete(message.id);
        reject(new Error(`connector ${this.id} ${operation} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      timer.unref?.();
      this.pending.set(message.id, { resolve, reject, timer });
      try {
        this.child.stdin.write(`${JSON.stringify(message)}\n`);
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(message.id);
        reject(error);
      }
    });
  }

  async preflight() {
    this.spawn();
    const spec = await this.request('spec', {}, 10_000);
    this.manifest = connectorManifest(spec?.manifest);
    if (this.manifest.name !== this.type) throw new Error(`connector spec name mismatch: ${this.id}`);
    const checked = await this.request('check', {}, 30_000);
    this.checked = true;
    this.profile = checked?.profile || this.profile;
    this.owner = ownerIdentity(checked?.owner) ?? this.owner;
    this.self = selfIdentity(checked?.self) ?? this.self;
    return {
      profile: this.profile,
      version: checked?.version || this.manifest.version,
      manifest: this.manifest,
    };
  }

  async startSubscription(onEvent, onUnexpectedExit) {
    if (!this.checked) throw new Error(`connector ${this.id} has not passed preflight`);
    this.onEvent = onEvent;
    this.onUnexpectedExit = onUnexpectedExit;
    await this.request('start', {}, 10_000);
    this.started = true;
  }

  get subscriptionRunning() {
    return Boolean(this.started && this.child && this.child.exitCode === null);
  }

  get available() {
    return Boolean(this.child && this.checked && !this.stopping);
  }

  invoke(name, input, timeoutMs = this.config.sendTimeoutMs ?? 30_000) {
    if (!this.available) return Promise.reject(unavailable(this.id));
    if (this.type === 'lark' && input.content?.card_images?.length) {
      // One CLI timeout per unique image, plus the send and optional thread
      // lookup. Core must not remove snapshots while this worker uses them.
      const count = new Set(input.content.card_images.map((image) => image.digest)).size;
      timeoutMs = (Math.min(count, 32) + 2) * timeoutMs + 1000;
    }
    return this.request('action', { name, input }, timeoutMs);
  }

  // Kills the worker without reporting an exit, so a failed start or restart
  // attempt can begin again from a fresh process.
  discard() {
    const child = this.child;
    this.child = null;
    this.checked = false;
    this.started = false;
    this.onUnexpectedExit = null;
    if (!child) return;
    this.failPending(new Error(`connector ${this.id} worker was discarded`));
    terminateProcessTree(child, 'SIGKILL');
  }

  async pauseSubscription() {
    if (!this.started || !this.child) return;
    await this.request('pause', {}, 5_000);
    this.started = false;
  }

  async stop() {
    if (this.stopping) return;
    this.stopping = true;
    const child = this.child;
    if (!child) return;
    try { await this.request('stop', {}, 5_000); } catch {}
    if (child.exitCode === null) {
      terminateProcessTree(child, 'SIGTERM');
      await Promise.race([
        new Promise((resolve) => child.once('exit', resolve)),
        new Promise((resolve) => setTimeout(resolve, 2_000)),
      ]);
    }
    terminateProcessTree(child, 'SIGKILL');
  }
}

// Supervises the Connector workers. Each one fails and recovers on its own: a
// worker that exits, or fails check or start, is restarted with backoff while
// the others keep running. Startup fails only if no Connector comes up.
export class ConnectorManager {
  constructor(config, registry, logger) {
    this.config = config;
    this.logger = logger;
    this.connectors = new Map(connectorInstances(config).map((instance) => [
      instance.id,
      new ConnectorProcess(instance, config, registry, logger.child({ connector: instance.id })),
    ]));
    this.profile = config.profile || null;
    this.restartDelaysMs = config.connectorRestartDelaysMs ?? RESTART_DELAYS_MS;
    this.stableAfterMs = config.connectorStableAfterMs ?? STABLE_AFTER_MS;
    this.onEvent = null;
    this.onStatus = null;
    // Subscriptions were started and not yet paused: inbound may arrive.
    this.subscribed = false;
    this.closed = false;
  }

  connector(id) {
    const connector = this.connectors.get(id);
    if (!connector) throw new Error(`connector is not configured: ${id}`);
    return connector;
  }

  // A Connector that fails check is left for startSubscription to retry.
  async preflight() {
    const entries = await Promise.all([...this.connectors.values()].map(async (connector) => {
      try {
        return [connector.id, await connector.preflight()];
      } catch (error) {
        connector.discard();
        connector.lastError = safeError(error);
        return [connector.id, null];
      }
    }));
    const passed = entries.filter(([, info]) => info);
    if (passed.length === 0) throw new Error(this.failureSummary());
    const checked = Object.fromEntries(passed);
    this.profile = checked.lark?.profile || this.profile || checked.weixin?.profile;
    return {
      profile: this.profile,
      version: passed.map(([id, info]) => `${id}:${info.version}`).join(', '),
      connectors: Object.fromEntries(passed.map(([id, info]) => [id, info.manifest])),
      failed: Object.fromEntries(entries.filter(([, info]) => !info).map(([id]) => [id, this.connector(id).lastError])),
    };
  }

  // onStatus({ status: 'failed' | 'recovered', connector, ... }) reports each
  // Connector going down (with the retry delay) and coming back.
  async startSubscription(onEvent, onStatus) {
    this.onEvent = onEvent;
    this.onStatus = onStatus;
    this.subscribed = true;
    await Promise.all([...this.connectors.values()].filter((connector) => connector.checked).map(async (connector) => {
      try { await this.subscribe(connector); } catch (error) {
        connector.discard();
        connector.lastError = safeError(error);
      }
    }));
    if (!this.anySubscriptionRunning) throw new Error(this.failureSummary());
    for (const connector of this.connectors.values()) {
      if (!connector.subscriptionRunning) this.fail(connector, connector.lastError);
    }
  }

  async subscribe(connector) {
    await connector.startSubscription(this.onEvent, (exit) => this.exited(connector, exit));
    connector.runningSince = Date.now();
    if (connector.failures > 0) {
      connector.stableTimer = setTimeout(() => this.recovered(connector), this.stableAfterMs);
      connector.stableTimer.unref?.();
    }
  }

  recovered(connector) {
    connector.stableTimer = null;
    if (this.closed || !connector.subscriptionRunning) return;
    const failures = connector.failures;
    connector.failures = 0;
    connector.lastError = null;
    this.onStatus?.({ status: 'recovered', connector: connector.id, failures });
  }

  exited(connector, exit) {
    if (connector.runningSince === null) return;
    clearTimeout(connector.stableTimer);
    connector.stableTimer = null;
    connector.runningSince = null;
    this.fail(connector, exit.stderr, { code: exit.code, signal: exit.signal });
  }

  fail(connector, error, exit = {}) {
    if (this.closed) return;
    const retryInMs = this.restartDelaysMs[Math.min(connector.failures, this.restartDelaysMs.length - 1)];
    connector.failures += 1;
    connector.lastError = error || `connector ${connector.id} failed`;
    connector.retryAt = Date.now() + retryInMs;
    clearTimeout(connector.retryTimer);
    connector.retryTimer = setTimeout(() => void this.restart(connector), retryInMs);
    connector.retryTimer.unref?.();
    this.onStatus?.({ status: 'failed', connector: connector.id, error: connector.lastError,
      failures: connector.failures, retryInMs, code: exit.code ?? null, signal: exit.signal ?? null });
  }

  // A fresh worker through the whole check and start again. A Weixin login
  // renewed by the QR command is picked up here, without restarting the daemon.
  async restart(connector) {
    connector.retryTimer = null;
    connector.retryAt = null;
    if (this.closed) return;
    try {
      connector.discard();
      await connector.preflight();
      if (this.closed) throw new Error('connectors are stopping');
      await this.subscribe(connector);
    } catch (error) {
      connector.discard();
      this.fail(connector, safeError(error));
      return;
    }
    if (this.closed) await connector.pauseSubscription().catch(() => {});
  }

  failureSummary() {
    return [...this.connectors.values()]
      .map((connector) => `${connector.id}: ${connector.lastError ?? 'not started'}`).join('; ');
  }

  available(id) {
    return Boolean(this.connectors.get(id)?.available);
  }

  owner(id) {
    return this.connectors.get(id)?.owner ?? null;
  }

  // Whether an event's sender is the owner its Connector reported at check.
  isOwner(event) {
    const owner = this.owner(event?.connectorInstance ?? event?.source);
    return Boolean(owner && event.senderId && owner.id === event.senderId);
  }

  // Whom an event's @mentions call, against the bot identity its Connector
  // reported at check (see mentionTarget).
  mentionTarget(event) {
    const self = this.connectors.get(event?.connectorInstance ?? event?.source)?.self;
    return mentionTarget(event?.mentions, self?.id);
  }

  get subscriptionRunning() {
    return this.connectors.size > 0
      && [...this.connectors.values()].every((connector) => connector.subscriptionRunning);
  }

  get anySubscriptionRunning() {
    return [...this.connectors.values()].some((connector) => connector.subscriptionRunning);
  }

  state(connector) {
    if (connector.subscriptionRunning) return 'running';
    if (this.closed) return 'stopped';
    return this.subscribed ? 'restarting' : 'starting';
  }

  snapshot() {
    return Object.fromEntries([...this.connectors.entries()].map(([id, connector]) => {
      const state = this.state(connector);
      return [id, {
        state,
        running: state === 'running',
        capabilities: connector.manifest?.capabilities ?? [],
        version: connector.manifest?.version ?? null,
        ...(state === 'restarting' ? {
          failures: connector.failures,
          error: connector.lastError,
          retryAt: connector.retryAt ? new Date(connector.retryAt).toISOString() : null,
        } : {}),
      }];
    }));
  }

  invoke(connector, action, input, timeoutMs) {
    return this.connector(connector).invoke(action, input, timeoutMs);
  }

  // Manifests come from each Connector's spec response during preflight.
  manifests() {
    return [...this.connectors.values()].filter((connector) => connector.manifest)
      .map((connector) => [connector.id, connector.manifest]);
  }

  listChatMembers(chatId) {
    if (!this.connectors.has('lark')) return Promise.resolve([]);
    return this.invoke('lark', 'chat.members.list', { chat_id: chatId }, 15_000);
  }

  getChatName(chatId) {
    if (!this.connectors.has('lark')) return Promise.resolve(null);
    return this.invoke('lark', 'chat.get', { chat_id: chatId }, 15_000);
  }

  updateCard(messageId, card) {
    return this.invoke('lark', 'card.update', { message_id: messageId, card });
  }

  joinMeeting(input) { return this.invoke('lark', 'meeting.join', input); }
  leaveMeeting(meetingId) {
    return this.invoke('lark', 'meeting.leave', { meeting_id: meetingId });
  }
  listMeetingEvents(input) { return this.invoke('lark', 'meeting.events.list', input); }

  // No restart is scheduled or completed once shutdown begins.
  close() {
    this.closed = true;
    this.subscribed = false;
    for (const connector of this.connectors.values()) {
      clearTimeout(connector.retryTimer);
      clearTimeout(connector.stableTimer);
      connector.retryTimer = null;
      connector.stableTimer = null;
      connector.retryAt = null;
    }
  }

  async pauseSubscriptions() {
    this.close();
    const results = await Promise.allSettled(
      [...this.connectors.values()].map((connector) => connector.pauseSubscription()),
    );
    const failed = results.find((result) => result.status === 'rejected');
    if (failed) throw failed.reason;
  }

  async stop() {
    this.close();
    await Promise.allSettled([...this.connectors.values()].map((connector) => connector.stop()));
  }
}
