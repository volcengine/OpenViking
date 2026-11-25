// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { open } from 'node:fs/promises';

// A pm2 app that is not online leaves its reason in the app's own error log:
// the daemon and the Web write one JSON line per failure, and Node writes a
// stack when the module cannot even load. Reading the tail turns
// "status=errored, exit_code=1" into a cause the CLI can name. Classification
// lives here, in the release-independent updater; the wording is the caller's.

const TAIL_BYTES = 8 * 1024;
const DETAIL_MAX = 300;
const FAILURE_MESSAGES = new Set([
  'invalid_configuration',
  'web_start_failed',
  'daemon_start_failed',
  'uncaught_exception',
  'unhandled_rejection',
]);

export async function readAppLogTail(file, { maxBytes = TAIL_BYTES } = {}) {
  if (!file) return '';
  let handle;
  try {
    handle = await open(file, 'r');
    const { size } = await handle.stat();
    const length = Math.min(size, maxBytes);
    const buffer = Buffer.alloc(length);
    if (length > 0) await handle.read(buffer, 0, length, size - length);
    return buffer.toString('utf8');
  } catch {
    // No log yet (the app never wrote one) is the same as an empty one.
    return '';
  } finally {
    await handle?.close().catch(() => {});
  }
}

function jsonRecords(tail) {
  const records = [];
  for (const line of tail.split('\n')) {
    const text = line.trim();
    if (!text.startsWith('{')) continue;
    try {
      const record = JSON.parse(text);
      if (record && typeof record === 'object' && typeof record.message === 'string') records.push(record);
    } catch {
      // A truncated first line of the tail or a non-JSON line: not a record.
    }
  }
  return records;
}

function errorText(record) {
  const value = record?.error;
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') {
    return [value.message, value.code, value.stack].filter((part) => typeof part === 'string').join(' ');
  }
  return '';
}

function firstLine(text) {
  const line = String(text).split('\n').map((part) => part.trim()).find(Boolean) ?? '';
  return line.length > DETAIL_MAX ? `${line.slice(0, DETAIL_MAX)}…` : line;
}

// A stack starts with the code frame; the sentence a person needs is the
// "Error: ..." line inside it.
function firstErrorLine(text) {
  const lines = String(text).split('\n').map((part) => part.trim()).filter(Boolean);
  return firstLine(lines.find((line) => /^[A-Za-z_]*Error\b/.test(line) || /^Cannot find module\b/.test(line)) ?? lines[0] ?? '');
}

// "listen EADDRINUSE: address already in use 127.0.0.1:3737", ":::3737",
// "[::1]:3737". The dot/colon requirement keeps an IP's own digits out.
function listenPort(text) {
  const match = /\bEADDRINUSE\b[^\n]*[:.](\d{2,5})\b/.exec(text);
  return match ? Number(match[1]) : null;
}

function configuredPort(state) {
  const value = Number(state?.OV_SUPERAGENT_WEB_PORT ?? state?.OV_SUPERAGENT_PORT);
  return Number.isSafeInteger(value) && value > 0 && value < 65536 ? value : null;
}

// `state` is pm2's pm2_env for the app; `tail` its error log tail. Returns
// { kind, port?, detail? }, or null when the log says nothing usable.
export function diagnoseAppFailure({ state = {}, tail = '' } = {}) {
  const failures = jsonRecords(tail).filter((record) => record.level === 'error'
    && FAILURE_MESSAGES.has(record.message));
  const latest = failures.at(-1) ?? null;
  // With a JSON failure line, only that line speaks for the current run; an
  // older crash in the same log must not be blamed. Without one, the process
  // died before logging and the stack itself is the evidence.
  const text = latest ? `${errorText(latest)} ${latest.stack ?? ''}` : tail;
  const detail = latest ? firstLine(errorText(latest) || latest.stack || latest.message) : firstErrorLine(tail);

  if (/EADDRINUSE|address already in use/i.test(text)) {
    return { kind: 'port_in_use', port: listenPort(text) ?? configuredPort(state), detail };
  }
  if (latest?.message === 'invalid_configuration') return { kind: 'invalid_configuration', detail };
  if (/ERR_MODULE_NOT_FOUND|Cannot find module|MODULE_NOT_FOUND|\bENOENT\b/.test(text)) {
    return { kind: 'incomplete_install', detail };
  }
  if (/\bEACCES\b|\bEPERM\b|permission denied/i.test(text)) return { kind: 'permission', detail };
  if (latest && ['uncaught_exception', 'unhandled_rejection'].includes(latest.message)) {
    return { kind: 'crashed', detail };
  }
  if (latest) return { kind: 'start_failed', detail };
  if (/\bEADDRINUSE\b|^\w*Error\b|Cannot find module/m.test(tail)) return { kind: 'crashed', detail };
  return null;
}
