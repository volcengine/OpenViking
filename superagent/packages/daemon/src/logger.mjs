// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';

const levels = { debug: 10, info: 20, warn: 30, error: 40 };

export function redactSensitive(value) {
  return String(value)
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer [REDACTED]')
    .replace(/(["'][^"']*(?:authorization|api[_-]?key|token|secret|credential|password|cookie)[^"']*["']\s*:\s*["'])[^"']*/gi, '$1[REDACTED]')
    .replace(/(\b[A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|CREDENTIAL|PASSWORD|COOKIE|API_KEY)[A-Za-z0-9_]*\s*=\s*["']?)[^\s,"'&}]+/gi, '$1[REDACTED]')
    .replace(/((?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|app[_-]?secret|client[_-]?secret|credential|password|cookie)\s*[=:]\s*["']?)[^\s,"'&}]+/gi, '$1[REDACTED]')
    .replace(/([?&](?:token|api_key|access_token|secret|password)=)[^&\s]+/gi, '$1[REDACTED]')
    .replace(/\b(?:sk|t)-[A-Za-z0-9_-]{12,}\b/g, '[REDACTED]')
    .replace(/\b(?:github_pat_[A-Za-z0-9_]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|xox[baprs]-[A-Za-z0-9-]{12,})\b/g, '[REDACTED]');
}

function cleanError(value) {
  const text = value instanceof Error ? (value.stack || value.message) : String(value);
  return redactSensitive(text)
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
    .slice(0, 4000);
}

function cleanValue(value, seen = new WeakSet(), depth = 0) {
  if (value instanceof Error) return cleanError(value);
  if (typeof value === 'bigint') return Number(value);
  if (typeof value === 'string') {
    const cleaned = redactSensitive(value);
    return cleaned.length > 4000 ? `${cleaned.slice(0, 4000)}…` : cleaned;
  }
  if (value && typeof value === 'object') {
    if (depth >= 8) return '[TRUNCATED]';
    if (seen.has(value)) return '[CIRCULAR]';
    seen.add(value);
    if (Array.isArray(value)) return value.map((item) => cleanValue(item, seen, depth + 1));
    const cleaned = {};
    for (const [key, child] of Object.entries(value)) {
      if (/(?:authorization|api[_-]?key|token|secret|credential|password|cookie)/i.test(key)) {
        cleaned[key] = '[REDACTED]';
      } else {
        cleaned[key] = cleanValue(child, seen, depth + 1);
      }
    }
    return cleaned;
  }
  return value;
}

export function safeError(error) {
  return redactSensitive(error instanceof Error ? error.message : String(error));
}

// For errors that may embed Lark identifiers (chat/message/user ids) in their
// text: redact them alongside the generic sensitive patterns.
export function safeChatLookupError(error) {
  return safeError(error)
    .replace(/\b(?:om|oc|ou|on|omt|cli)_[A-Za-z0-9_-]{6,}\b/g, '[LARK_ID]');
}

export function opaqueRef(value) {
  if (!value) return undefined;
  return createHash('sha256').update(String(value)).digest('hex').slice(0, 12);
}

export function createLogger(level = 'info', base = {}) {
  const threshold = levels[level] ?? levels.info;

  function emit(kind, message, fields = {}) {
    if (levels[kind] < threshold) return;
    const record = {
      time: new Date().toISOString(),
      level: kind,
      message,
      ...base,
    };
    for (const [key, value] of Object.entries(fields)) {
      if (value !== undefined) record[key] = cleanValue(value);
    }
    const stream = kind === 'error' ? process.stderr : process.stdout;
    stream.write(`${JSON.stringify(record)}\n`);
  }

  return {
    debug: (message, fields) => emit('debug', message, fields),
    info: (message, fields) => emit('info', message, fields),
    warn: (message, fields) => emit('warn', message, fields),
    error: (message, fields) => emit('error', message, fields),
    child: (fields) => createLogger(level, { ...base, ...fields }),
  };
}
