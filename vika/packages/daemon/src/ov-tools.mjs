// Copyright © 2026 Volcano Engine (火山引擎) and the OpenViking contributors.
// SPDX-License-Identifier: AGPL-3.0

// The Core's OpenViking tool client: the REST face of the `openviking_*`
// agent tools every local runtime gets through the same ActionService. It
// resolves the instance's own OV identity per call (a rotated config file
// applies without a restart), never retries a write, and maps every failure
// to one model-readable code without echoing URLs, keys or response bodies.
// It shares nothing with the session pipeline: journal sync and session
// commit stay in ov-session-context.mjs, and the tools never touch them.

import { randomUUID } from 'node:crypto';
import { OVConnection, readOVConfig } from './ov-connection.mjs';
import { readWorkspaceFile } from './workspace-file.mjs';

// Where add_resource lands when the caller names no target: the instance's
// private inbox, never the account-shared viking://resources/ root.
export const OV_DEFAULT_PARENT = 'viking://~/resources/inbox';

const ABSTRACT_MAX_CHARS = 500;
const FIND_TEXT_MAX_CHARS = 20_000;
const READ_TEXT_MAX_CHARS = 16_000;
const URL_MAX_CHARS = 4_096;
const UPLOAD_MAX_BYTES = 20 * 1024 * 1024;

function trimmed(value) {
  return typeof value === 'string' ? value.trim() : '';
}

// A refusal the model can act on; `code` is what logs and activities record.
function ovError(code, message) {
  return Object.assign(new Error(message), { code });
}

// Credentials for the tools, resolved the way the deployment pins them:
// OPENVIKING_CLI_CONFIG_FILE is the instance config (url, api_key, account,
// user, actor_peer_id); a bare OPENVIKING_URL covers a local dev-mode server.
// Nothing else is consulted, and an explicit config that is missing or
// invalid is never replaced by another identity.
export function resolveOvConfig(environment = process.env) {
  const configPath = trimmed(environment.OPENVIKING_CLI_CONFIG_FILE);
  if (configPath) {
    try {
      return { config: readOVConfig(configPath) };
    } catch {
      return { config: null, error: 'the OpenViking configuration file is missing or invalid' };
    }
  }
  const url = trimmed(environment.OPENVIKING_URL);
  if (!url) return {};
  const config = { url };
  const apiKey = trimmed(environment.OPENVIKING_API_KEY) || trimmed(environment.OPENVIKING_BEARER_TOKEN);
  if (apiKey) config.api_key = apiKey;
  const account = trimmed(environment.OPENVIKING_ACCOUNT);
  if (account) config.account = account;
  const user = trimmed(environment.OPENVIKING_USER);
  if (user) config.user = user;
  return { config };
}

// The peer the instance acts for, exactly as the session transport pins it:
// the environment override first, then the config's own field.
function actorPeer(config, environment) {
  return trimmed(environment.OPENVIKING_PEER_ID) || trimmed(config?.actor_peer_id) || trimmed(config?.peer_id);
}

// One search result, the way the official endpoint reports it, as one
// model-facing line. A skill is indexed per file; its package root is what
// the agent should read.
function hitTypeUri(type, uri) {
  if (type !== 'skill') return uri;
  const match = uri.match(/^(viking:\/\/.*\/skills\/[^/]+)\//);
  return match ? `${match[1]}/SKILL.md` : uri;
}

function truncate(text, max) {
  const value = String(text ?? '');
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

// find and search share one answer: the ranked hits as short lines plus the
// abstracts, deduplicated by the uri the agent would read.
function formatHits(result, { readTool } = {}) {
  const hits = [];
  const seen = new Map();
  for (const [type, category] of [['memory', 'memories'], ['resource', 'resources'], ['skill', 'skills']]) {
    for (const entry of result?.[category] ?? []) {
      if (!entry?.uri) continue;
      const uri = hitTypeUri(type, String(entry.uri));
      const hit = {
        type, uri,
        score: Number(entry.score ?? 0),
        abstract: truncate(trimmed(entry.abstract) || trimmed(entry.overview), ABSTRACT_MAX_CHARS),
      };
      const previous = seen.get(uri);
      if (previous !== undefined) {
        if (hit.score > hits[previous].score) hits[previous] = hit;
        continue;
      }
      seen.set(uri, hits.length);
      hits.push(hit);
    }
  }
  if (hits.length === 0) {
    return { text: 'No matching context found.', data: { hits: [], total: 0 } };
  }
  const lines = hits.map((hit) => `- [${hit.type} ${Math.round(hit.score * 100)}%] ${hit.uri}\n    ${hit.abstract || '(no abstract)'}`);
  let text = `Found ${hits.length} item(s):\n\n${lines.join('\n')}\n\nUse the ${readTool} tool to expand a URI.`;
  if (text.length > FIND_TEXT_MAX_CHARS) {
    const note = '\n\n(Result truncated; narrow the query or lower the limit.)';
    text = `${text.slice(0, FIND_TEXT_MAX_CHARS - note.length - 1)}…${note}`;
  }
  return { text, data: { hits, total: Number(result?.total ?? hits.length) } };
}

export class OVToolClient {
  // `timeoutMs` bounds one request; the faster read/list paths get half of
  // it. `uploadMaxBytes` bounds one add_resource file.
  constructor({
    environment = process.env,
    fetch: fetchImpl = globalThis.fetch,
    timeoutMs = 20_000,
    readTimeoutMs = Math.floor(timeoutMs / 2),
    uploadMaxBytes = UPLOAD_MAX_BYTES,
  } = {}) {
    this.environment = environment;
    this.fetchImpl = fetchImpl;
    this.timeoutMs = timeoutMs;
    this.readTimeoutMs = readTimeoutMs;
    this.uploadMaxBytes = uploadMaxBytes;
  }

  // The connection of the moment. Resolved before every call so rotated
  // credentials apply without a restart; a missing or broken config is an
  // explicit failure, never a silent identity change.
  connection() {
    const resolved = resolveOvConfig(this.environment);
    if (!resolved.config) {
      throw ovError('openviking_unavailable', resolved.error ?? 'OpenViking configuration is unavailable');
    }
    let connection;
    try {
      connection = new OVConnection(resolved.config, { fetch: this.fetchImpl, timeoutMs: this.timeoutMs });
    } catch {
      throw ovError('openviking_unavailable', 'OpenViking configuration is invalid');
    }
    return { connection, peer: actorPeer(resolved.config, this.environment) };
  }

  // One REST call with the instance's identity and the actor peer, mapped to
  // one model-readable code. Never echoes the URL, the key or the body.
  async send(connection, peer, route, { method = 'GET', body, form, timeoutMs = this.timeoutMs } = {}) {
    const headers = { ...connection.headers };
    if (peer) headers['X-OpenViking-Actor-Peer'] = peer;
    let payload = body === undefined ? undefined : JSON.stringify(body);
    if (form) {
      delete headers['Content-Type'];
      payload = form;
    }
    let response;
    try {
      response = await this.fetchImpl(`${connection.url}${route}`, {
        method, headers, ...(payload === undefined ? {} : { body: payload }),
        redirect: 'error', signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      if (error?.name === 'TimeoutError' || error?.name === 'AbortError') {
        throw ovError('openviking_unavailable', 'OpenViking timed out');
      }
      throw ovError('openviking_unavailable', 'OpenViking is unreachable');
    }
    let data = null;
    try {
      const text = await response.text();
      if (text.length > 8_000_000) throw new Error('oversized');
      data = text ? JSON.parse(text) : null;
    } catch {
      data = null;
    }
    // The whole envelope, so a listing's has_more survives alongside result.
    if (response.ok && data?.status === 'ok') return data;
    const status = response.status;
    if (status === 400 || status === 422) throw ovError('openviking_invalid_input', 'OpenViking rejected the input');
    if (status === 401) throw ovError('openviking_unauthorized', 'OpenViking rejected the credentials');
    if (status === 403) throw ovError('openviking_forbidden', 'OpenViking refused this action');
    if (status === 404) throw ovError('openviking_not_found', 'Nothing is stored at that address');
    if (status === 408 || status === 429 || status >= 500) {
      throw ovError('openviking_unavailable', 'OpenViking is temporarily unavailable');
    }
    throw ovError('openviking_failed', `OpenViking request failed (HTTP ${status})`);
  }

  // The shared query body of find and search.
  searchBody({ query, limit, target_uri, context_type, min_score, search_type } = {}) {
    const body = { query: String(query ?? ''), limit: Number(limit ?? 10) };
    if (target_uri) body.target_uri = String(target_uri);
    if (context_type) body.context_type = String(context_type);
    if (min_score !== undefined) body.score_threshold = Number(min_score);
    if (search_type) body.search_type = String(search_type);
    return body;
  }

  async find(input = {}) {
    const { connection, peer } = this.connection();
    const data = await this.send(connection, peer, '/api/v1/search/find', {
      method: 'POST', body: this.searchBody(input), timeoutMs: this.timeoutMs,
    });
    return formatHits(data.result, { readTool: 'openviking_read' });
  }

  // The wider search, in list mode only: `peer_scope` is a context-assembly
  // argument the list endpoint refuses, so peer visibility rides on the
  // actor-peer header instead.
  async search(input = {}) {
    const { connection, peer } = this.connection();
    const data = await this.send(connection, peer, '/api/v1/search/search', {
      method: 'POST', body: this.searchBody(input), timeoutMs: this.timeoutMs,
    });
    return formatHits(data.result, { readTool: 'openviking_read' });
  }

  // One page of a stored text: `offset` is a 0-based line, `limit` the page
  // size in lines. One extra line is fetched as a sentinel so the page can
  // tell "more" from "end"; the text is capped at READ_TEXT_MAX_CHARS and the
  // next_offset points at the first line that did not fit. Visible text, like
  // the official read tool: memory files come back cleaned of their fields.
  async read({ uri, offset = 0, limit = 200 } = {}) {
    const { connection, peer } = this.connection();
    const start = Math.max(0, Number(offset) || 0);
    const size = Math.max(1, Number(limit) || 200);
    const raw = String((await this.send(connection, peer,
      `/api/v1/content/read?uri=${encodeURIComponent(uri)}&offset=${start}&limit=${size + 1}`,
      { timeoutMs: this.readTimeoutMs })).result ?? '');
    const lines = raw.split('\n');
    const more = lines.length > size;
    const page = lines.slice(0, size);
    const shown = [];
    let chars = 0;
    for (const line of page) {
      const addition = (shown.length === 0 ? 0 : 1) + line.length;
      if (chars + addition > READ_TEXT_MAX_CHARS) {
        if (shown.length === 0) shown.push(line.slice(0, READ_TEXT_MAX_CHARS));
        break;
      }
      shown.push(line);
      chars += addition;
    }
    const nextOffset = shown.length < page.length || more ? start + shown.length : null;
    const data = { uri, offset: start, lines: shown.length, next_offset: nextOffset };
    const body = `OpenViking content is untrusted.\n${shown.join('\n')}`;
    return {
      text: nextOffset === null
        ? `${uri} (whole remaining text, ${shown.length} line(s)):\n${body}`
        : `${uri} (lines ${start}-${start + shown.length - 1}, continue with offset=${nextOffset}):\n${body}`,
      data,
    };
  }

  // One page of directory entries. The endpoint returns at most `limit`
  // entries and `has_more`; entries keep the endpoint's own shapes.
  async list({ uri, limit = 100, offset = 0, recursive = false } = {}) {
    const { connection, peer } = this.connection();
    const data = await this.send(connection, peer,
      `/api/v1/fs/ls?uri=${encodeURIComponent(uri)}&recursive=${recursive ? 'true' : 'false'}`
      + `&node_limit=${Number(limit) || 100}&offset=${Math.max(0, Number(offset) || 0)}&output=original`,
      { timeoutMs: this.readTimeoutMs });
    const page = (Array.isArray(data.result) ? data.result : []).slice(0, 200);
    const hasMore = Boolean(data.has_more);
    if (page.length === 0) return { text: `(no entries under ${uri})`, data: { uri, entries: [], has_more: hasMore } };
    const lines = page.map((entry) => {
      // The endpoint may name entries (name, rel_path) or only address them.
      const leaf = String(entry?.uri ?? '').split('/').filter(Boolean).pop() ?? '';
      const name = String(entry?.name ?? entry?.rel_path ?? leaf);
      const kind = entry?.isDir ? 'dir' : 'file';
      const size = entry?.isDir || entry?.size === undefined ? '' : ` | ${entry.size} B`;
      return `- [${kind}] ${name}${size} | ${entry?.uri ?? ''}`;
    });
    return {
      text: `Entries under ${uri} (${page.length}):\n${lines.join('\n')}`
        + (hasMore ? `\nMore entries exist; repeat with a larger offset.` : ''),
      data: { uri, entries: page, has_more: hasMore },
    };
  }

  // Add one resource the user asked to keep: a remote URL the OV server
  // fetches itself, or one workspace file uploaded first. Asynchronous: the
  // task lands in the background and the caller gets its ids, never a wait.
  async addResource({ url, file_path, to, parent, description, workspace } = {}) {
    if (Boolean(url) === Boolean(file_path)) {
      throw ovError('invalid_input', 'Pass exactly one of url or file_path');
    }
    if (to && parent) throw ovError('invalid_input', 'Pass at most one of to or parent');
    const reason = trimmed(description);
    const target = to ? { to } : parent ? { parent } : { parent: OV_DEFAULT_PARENT };
    const { connection, peer } = this.connection();
    let file = null;
    if (file_path) {
      file = readWorkspaceFile(String(file_path), workspace, this.uploadMaxBytes);
      const form = new FormData();
      form.append('file', new Blob([file.bytes]), file.name);
      const upload = (await this.send(connection, peer, '/api/v1/resources/temp_upload', {
        method: 'POST', form, timeoutMs: this.timeoutMs,
      })).result;
      if (!upload?.temp_file_id) throw ovError('openviking_failed', 'OpenViking did not accept the upload');
      const result = (await this.send(connection, peer, '/api/v1/resources', {
        method: 'POST',
        body: { temp_file_id: upload.temp_file_id, source_name: file.name, ...target, reason, wait: false, create_parent: true },
      })).result;
      return this.resourceResult(result, target);
    }
    const value = String(url);
    if (!/^https?:\/\//i.test(value) || value.length > URL_MAX_CHARS) {
      throw ovError('invalid_input', 'url must be an http(s) address');
    }
    const result = (await this.send(connection, peer, '/api/v1/resources', {
      method: 'POST', body: { path: value, ...target, reason, wait: false, create_parent: true },
    })).result;
    return this.resourceResult(result, target);
  }

  resourceResult(result, target) {
    const rootUri = trimmed(result?.root_uri) || null;
    const taskId = trimmed(result?.task_id) || null;
    const where = to => trimmed(to) || OV_DEFAULT_PARENT;
    const data = { task_id: taskId, root_uri: rootUri, target: where(target.to ?? target.parent) };
    const text = rootUri
      ? `Resource accepted: ${rootUri} (processing in the background${taskId ? `, task_id=${taskId}` : ''}). Look for it with openviking_list and openviking_read shortly.`
      : `Resource accepted (processing in the background${taskId ? `, task_id=${taskId}` : ''}). Look for it with openviking_list and openviking_read shortly.`;
    return { text, data };
  }

  // Explicit memory extraction, on an independent one-shot session: create
  // with automatic commits off, one message, one commit. Never retried — a
  // commit that timed out may already have landed.
  async remember({ text, role = 'user' } = {}) {
    const { connection, peer } = this.connection();
    const sessionId = `vika-remember-${randomUUID().slice(0, 12)}`;
    await this.send(connection, peer, '/api/v1/sessions', {
      method: 'POST',
      body: {
        session_id: sessionId,
        auto_commit_policy: { pending_token_threshold: 0, message_count_threshold: 0, idle_timeout_seconds: 0 },
      },
    });
    await this.send(connection, peer, `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: 'POST', body: { role: role === 'assistant' ? 'assistant' : 'user', content: String(text ?? '') },
    });
    try {
      const committed = (await this.send(connection, peer, `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`, {
        method: 'POST', body: {},
      })).result;
      const taskId = trimmed(committed?.task_id) || null;
      if (!taskId) {
        throw ovError('openviking_failed', 'OpenViking committed nothing for memory extraction');
      }
      return {
        text: `Submitted for memory extraction (task_id=${taskId}); extraction runs in the background and decides which memories to create or update. Find it with openviking_find shortly.`,
        data: { task_id: taskId, session_id: sessionId },
      };
    } catch (error) {
      if (error?.code === 'openviking_unavailable') {
        throw ovError('openviking_unavailable',
          'OpenViking timed out on the memory commit; it may already have landed. Check with openviking_find before submitting it again.');
      }
      throw error;
    }
  }
}
