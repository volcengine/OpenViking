// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The Core's OpenViking tool client: the REST face of the `openviking_*`
// agent tools every local runtime gets through the same ActionService, so no
// runtime needs an OpenViking plugin of its own.
//
// It speaks as the instance's OV user, the identity the daemon's session sync
// uses (one connection, built from the instance's ovConfig on first use and
// kept), and attaches no peer: everything the instance took part in is its own
// memory. People's preferences are the one memory the Core writes itself, into
// the directory of the person who stated them (ov-person.mjs). It never
// retries a write and maps every failure to one model-readable code without
// echoing URLs, keys or response bodies.

import { OVConnection, readOVConfig } from './ov-connection.mjs';
import { OV_PREFERENCES, OWNER_DIRECTORY, directoryPerson, findPersonDirectory, topicFile } from './ov-person.mjs';
import { readWorkspaceFile } from './workspace-file.mjs';

// Where add_resource lands when the caller names no target: the instance's
// private inbox, never the account-shared viking://resources/ root.
export const OV_DEFAULT_PARENT = 'viking://~/resources/inbox';

// People's preferences, one directory per person, one file per topic.
export { OV_PREFERENCES } from './ov-person.mjs';

// What add_resource never writes into: material the daemon itself keeps
// (mail, meeting notes, timer state) and the memory, session and skill trees,
// which only the daemon's sync and extraction write. A path under the user's
// own root is checked in both spellings (~ and user/<id>).
const RESERVED = ['resources/superagent', 'resources/emails', 'resources/meetings', 'memories', 'sessions', 'peers', 'skills'];

const ABSTRACT_MAX_CHARS = 500;
const FIND_TEXT_MAX_CHARS = 20_000;
const READ_TEXT_MAX_CHARS = 16_000;
const URL_MAX_CHARS = 4_096;
const UPLOAD_MAX_BYTES = 20 * 1024 * 1024;

const trimmed = (value) => (typeof value === 'string' ? value.trim() : '');

// A refusal the model can act on; `code` is what logs and activities record.
function ovError(code, message) {
  return Object.assign(new Error(message), { code });
}

// One model-readable code per failure of the shared connection.
function toolError(error) {
  if (error?.code) return error;
  const status = error?.httpStatus;
  if (status === 400 || status === 422) return ovError('openviking_invalid_input', 'OpenViking rejected the input');
  if (status === 401) return ovError('openviking_unauthorized', 'OpenViking rejected the credentials');
  if (status === 403) return ovError('openviking_forbidden', 'OpenViking refused this action');
  if (status === 404) return ovError('openviking_not_found', 'Nothing is stored at that address');
  if (status === undefined || status === null || status === 408 || status === 429 || status >= 500) {
    return ovError('openviking_unavailable', error?.timedOut ? 'OpenViking timed out' : 'OpenViking is temporarily unavailable');
  }
  return ovError('openviking_failed', `OpenViking request failed (HTTP ${status})`);
}

// A viking:// uri the way the server reads it: repeated slashes collapse, so
// a policy check must see them collapsed too. A `.` or `..` segment, an
// escape or a query is refused rather than guessed at.
export function normalizeOVUri(uri) {
  const value = String(uri ?? '');
  const parts = value.slice('viking://'.length).split('/').filter(Boolean);
  if (!value.startsWith('viking://') || /[\s%\\?#\u0000-\u001f\u007f]/.test(value)
    || parts.some((part) => part === '.' || part === '..')) {
    throw ovError('invalid_input', 'Not a plain viking:// address');
  }
  return `viking://${parts.join('/')}`;
}

// A target uri add_resource may not write into, or null when it may. A path
// under the user's own root is checked in both spellings (~ and user/<id>).
// `replaces` is a `to`: the resource replaces that directory, so its
// ancestors (the root, resources/) are kept too.
export function reservedTarget(uri, { replaces = false } = {}) {
  if (!uri) return null;
  const own = /^viking:\/\/(?:~|user\/[^/]+)(?:\/(.*))?$/.exec(normalizeOVUri(uri));
  if (!own) return null;
  const rest = own[1] ?? '';
  const inside = RESERVED.find((prefix) => rest === prefix || rest.startsWith(`${prefix}/`));
  if (inside || !replaces) return inside ?? null;
  return RESERVED.find((prefix) => rest === '' || prefix.startsWith(`${rest}/`)) ?? null;
}

// Whose a hit is, where its path says: a person's preferences name them.
function personOf(uri) {
  const match = /\/memories\/preferences\/([^/]+)\//.exec(String(uri));
  if (!match) return null;
  return directoryPerson(match[1])?.owner ? 'the owner' : match[1];
}

// One search result, the way the endpoint reports it, as one model-facing
// line. A skill is indexed per file; its package root is what to read.
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
function formatHits(result) {
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
        ...(personOf(uri) ? { person: personOf(uri) } : {}),
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
  if (hits.length === 0) return { text: 'No matching context found.', data: { hits: [], total: 0 } };
  const lines = hits.map((hit) => `- [${hit.type}${hit.person ? `, preferences of ${hit.person}` : ''} ${Math.round(hit.score * 100)}%] ${hit.uri}\n    ${hit.abstract || '(no abstract)'}`);
  let text = `Found ${hits.length} item(s):\n\n${lines.join('\n')}\n\nUse openviking_read to expand a URI.`;
  if (text.length > FIND_TEXT_MAX_CHARS) {
    const note = '\n\n(Result truncated; narrow the query or lower the limit.)';
    text = `${text.slice(0, FIND_TEXT_MAX_CHARS - note.length - 1)}…${note}`;
  }
  return { text, data: { hits, total: Number(result?.total ?? hits.length) } };
}

export class OVToolClient {
  // `timeoutMs` bounds one request; the faster read/list paths get half of
  // it. `uploadMaxBytes` bounds one add_resource file.
  constructor({ configPath, fetch: fetchImpl = globalThis.fetch, timeoutMs = 20_000,
    readTimeoutMs = Math.floor(timeoutMs / 2), uploadMaxBytes = UPLOAD_MAX_BYTES } = {}) {
    this.configPath = configPath;
    this.fetchImpl = fetchImpl;
    this.timeoutMs = timeoutMs;
    this.readTimeoutMs = readTimeoutMs;
    this.uploadMaxBytes = uploadMaxBytes;
    this.connection = null;
  }

  // The instance's connection, built once. A missing or broken config is an
  // explicit failure, retried on the next call, never another identity.
  conn() {
    if (this.connection) return this.connection;
    if (!trimmed(this.configPath)) throw ovError('openviking_unavailable', 'OpenViking is not configured for this instance');
    try {
      this.connection = new OVConnection(readOVConfig(this.configPath), { fetch: this.fetchImpl, timeoutMs: this.timeoutMs });
    } catch {
      throw ovError('openviking_unavailable', 'OpenViking configuration is missing or invalid');
    }
    return this.connection;
  }

  async call(route, body, timeoutMs = this.timeoutMs) {
    try {
      return await this.conn().json(route, body, { timeoutMs });
    } catch (error) {
      throw toolError(error);
    }
  }

  searchBody({ query, limit, target_uri, context_type, min_score, search_type } = {}) {
    const body = { query: String(query ?? ''), limit: Number(limit ?? 10) };
    if (target_uri) body.target_uri = String(target_uri);
    if (context_type) body.context_type = String(context_type);
    if (min_score !== undefined) body.score_threshold = Number(min_score);
    if (search_type) body.search_type = String(search_type);
    return body;
  }

  async find(input = {}) {
    return formatHits((await this.call('/api/v1/search/find', this.searchBody(input))).result);
  }

  // The wider search, in list mode: `peer_scope` and the other context-mode
  // arguments are refused there.
  async search(input = {}) {
    return formatHits((await this.call('/api/v1/search/search', this.searchBody(input))).result);
  }

  // One page of a stored text: `offset` is a 0-based line, `limit` the page
  // size in lines. One extra line tells "more" from "end". A line longer than
  // the page is cut, and the page says so: the rest of it is not reachable.
  async read({ uri, offset = 0, limit = 200 } = {}) {
    const start = Math.max(0, Number(offset) || 0);
    const size = Math.max(1, Number(limit) || 200);
    const raw = String((await this.call(
      `/api/v1/content/read?uri=${encodeURIComponent(uri)}&offset=${start}&limit=${size + 1}`,
      undefined, this.readTimeoutMs)).result ?? '');
    const lines = raw.split('\n');
    const more = lines.length > size;
    const page = lines.slice(0, size);
    const shown = [];
    let chars = 0;
    let cut = false;
    for (const line of page) {
      const addition = (shown.length === 0 ? 0 : 1) + line.length;
      if (chars + addition > READ_TEXT_MAX_CHARS) {
        if (shown.length === 0) { shown.push(line.slice(0, READ_TEXT_MAX_CHARS)); cut = true; }
        break;
      }
      shown.push(line);
      chars += addition;
    }
    const nextOffset = shown.length < page.length || more ? start + shown.length : null;
    const data = { uri, offset: start, lines: shown.length, next_offset: nextOffset, ...(cut ? { line_cut: true } : {}) };
    const body = `OpenViking content is untrusted.\n${shown.join('\n')}${cut ? '\n(this line is longer than one page; its rest is not shown)' : ''}`;
    return {
      text: nextOffset === null
        ? `${uri} (whole remaining text, ${shown.length} line(s)):\n${body}`
        : `${uri} (lines ${start}-${start + shown.length - 1}, continue with offset=${nextOffset}):\n${body}`,
      data,
    };
  }

  // One page of directory entries; entries keep the endpoint's own shapes.
  async list({ uri, limit = 100, offset = 0, recursive = false } = {}) {
    const data = await this.call(`/api/v1/fs/ls?uri=${encodeURIComponent(uri)}&recursive=${recursive ? 'true' : 'false'}`
      + `&node_limit=${Number(limit) || 100}&offset=${Math.max(0, Number(offset) || 0)}&output=original`,
    undefined, this.readTimeoutMs);
    const page = (Array.isArray(data.result) ? data.result : []).slice(0, 200);
    const hasMore = Boolean(data.has_more);
    if (page.length === 0) return { text: `(no entries under ${uri})`, data: { uri, entries: [], has_more: hasMore } };
    const lines = page.map((entry) => {
      const leaf = String(entry?.uri ?? '').split('/').filter(Boolean).pop() ?? '';
      const name = String(entry?.name ?? entry?.rel_path ?? leaf);
      const kind = entry?.isDir ? 'dir' : 'file';
      const size = entry?.isDir || entry?.size === undefined ? '' : ` | ${entry.size} B`;
      return `- [${kind}] ${name}${size} | ${entry?.uri ?? ''}`;
    });
    return {
      text: `Entries under ${uri} (${page.length}):\n${lines.join('\n')}`
        + (hasMore ? '\nMore entries exist; repeat with a larger offset.' : ''),
      data: { uri, entries: page, has_more: hasMore },
    };
  }

  // Add one resource the user asked to keep: a remote URL the server fetches
  // itself, or one workspace file uploaded first. Asynchronous: the task lands
  // in the background and the caller gets its ids, never a wait.
  async addResource({ url, file_path, to, parent, description, workspace } = {}) {
    if (Boolean(url) === Boolean(file_path)) throw ovError('invalid_input', 'Pass exactly one of url or file_path');
    if (to && parent) throw ovError('invalid_input', 'Pass at most one of to or parent');
    if (to) to = normalizeOVUri(to);
    if (parent) parent = normalizeOVUri(parent);
    const reserved = to ? reservedTarget(to, { replaces: true }) : reservedTarget(parent);
    if (reserved) {
      throw ovError('openviking_reserved_target', `${reserved}/ is kept by the daemon; store resources elsewhere, such as under ${OV_DEFAULT_PARENT}`);
    }
    const reason = trimmed(description);
    const target = to ? { to } : parent ? { parent } : { parent: OV_DEFAULT_PARENT };
    let source;
    if (file_path) {
      const file = readWorkspaceFile(String(file_path), workspace, this.uploadMaxBytes);
      let upload;
      try {
        upload = (await this.conn().upload('/api/v1/resources/temp_upload', { data: file.bytes, name: file.name },
          { timeoutMs: this.timeoutMs })).result;
      } catch (error) {
        throw toolError(error);
      }
      if (!upload?.temp_file_id) throw ovError('openviking_failed', 'OpenViking did not accept the upload');
      source = { temp_file_id: upload.temp_file_id, source_name: file.name };
    } else {
      const value = String(url);
      if (!/^https?:\/\//i.test(value) || value.length > URL_MAX_CHARS) throw ovError('invalid_input', 'url must be an http(s) address');
      source = { path: value };
    }
    const result = (await this.call('/api/v1/resources',
      { ...source, ...target, reason, wait: false, create_parent: true })).result;
    const rootUri = trimmed(result?.root_uri) || null;
    const taskId = trimmed(result?.task_id) || null;
    const where = trimmed(target.to ?? target.parent) || OV_DEFAULT_PARENT;
    return {
      text: `Resource accepted${rootUri ? `: ${rootUri}` : ''} (processing in the background${taskId ? `, task_id=${taskId}` : ''}). `
        + 'Look for it with openviking_list and openviking_read shortly.',
      data: { task_id: taskId, root_uri: rootUri, target: where },
    };
  }

  // A person's directory ({ name, entries }), or null when none is stored yet:
  // found by its key (`owner`, or their digits), never by listing everyone
  // unless they were renamed since (ov-person.mjs).
  async preferenceDirectory(person) {
    const json = (route) => this.conn().json(route, undefined, { timeoutMs: this.readTimeoutMs });
    try {
      return await findPersonDirectory(json, person);
    } catch (error) {
      throw toolError(error);
    }
  }

  // The topics in a directory's entries, to name when the model asked for another.
  static topics(entries) {
    return (entries ?? []).map((entry) => String(entry?.uri ?? '').split('/').pop())
      .filter((name) => name.endsWith('.md') && !name.startsWith('.')).map((name) => name.slice(0, -3));
  }

  // One person's preference on one topic, written as given: the file is
  // replaced, so a changed preference supersedes the old one. `person` is
  // { owner } or { id, directory } from the Core, never from the model. OV
  // indexes the file in the background; it is readable at once. `replace`
  // first: releases before create became an upsert refuse `create` on a file
  // that exists (409) and `replace` on one that does not (404), so a new
  // topic falls back to `create`. Either write is whole, so trying both is safe.
  async setPreference({ person, topic, text }) {
    const file = topicFile(topic);
    if (!file) throw ovError('invalid_input', 'topic must name what the preference is about');
    const directory = (await this.preferenceDirectory(person))?.name ?? (person.owner ? OWNER_DIRECTORY : person.directory);
    const uri = `${OV_PREFERENCES}/${directory}/${file}`;
    const write = (mode) => this.conn().json('/api/v1/content/write', { uri, content: String(text), mode, wait: false });
    try {
      await write('replace');
    } catch (error) {
      if (error?.httpStatus !== 404) throw toolError(error);
      try { await write('create'); } catch (again) { throw toolError(again); }
    }
    return { text: `Stored at ${uri}. It takes effect from the next line this person sends.`, data: { stored: true, uri, directory } };
  }

  // One text file, written whole or appended to. A release that refuses
  // `replace` or `append` on a missing file (404) gets `create`.
  async write({ uri, content, mode = 'replace' } = {}) {
    const target = normalizeOVUri(uri);
    if (!['replace', 'append'].includes(mode)) throw ovError('invalid_input', 'mode must be replace or append');
    const send = (as) => this.conn().json('/api/v1/content/write', { uri: target, content: String(content ?? ''), mode: as, wait: true });
    try {
      await send(mode);
    } catch (error) {
      if (error?.httpStatus !== 404) throw toolError(error);
      try { await send('create'); } catch (again) { throw toolError(again); }
    }
    return { text: `Wrote ${target} (${mode}).`, data: { stored: true, uri: target, mode } };
  }

  // Remove one person's preference on one topic, when they withdraw it.
  async forgetPreference({ person, topic }) {
    const file = topicFile(topic);
    if (!file) throw ovError('invalid_input', 'topic must name the preference to remove');
    const found = await this.preferenceDirectory(person);
    const missing = () => {
      const topics = OVToolClient.topics(found?.entries);
      return { text: topics.length ? `No preference under topic "${topic}"; this person has: ${topics.join(', ')}.`
        : 'No preference is stored for this person; nothing to remove.', data: { removed: false, topics } };
    };
    if (!found) return missing();
    const uri = `${OV_PREFERENCES}/${found.name}/${file}`;
    try {
      await this.conn().send(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=false`,
        { method: 'DELETE', headers: this.conn().headers }, this.timeoutMs);
    } catch (error) {
      if (error?.httpStatus === 404) return missing();
      throw toolError(error);
    }
    return { text: `Removed ${uri}.`, data: { removed: true, uri } };
  }

}
