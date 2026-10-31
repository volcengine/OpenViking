#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { ProcessRegistry } from '../process-utils.mjs';
import { safeError } from '../logger.mjs';
import { LarkClient } from '../lark.mjs';
import { WeixinClient } from '../weixin/client.mjs';
import { TelegramClient } from '../telegram/client.mjs';
import { QQClient } from '../qq/client.mjs';
import { SlackClient } from '../slack/client.mjs';
import { EmailClient } from '../email/client.mjs';
import { MeetingNoteSync } from '../meeting/note-sync.mjs';
import { getConnectorManifest } from './catalog.mjs';
import { docCommentNotice, isDocCommentEvent, isOwnDocComment, normalizeLarkEvent } from './lark-events.mjs';
import { invokeOperation } from './operations.mjs';
import {
  parseProtocolLine,
  resultMessage,
  statusMessage,
} from './protocol.mjs';

// The connector type; an email worker serves the one mailbox in its config.
const connectorId = process.argv[2];
const manifest = getConnectorManifest(connectorId);
let config;
try {
  const parsed = JSON.parse(process.env.PERSONAL_AGENT_CONNECTOR_CONFIG ?? '');
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('missing config');
  config = {
    ...parsed,
    channel: connectorId,
    instance: parsed.instance || connectorId,
    connectorWorker: true,
  };
} catch {
  process.stderr.write('connector worker configuration is invalid\n');
  process.exit(1);
}
delete process.env.PERSONAL_AGENT_CONNECTOR_CONFIG;

function write(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function workerLogger(level = 'info', base = {}) {
  const order = { debug: 10, info: 20, warn: 30, error: 40 };
  const threshold = order[level] ?? order.info;
  const emit = (kind, message, fields = {}) => {
    if (order[kind] < threshold) return;
    process.stderr.write(`${JSON.stringify({
      time: new Date().toISOString(),
      level: kind,
      message,
      connector: config.instance,
      ...base,
      ...Object.fromEntries(Object.entries(fields).map(([key, value]) => (
        [key, key.toLowerCase().includes('token') ? '[REDACTED]' : value instanceof Error ? safeError(value) : value]
      ))),
    })}\n`);
  };
  return {
    debug: (message, fields) => emit('debug', message, fields),
    info: (message, fields) => emit('info', message, fields),
    warn: (message, fields) => emit('warn', message, fields),
    error: (message, fields) => emit('error', message, fields),
    child: (fields) => workerLogger(level, { ...base, ...fields }),
  };
}

const logger = workerLogger(config.logLevel);
// Provider subprocesses stay in the worker's process group. Killing a failed
// Connector therefore also reaps lark-cli and any short-lived command child.
const registry = new ProcessRegistry(logger, { detached: false });
const Client = { weixin: WeixinClient, email: EmailClient, telegram: TelegramClient, slack: SlackClient, qq: QQClient }[connectorId] ?? LarkClient;
const client = new Client(config, registry, logger);
// The Lark Connector of the instance that stores meeting notes scans for
// them and stores them in OV; nothing of it reaches the Core (see
// meeting/note-sync.mjs).
const notes = connectorId === 'lark' && config.meetingNotes
  ? MeetingNoteSync.forWorker({ config, registry, logger, profile: () => client.profile }) : null;
let checked = false;
let started = false;
let stopping = false;
let inputBuffer = '';

// A doc comment notice carries ids only: read the comment, then emit. A failed
// read still emits, so the agent can read the thread itself.
async function emitDocComment(raw) {
  try {
    const notice = docCommentNotice(raw);
    if (isOwnDocComment(notice)) return;
    let comment = null;
    try {
      comment = await client.getDocComment(notice);
    } catch (error) {
      logger.warn('doc_comment_read_failed', { error: safeError(error) });
    }
    const event = normalizeLarkEvent(raw, 'lark', { docComment: comment });
    if (event) write(event);
  } catch (error) {
    logger.warn('connector_event_rejected', { error: safeError(error) });
  }
}

async function handleRequest(message) {
  try {
    let result;
    if (message.operation === 'spec') {
      result = { manifest };
    } else if (message.operation === 'check') {
      result = await client.preflight();
      checked = true;
    } else if (message.operation === 'start') {
      if (!checked) throw new Error('connector must pass check before start');
      if (started) throw new Error('connector is already started');
      started = true;
      client.startSubscription((line) => {
        try {
          const raw = JSON.parse(line);
          if (connectorId === 'lark' && isDocCommentEvent(raw)) {
            void emitDocComment(raw);
            return;
          }
          write(connectorId === 'lark' ? normalizeLarkEvent(raw) : parseProtocolLine(line));
        } catch (error) {
          logger.warn('connector_event_rejected', { error: safeError(error) });
        }
      }, (exit) => {
        if (stopping) return;
        write(statusMessage(connectorId, 'failed', {
          code: exit.code ?? null,
          signal: exit.signal ?? null,
          error: String(exit.stderr ?? '').slice(-2_000),
        }));
        setImmediate(() => process.exit(1));
      });
      notes?.start();
      result = { started: true };
    } else if (message.operation === 'action') {
      if (!checked) throw new Error('connector is not ready');
      const payload = message.payload;
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)
        || typeof payload.name !== 'string'
        || !payload.input || typeof payload.input !== 'object' || Array.isArray(payload.input)) {
        throw new Error('connector action payload is invalid');
      }
      result = await invokeOperation(client, payload.name, payload.input);
    } else if (message.operation === 'pause') {
      await notes?.stop();
      if (typeof client.stopSubscription === 'function') await client.stopSubscription();
      else await client.stop();
      started = false;
      result = { paused: true };
    } else if (message.operation === 'stop') {
      stopping = true;
      await notes?.stop();
      await client.stop();
      await registry.stopAll(2_000);
      result = { stopped: true };
      write(resultMessage(message.id, result));
      setImmediate(() => process.exit(0));
      return;
    } else {
      throw new Error(`unsupported connector operation: ${message.operation}`);
    }
    write(resultMessage(message.id, result));
  } catch (error) {
    write(resultMessage(message.id, null, safeError(error)));
  }
}

process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  inputBuffer += chunk;
  while (true) {
    const newline = inputBuffer.indexOf('\n');
    if (newline < 0) break;
    const line = inputBuffer.slice(0, newline);
    inputBuffer = inputBuffer.slice(newline + 1);
    if (!line.trim()) continue;
    try {
      const message = parseProtocolLine(line);
      if (message.type !== 'REQUEST') throw new Error('connector worker accepts REQUEST messages only');
      void handleRequest(message);
    } catch (error) {
      logger.warn('connector_request_rejected', { error: safeError(error) });
    }
  }
  if (Buffer.byteLength(inputBuffer, 'utf8') > 1_000_000) {
    logger.error('connector_request_oversize');
    process.exit(1);
  }
});

async function shutdown(signal) {
  if (stopping) return;
  stopping = true;
  logger.info('connector_worker_stopping', { signal });
  try { await notes?.stop(); } catch {}
  try { await client.stop(); } catch {}
  try { await registry.stopAll(2_000); } catch {}
  process.exit(0);
}

process.on('SIGINT', () => void shutdown('SIGINT'));
process.on('SIGTERM', () => void shutdown('SIGTERM'));
process.stdin.resume();
