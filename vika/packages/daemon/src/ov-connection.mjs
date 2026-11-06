// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';

export class OVUnavailableError extends Error {
  constructor(message, httpStatus) {
    super(message);
    this.name = 'OVUnavailableError';
    this.httpStatus = httpStatus;
  }
}

export function readOVConfig(filePath) {
  try {
    const config = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error();
    return config;
  } catch {
    throw new Error('OpenViking configuration is missing or invalid');
  }
}

export class OVConnection {
  constructor(config, { fetch: fetchImpl = globalThis.fetch, timeoutMs = 15_000 } = {}) {
    let url;
    try { url = new URL(config.url); } catch {
      throw new Error('OpenViking URL is missing or invalid');
    }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error('OpenViking URL is missing or invalid');
    }
    this.url = String(url).replace(/\/$/, '');
    this.headers = { 'Content-Type': 'application/json' };
    if (config.api_key) this.headers['X-API-Key'] = config.api_key;
    for (const [key, header] of [['account', 'X-OpenViking-Account'], ['user', 'X-OpenViking-User'], ['agent', 'X-OpenViking-Agent']]) {
      if (config[key]) this.headers[header] = config[key];
    }
    // Configured user hint; an API key may authenticate a different user.
    this.user = String(config.user ?? '').trim();
    this.fetch = fetchImpl;
    this.timeoutMs = timeoutMs;
  }

  async json(route, body, { timeoutMs = this.timeoutMs } = {}) {
    return this.send(route, {
      method: body === undefined ? 'GET' : 'POST', headers: this.headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    }, timeoutMs);
  }

  // One file as multipart form data, for temp_upload.
  async upload(route, { data, name, type }, { timeoutMs = this.timeoutMs } = {}) {
    const form = new FormData();
    form.append('file', new Blob([data], { type: type || 'application/octet-stream' }), name);
    const { 'Content-Type': _json, ...headers } = this.headers;
    return this.send(route, { method: 'POST', headers, body: form }, timeoutMs);
  }

  async send(route, init, timeoutMs) {
    let response;
    try {
      response = await this.fetch(`${this.url}${route}`, {
        ...init, signal: AbortSignal.timeout(timeoutMs), redirect: 'error',
      });
      const text = await response.text();
      if (text.length > 8_000_000) throw new Error();
      const data = JSON.parse(text);
      if (!response.ok || data.status !== 'ok') throw new Error();
      return data;
    } catch (error) {
      // Never expose credentials, URLs, or response bodies. A request that ran
      // out of time says so: unlike a refused connection, it tells nothing
      // about other requests.
      throw Object.assign(new OVUnavailableError(
        `OpenViking request failed${response ? ` (HTTP ${response.status})` : ' or timed out'}`,
        response?.status,
      ), { timedOut: error?.name === 'TimeoutError' });
    }
  }

  // /health is public. The status call goes through the same identity check
  // as the session API, and unlike listing sessions it costs the same however
  // much the user has stored. Resolves with the authenticated user id.
  async authenticate() {
    const { result } = await this.json('/api/v1/system/status');
    if (result?.initialized === false) throw new OVUnavailableError('OpenViking is still starting');
    return result?.user ?? null;
  }

  async check() {
    const health = await this.json('/health');
    await this.authenticate();
    return health;
  }
}
