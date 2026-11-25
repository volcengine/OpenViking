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

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { unzipSync } from 'fflate';
import { parse as parseYaml } from 'yaml';
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
const SKILL_CACHE = '.openviking-skills';
const SKILL_NAME = /^[A-Za-z0-9_-]+$/;

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

function inlineSkill(data) {
  const value = String(data);
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(value);
  if (!match) throw ovError('invalid_input', 'SKILL.md must start with YAML frontmatter');
  let metadata;
  try { metadata = parseYaml(match[1]); } catch {
    throw ovError('invalid_input', 'SKILL.md frontmatter must be valid YAML');
  }
  const name = metadata?.name;
  if (typeof name !== 'string') throw ovError('invalid_input', 'SKILL.md name is required');
  if (!SKILL_NAME.test(name)) throw ovError('invalid_input', 'SKILL.md name must use only letters, numbers, underscore or hyphen');
  if (typeof metadata?.description !== 'string' || !metadata.description.trim()) {
    throw ovError('invalid_input', 'SKILL.md description is required');
  }
}

function validateSkillArchive(bytes, maxBytes) {
  let total = 0;
  let entries = 0;
  let definitions;
  try {
    definitions = unzipSync(bytes, {
      filter(file) {
        const relative = file.name.replace(/\/$/, '');
        if (!relative || /^[\\/]/.test(relative) || /[\\\u0000-\u001f]/.test(relative)
          || /^[A-Za-z]:/.test(relative)
          || relative.split('/').some((segment) => !segment || segment === '.' || segment === '..')) {
          throw new Error('unsafe path');
        }
        total += file.originalSize;
        entries += 1;
        if (file.originalSize > maxBytes || total > 64 * 1024 * 1024 || entries > 512) {
          throw new Error('package too large');
        }
        return relative.split('/').at(-1) === 'SKILL.md';
      },
    });
  } catch {
    throw ovError('invalid_input', 'Skill package must be a valid, size-bounded ZIP with safe paths');
  }
  const contents = Object.values(definitions);
  if (!contents.length) throw ovError('invalid_input', 'Skill package must contain SKILL.md');
  for (const content of contents) inlineSkill(Buffer.from(content).toString('utf8'));
}

function pruneSkillCache(directory, kept, prefix = '') {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    const target = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) throw ovError('invalid_input', 'Skill cache contains an unsafe directory');
    if (entry.isDirectory()) {
      pruneSkillCache(target, kept, relative);
      if (!fs.readdirSync(target).length) fs.rmdirSync(target);
    } else if (!kept.has(relative)) {
      fs.unlinkSync(target);
    }
  }
}

function skillAddress(uri) {
  const value = normalizeOVUri(uri);
  const match = /^viking:\/\/(?:~|user\/[^/]+)\/skills\/([A-Za-z0-9_-]+)(?:\/(.+))?$/.exec(value);
  if (!match) throw ovError('invalid_input', 'Download is limited to files under your OpenViking Skill directory');
  return { uri: value, name: match[1], relative: match[2] ?? null };
}

function cacheRoot(workspace) {
  const root = fs.realpathSync(workspace);
  const cache = path.join(root, SKILL_CACHE);
  if (!fs.existsSync(cache)) fs.mkdirSync(cache, { mode: 0o700 });
  const stat = fs.lstatSync(cache);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw ovError('invalid_input', 'Skill cache must be a private directory');
  return cache;
}

function privateDirectory(root, relative) {
  let current = root;
  for (const segment of relative.split('/').filter(Boolean)) {
    current = path.join(current, segment);
    if (!fs.existsSync(current)) fs.mkdirSync(current, { mode: 0o700 });
    const stat = fs.lstatSync(current);
    if (!stat.isDirectory() || stat.isSymbolicLink()) {
      throw ovError('invalid_input', 'Skill cache contains an unsafe directory');
    }
  }
  return current;
}

function cacheFile(root, skill, relative, bytes, digest) {
  const target = path.resolve(root, skill, ...relative.split('/'));
  const skillRoot = path.resolve(root, skill);
  if (!target.startsWith(`${skillRoot}${path.sep}`)) throw ovError('invalid_input', 'Skill file path is invalid');
  privateDirectory(root, [skill, ...relative.split('/').slice(0, -1)].join('/'));
  if (fs.existsSync(target)) {
    const stat = fs.lstatSync(target);
    if (!stat.isFile() || stat.isSymbolicLink()) throw ovError('invalid_input', 'Skill cache target is not a regular file');
    const current = crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex');
    if (current === digest) return { path: target, cached: true };
  }
  const temporary = `${target}.tmp-${process.pid}-${crypto.randomBytes(6).toString('hex')}`;
  fs.writeFileSync(temporary, bytes, { mode: 0o600, flag: 'wx' });
  fs.renameSync(temporary, target);
  return { path: target, cached: false };
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

  async download({ uri, skill, workspace } = {}) {
    if (Boolean(uri) === Boolean(skill)) throw ovError('invalid_input', 'Pass exactly one of uri or skill');
    if (!trimmed(workspace)) throw ovError('invalid_input', 'A workspace is required');
    let requested;
    if (skill) {
      const name = trimmed(skill);
      if (!SKILL_NAME.test(name)) throw ovError('invalid_input', 'skill must use only letters, numbers, underscore or hyphen');
      requested = { name, relative: null, uri: `viking://~/skills/${name}` };
    } else {
      requested = skillAddress(uri);
    }
    const targetRoot = requested.uri.slice(0, requested.uri.indexOf(`/skills/${requested.name}`) + '/skills'.length);
    const detail = (await this.call(`/api/v1/skills/${encodeURIComponent(requested.name)}`
      + `?target_uri=${encodeURIComponent(targetRoot)}&include_content=false&include_files=true&include_integrity=true`)).result;
    const returnedRoot = String(detail?.root_uri ?? '');
    const privateRoot = requested.uri.startsWith('viking://~/')
      ? new RegExp(`^viking://user/[^/]+/skills/${requested.name}$`).test(returnedRoot)
      : returnedRoot === `${targetRoot}/${requested.name}`;
    if (!privateRoot) throw ovError('openviking_forbidden', 'OpenViking did not return the requested private Skill');
    const files = (Array.isArray(detail?.files) ? detail.files : [])
      .filter((entry) => !entry?.is_dir && entry?.path && entry?.uri && entry?.sha256)
      .filter((entry) => !requested.relative || entry.path === requested.relative);
    if (!files.length) throw ovError('openviking_not_found', 'No matching Skill file was found');
    const root = cacheRoot(workspace);
    const landed = [];
    for (const file of files) {
      const relative = String(file.path);
      if (path.posix.isAbsolute(relative) || relative.split('/').some((part) => !part || part === '.' || part === '..')) {
        throw ovError('invalid_input', 'OpenViking returned an invalid Skill file path');
      }
      privateDirectory(root, [requested.name, ...relative.split('/').slice(0, -1)].join('/'));
      const target = path.resolve(root, requested.name, ...relative.split('/'));
      let result;
      if (fs.existsSync(target) && fs.lstatSync(target).isFile() && !fs.lstatSync(target).isSymbolicLink()) {
        const digest = crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex');
        if (digest === file.sha256) result = { path: target, cached: true };
      }
      if (!result) {
        let bytes;
        try {
          bytes = await this.conn().bytes(`/api/v1/content/download?uri=${encodeURIComponent(file.uri)}`, {
            timeoutMs: this.timeoutMs, maxBytes: this.uploadMaxBytes,
          });
        } catch (error) {
          throw toolError(error);
        }
        const digest = crypto.createHash('sha256').update(bytes).digest('hex');
        if (digest !== file.sha256) throw ovError('openviking_failed', 'Downloaded Skill file failed its integrity check');
        result = cacheFile(root, requested.name, relative, bytes, digest);
      }
      landed.push({ uri: file.uri, path: result.path, sha256: file.sha256, cached: result.cached });
    }
    if (!requested.relative) {
      pruneSkillCache(path.join(root, requested.name), new Set(files.map((file) => String(file.path))));
    }
    const output = requested.relative ? landed[0].path : path.join(root, requested.name);
    return {
      text: `${landed.length === 1 ? 'Downloaded 1 Skill file' : `Synced ${landed.length} Skill files`} to ${output}.`,
      data: { path: output, files: landed, revision: detail?.revision ?? null, downloaded: true },
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

  async addSkill({ data, git_url, file_path, skills, workspace } = {}) {
    const sources = [trimmed(data), trimmed(git_url), trimmed(file_path)].filter(Boolean);
    if (sources.length !== 1) {
      throw ovError('invalid_input', 'Pass exactly one of data, git_url or file_path');
    }
    const selected = Array.isArray(skills) && skills.length ? { skills: skills.map(String) } : {};
    let source;
    if (file_path) {
      if (!/\.(?:zip|skill)$/i.test(String(file_path))) {
        throw ovError('invalid_input', 'file_path must name a .zip or .skill package');
      }
      const file = readWorkspaceFile(String(file_path), workspace, this.uploadMaxBytes);
      validateSkillArchive(file.bytes, this.uploadMaxBytes);
      let upload;
      try {
        upload = (await this.conn().upload('/api/v1/resources/temp_upload', {
          data: file.bytes, name: file.name,
        }, { timeoutMs: this.timeoutMs })).result;
      } catch (error) {
        throw toolError(error);
      }
      if (!upload?.temp_file_id) throw ovError('openviking_failed', 'OpenViking did not accept the upload');
      source = { temp_file_id: upload.temp_file_id };
    } else if (git_url) {
      const value = String(git_url);
      if (!/^https?:\/\/\S+$/i.test(value) || value.length > URL_MAX_CHARS) {
        throw ovError('invalid_input', 'git_url must be a supported Git repository address');
      }
      source = { data: value };
    } else {
      inlineSkill(data);
      source = { data: String(data) };
    }
    const result = (await this.call('/api/v1/skills', {
      ...source, ...selected, wait: false,
    })).result;
    const installed = Array.isArray(result?.installed) ? result.installed : [result];
    const rootUris = installed.map((item) => trimmed(item?.root_uri)).filter(Boolean);
    const taskIds = installed.map((item) => trimmed(item?.task_id)).filter(Boolean);
    const rootUri = rootUris[0] ?? null;
    const taskId = taskIds[0] ?? null;
    const count = rootUris.length || installed.length;
    return {
      text: `${count === 1 ? 'Skill' : `${count} Skills`} accepted${rootUri ? `: ${rootUris.join(', ')}` : ''}`
        + `${taskId ? ` (task_id=${taskIds.join(', ')})` : ''}. `
        + 'It will be available for discovery after OpenViking finishes indexing it.',
      data: {
        task_id: taskId, root_uri: rootUri, task_ids: taskIds, root_uris: rootUris,
      },
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
