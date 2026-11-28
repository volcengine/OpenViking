// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { spawn } from '../platform/index.mjs';
import { sanitizeNonArkChildEnv, terminateProcessTree } from '../process-utils.mjs';
import { safeError } from '../logger.mjs';

export const DWS_EVENT = 'user_im_message_receive_group';
export const DWS_ALL_GROUPS = 'user_im_message_receive_group_all';
const START_TIMEOUT_MS = 60_000;
const HEALTH_CHECK_MS = 30_000;
const DOWN_LIMIT_MS = 60_000;
// The shared ConnectorProcess gives pause/stop requests five seconds.
const STOP_TIMEOUT_MS = 4_500;

export async function checkDws(registry, { cliBin, profile }) {
  const { code, stdout, stderr, timedOut } = await registry.run(cliBin,
    ['--profile', profile, 'auth', 'status', '--format', 'json'], { timeoutMs: 15_000 });
  if (timedOut) throw new Error('DingTalk DWS authorization check timed out');
  if (code !== 0) throw new Error(`DingTalk DWS authorization check failed (exit ${code}): ${safeError(stderr.trim())}`);
  let status;
  try {
    status = JSON.parse(stdout);
  } catch {
    throw new Error('DingTalk DWS authorization check returned invalid JSON');
  }
  if (status?.authenticated !== true) throw new Error('DingTalk DWS profile is unauthorized');
}

export class DwsSubscription {
  constructor(registry, config, logger, onEvent, onFailure) {
    Object.assign(this, { registry, config, logger, onEvent, onFailure });
    this.children = new Map();
    this.ended = false;
  }

  start(groups) {
    if (this.config.allGroups) this.subscribe(null);
    else for (const group of groups) this.subscribe(group);
    this.healthTimer = setInterval(() => {
      if (this.checking || this.ended) return;
      this.checking = true;
      void this.checkTransport().finally(() => { this.checking = false; });
    }, HEALTH_CHECK_MS);
    this.healthTimer.unref();
  }

  async checkTransport() {
    const ready = [...this.children.values()].flatMap(({ subscription }) => subscription ? [subscription] : []);
    if (!ready.length) return;
    const { cliBin, profile, allGroups } = this.config;
    const event = allGroups ? DWS_ALL_GROUPS : DWS_EVENT;
    let healthy = false;
    try {
      const result = await this.registry.run(cliBin, [
        '--profile', profile, 'event', 'status', '--event', event, '--format', 'json',
      ], { timeoutMs: 12_000 });
      if (result.code === 0 && !result.timedOut) {
        const live = JSON.parse(result.stdout).bus?.live;
        // Connected and idle both indicate a live source with no traffic requirement.
        healthy = ['connected', 'idle'].includes(live?.source_state?.state) && ready.every(({ busPID, id }) =>
          live.bus.pid === busPID && live.consumers?.some((consumer) => consumer.subscribe_id === id));
      }
    } catch {
      this.logger.debug('dingtalk_dws_status_unavailable');
    }
    if (this.ended) return;
    if (healthy) {
      this.downSince = null;
      return;
    }
    this.downSince ??= Date.now();
    if (Date.now() - this.downSince >= DOWN_LIMIT_MS) this.fail('DingTalk DWS transport stayed unavailable');
  }

  subscribe(group) {
    const { cliBin, profile } = this.config;
    const event = group === null ? DWS_ALL_GROUPS : DWS_EVENT;
    const child = this.registry.register(spawn(cliBin, [
      '--profile', profile, 'event', 'consume', event,
      ...(group === null ? [] : ['--group', group]), '--flatten', '-f', 'ndjson', '--ttl', '0',
    ], { env: sanitizeNonArkChildEnv(process.env), detached: this.registry.detached, stdio: ['pipe', 'pipe', 'pipe'] }));
    const entry = { timer: setTimeout(() => this.fail('DingTalk DWS subscription did not become ready'), START_TIMEOUT_MS) };
    entry.timer.unref();
    this.children.set(child, entry);
    createInterface({ input: child.stderr }).on('line', (line) => {
      if (this.ended || !line.startsWith(`[event] ready event_key=${event} `)) return;
      const match = / bus_pid=(\d+) subscribe_id=(\S+)/.exec(line);
      if (!match) return;
      entry.subscription = { busPID: Number(match[1]), id: match[2] };
      clearTimeout(entry.timer);
      this.logger.info('dingtalk_dws_subscription_ready');
    });
    createInterface({ input: child.stdout }).on('line', (line) => {
      if (this.ended || !line.trim()) return;
      let raw;
      try {
        raw = JSON.parse(line);
      } catch {
        this.logger.warn('dingtalk_dws_event_rejected');
        return;
      }
      this.onEvent(raw, group ?? raw?.conversation_id);
    });
    child.stdin.on('error', () => {});
    child.once('error', (error) => this.fail(`DingTalk DWS process could not start: ${safeError(error)}`));
    child.once('close', () => {
      clearTimeout(entry.timer);
      this.children.delete(child);
      this.fail('DingTalk DWS subscription process exited');
    });
  }

  fail(reason) {
    if (this.ended) return;
    this.logger.warn('dingtalk_dws_subscription_failed', { reason });
    const exit = { code: 1, signal: null, stderr: reason };
    void this.stop().then(
      () => this.onFailure(exit),
      (error) => this.onFailure({ ...exit, stderr: `${reason}; ${safeError(error)}` }, true),
    );
  }

  stop() {
    return this.stopping ??= this.close();
  }

  async close() {
    this.ended = true;
    clearInterval(this.healthTimer);
    const results = await Promise.allSettled([...this.children].map(async ([child, entry]) => {
      clearTimeout(entry.timer);
      const deadline = AbortSignal.timeout(STOP_TIMEOUT_MS);
      const terminate = setTimeout(() => terminateProcessTree(child, 'SIGTERM'), 1_000);
      const kill = setTimeout(() => terminateProcessTree(child, 'SIGKILL'), 4_000);
      try {
        child.stdin.end();
        await once(child, 'close', { signal: deadline });
      } catch (error) {
        if (!deadline.aborted) throw error;
        // A descendant can hold the pipes open after the consumer exits.
        // Release our handles but report that teardown could not be confirmed.
        child.stdout.destroy();
        child.stderr.destroy();
        throw new Error('DingTalk DWS consumer did not close before the stop deadline');
      } finally {
        clearTimeout(terminate);
        clearTimeout(kill);
      }
    }));
    const failed = results.find((result) => result.status === 'rejected');
    if (failed) throw failed.reason;
  }
}

// The CLI uploads local images a proactive Markdown body refers to. A path the
// model wrote but no file backs would fail the whole send, so local references
// travel as their alt text; remote links need no upload and stay as written.
const INLINE_IMAGE = /!\[([^\]]*)\]\(\s*(?:<([^>\r\n]+)>|([^\s)]+))(?:[ \t]+"[^"]*")?\s*\)/g;
const REMOTE_IMAGE = /^(?:https?:\/\/|mediaid:.)|^[@$][^/\\]+$/i;

export function markdownForDws(text) {
  return text.replace(INLINE_IMAGE, (whole, alt, angleTarget, target) =>
    REMOTE_IMAGE.test(angleTarget ?? target) ? whole : alt);
}

// Reuse the Core action's idempotency key and the CLI's delivery receipt.
// A failed or uncertain send is handed back to the existing Core Outbox.
export async function sendDws(registry, { cliBin, profile }, { group, message, text, key, cwd, timeoutMs }) {
  const command = message ? '+messages-reply' : '+messages-send';
  const body = message ? text : markdownForDws(text);
  const args = ['--profile', profile, 'chat', command, '--as', 'user', '--group', group,
    ...(message ? ['--message-id', message, '--content', '-'] : ['--markdown', '-']),
    '--body-stdin', '--idempotency-key', key, '--wait-delivery', '--yes', '--format', 'json'];
  const result = await registry.run(cliBin, args, { cwd, timeoutMs, input: body });
  if (result.timedOut) throw new Error('DingTalk DWS send timed out before confirming delivery');
  if (result.code !== 0) {
    const detail = safeError(result.stderr.trim());
    throw new Error(`DingTalk DWS did not confirm delivery (exit ${result.code})${detail ? `: ${detail}` : ''}`);
  }
  let output;
  try {
    output = JSON.parse(result.stdout);
  } catch {
    throw new Error('DingTalk DWS send returned invalid receipt JSON');
  }
  const response = output?.data ?? output;
  const id = response?.openMessageId ?? response?.messageId;
  if (response?.deliveryStatus !== 'delivered' || !id) throw new Error('DingTalk DWS did not confirm delivery');
  return id;
}
