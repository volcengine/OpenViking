// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomUUID } from 'node:crypto';
import { normalizeEvent } from '../event.mjs';

export const CONNECTOR_PROTOCOL_VERSION = '0.2';
export const CONNECTOR_CAPABILITIES = Object.freeze(['sync', 'search', 'events', 'actions']);

const MESSAGE_TYPES = new Set(['SPEC', 'REQUEST', 'RESULT', 'EVENT', 'STATUS']);
const CONNECTOR_NAME = /^[a-z][a-z0-9-]{0,63}$/;

function record(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value;
}

function boundedString(value, label, max = 256) {
  if (typeof value !== 'string' || !value || value.length > max
    || /[\u0000-\u001f\u007f]/.test(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

export function connectorManifest(input) {
  const value = record(input, 'connector manifest');
  const name = boundedString(value.name, 'connector manifest name', 64);
  if (!CONNECTOR_NAME.test(name)) throw new Error('connector manifest name is invalid');
  const capabilities = Array.isArray(value.capabilities) ? [...new Set(value.capabilities)] : [];
  if (capabilities.length === 0
    || capabilities.some((item) => !CONNECTOR_CAPABILITIES.includes(item))) {
    throw new Error('connector manifest capabilities are invalid');
  }
  const actions = record(value.actions ?? {}, 'connector manifest actions');
  for (const spec of Object.values(actions)) record(spec, 'connector action spec');
  const queries = value.queries ?? [];
  if (!Array.isArray(queries) || queries.some((query) => typeof query !== 'string')) {
    throw new Error('connector manifest queries are invalid');
  }
  return Object.freeze({
    protocol_version: CONNECTOR_PROTOCOL_VERSION,
    name,
    version: boundedString(value.version, 'connector manifest version', 128),
    capabilities: Object.freeze(capabilities),
    actions,
    queries,
    auth_schema: record(value.auth_schema ?? {}, 'connector auth schema'),
    config_schema: record(value.config_schema ?? {}, 'connector config schema'),
  });
}

export function requestMessage(operation, payload = {}, id = randomUUID()) {
  return {
    protocol_version: CONNECTOR_PROTOCOL_VERSION,
    type: 'REQUEST',
    id: boundedString(id, 'connector request id'),
    operation: boundedString(operation, 'connector request operation', 64),
    payload: record(payload, 'connector request payload'),
  };
}

export function resultMessage(requestId, result = {}, error = null) {
  const errorText = error === null
    ? null
    : String(error).replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 4_000) || 'connector request failed';
  return {
    protocol_version: CONNECTOR_PROTOCOL_VERSION,
    type: 'RESULT',
    request_id: boundedString(requestId, 'connector result request id'),
    ok: error === null,
    ...(error === null
      ? { result: result === undefined ? null : result }
      : { error: errorText }),
  };
}

// Event ids identify occurrences, not transport attempts. Callers must supply a
// stable provider id or a deterministic occurrence key; never generate one here.
export function eventMessage(connector, fields) {
  return normalizeEvent({
    ...fields,
    protocol_version: CONNECTOR_PROTOCOL_VERSION,
    type: 'EVENT',
    connector,
    instance: fields.instance ?? connector,
  }).envelope;
}

export function statusMessage(connector, status, detail = {}) {
  if (!CONNECTOR_NAME.test(connector)) throw new Error('connector status source is invalid');
  return {
    protocol_version: CONNECTOR_PROTOCOL_VERSION,
    type: 'STATUS',
    connector,
    status: boundedString(status, 'connector status', 64),
    detail: record(detail, 'connector status detail'),
  };
}

export function parseProtocolLine(line) {
  if (typeof line !== 'string' || !line.trim()) throw new Error('connector protocol line is empty');
  let parsed;
  try { parsed = JSON.parse(line); } catch {
    throw new Error('connector protocol line is not valid JSON');
  }
  record(parsed, 'connector protocol message');
  if (parsed.protocol_version !== CONNECTOR_PROTOCOL_VERSION) {
    throw new Error(`unsupported connector protocol version: ${parsed.protocol_version ?? 'missing'}`);
  }
  if (!MESSAGE_TYPES.has(parsed.type)) throw new Error('connector protocol message type is invalid');
  if (parsed.type === 'REQUEST') {
    boundedString(parsed.id, 'connector request id');
    boundedString(parsed.operation, 'connector request operation', 64);
    record(parsed.payload, 'connector request payload');
  } else if (parsed.type === 'RESULT') {
    boundedString(parsed.request_id, 'connector result request id');
    if (typeof parsed.ok !== 'boolean') throw new Error('connector result status is invalid');
  } else if (parsed.type === 'EVENT') {
    return normalizeEvent(parsed).envelope;
  } else if (parsed.type === 'STATUS') {
    if (!CONNECTOR_NAME.test(parsed.connector ?? '')) throw new Error('connector status source is invalid');
    boundedString(parsed.status, 'connector status', 64);
  }
  return parsed;
}
