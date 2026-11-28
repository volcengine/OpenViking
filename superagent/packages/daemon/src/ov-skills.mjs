// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// OpenViking's agent skills over the same REST face every runtime reaches
// through the Core: list, find, show, install, update, remove and validate,
// plus the catalog the daemon shows a session. OpenViking's own plugin does
// this from inside the host; OpenViking SuperAgent starts its runtimes without
// that plugin, so the Core speaks for it.
//
// What the server offers (openviking/server/routers/skills.py on the release
// this ships with): GET /api/v1/skills merges the caller's own skills and the
// account-shared viking://agent/skills; /find and /validate are POSTs;
// /{name} is GET, PUT and DELETE. A local skill (a SKILL.md, a directory or a
// .zip) travels as a temp upload, a Git URL or inline SKILL.md text as `data`.
// Installing under an existing name replaces its package; `from_source`
// refetches the Git source recorded at install time. Nothing here is retried:
// an install the server took must not be reported failed.

import fs from 'node:fs';
import path from 'node:path';
import { OVConnection, readOVConfig } from './ov-connection.mjs';
import { ovError, toolError } from './ov-tools.mjs';
import { readWorkspaceFile } from './workspace-file.mjs';

// Where a skill goes when the caller names no root: the instance's own skills,
// the tree recall reads. viking://agent/skills is the account-shared one.
export const OV_SKILL_ROOT = 'viking://~/skills';
export const OV_SHARED_SKILL_ROOT = 'viking://agent/skills';

// One catalog, the way OpenViking's plugin sizes it: 200 roots from one list
// call, each description cut to 40 tokens, the whole block within 1200.
export const SKILL_CATALOG_LIMIT = 200;
export const SKILL_CATALOG_TOKENS = 1_200;
const SKILL_DESCRIPTION_TOKENS = 40;
const SKILL_MORE_HINT = 'search OpenViking skills to find the rest';
const SKILL_USAGE_LINE = '  OpenViking skills (stored in OpenViking, not local files). Before following one, read <dir>/<name>/SKILL.md with openviking_read.';

const SHOW_CONTENT_MAX_CHARS = 8_000;
const LIST_TEXT_MAX_CHARS = 20_000;
const UPLOAD_MAX_BYTES = 20 * 1024 * 1024;
const TREE_MAX_FILES = 500;

const trimmed = (value) => (typeof value === 'string' ? value.trim() : '');

// A skill root the server accepts, in the caller's spelling: its own skills or
// the account-shared one. '' means the caller's own.
export function normalizeSkillRoot(uri) {
  const value = trimmed(uri);
  if (!value) return OV_SKILL_ROOT;
  const collapsed = `viking://${value.slice('viking://'.length).split('/').filter(Boolean).join('/')}`;
  if (!value.startsWith('viking://') || /[\s%\\?#\u0000-\u001f\u007f]/.test(value)
    || collapsed.split('/').length < 4) {
    throw ovError('invalid_input', 'target_uri must be a skill root: viking://~/skills or viking://agent/skills');
  }
  if (collapsed === OV_SHARED_SKILL_ROOT) return OV_SHARED_SKILL_ROOT;
  if (/^viking:\/\/(?:~|user\/[^/]+)\/skills$/.test(collapsed)) return collapsed;
  throw ovError('invalid_input', 'target_uri must be a skill root: viking://~/skills or viking://agent/skills');
}

// OV limits a skill name to ASCII letters, digits, '_' and '-' (skill name is
// the package directory). Checked here so a bad name fails before an upload.
const SKILL_NAME = /^[A-Za-z0-9_-]{1,64}$/;

export function checkSkillName(name) {
  const value = trimmed(name);
  if (!SKILL_NAME.test(value)) throw ovError('invalid_input', 'name must be the skill name: ASCII letters, digits, _ or - (at most 64)');
  return value;
}

// The token estimate OpenViking's plugin budgets with: CJK counts 1.5, the
// rest a quarter per character. It overcounts rather than undercounts.
export function estimateTokens(text) {
  if (!text) return 0;
  let cjk = 0;
  for (let index = 0; index < text.length; index += 1) {
    if (text.charCodeAt(index) >= 0x3000) cjk += 1;
  }
  return Math.ceil(cjk * 1.5 + (text.length - cjk) / 4);
}

// Skill descriptions are written by whoever installed the skill, and
// viking://agent/skills is account-wide: a description must not close the
// envelope the catalog sits in, nor run over a line.
export function sanitizeInline(text) {
  return String(text ?? '')
    .replace(/<\/?(?:openviking-context|available-skills|available-memories|user-profile|memory|skill)\b[^>]*>/gi, ' ')
    .replace(/[\u0000-\u001f\u007f]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function truncateToTokens(text, maxTokens) {
  if (estimateTokens(text) <= maxTokens) return text;
  // Roughly the chars that fit: 1.5 tokens per CJK char, 0.25 per other.
  const budget = Math.max(1, Math.floor(maxTokens * 2));
  return `${text.slice(0, budget - 1).trimEnd()}…`;
}

// A skill summary names its package, never the skills root it sits under:
// `uri` and `root_uri` are both <root>/<name>, and <root> is that package's
// parent (OpenViking's plugin reads the catalog the same way).
const packageOf = (skill) => String(skill?.root_uri ?? skill?.uri ?? '').replace(/\/+$/, '');
const skillsRootOf = (skill) => {
  const uri = packageOf(skill);
  return uri.includes('/') ? uri.slice(0, uri.lastIndexOf('/')) : '';
};

// One group of the catalog, the way OpenViking's plugin lists it: the root as
// a header, each skill as a relative name and its description while the
// budget lasts, then a tail that says how many were left out.
function formatSkillGroup(root, entries, budgetTokens) {
  if (entries.length === 0) return { lines: [], used: 0, dropped: 0 };
  const header = `  ${root}/`;
  const headerTokens = estimateTokens(header);
  const stub = () => {
    const line = `  ${root}/  (${entries.length} entries, budget too tight; ${SKILL_MORE_HINT})`;
    return { lines: [line], used: estimateTokens(line), dropped: entries.length };
  };
  if (headerTokens > budgetTokens) return stub();
  const lines = [header];
  let used = headerTokens;
  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    const line = `    - ${entry.name}${entry.abstract ? ` — ${entry.abstract}` : ''}`;
    const tokens = estimateTokens(line);
    if (used + tokens <= budgetTokens) {
      lines.push(line);
      used += tokens;
      continue;
    }
    let remaining = entries.length - index;
    const tailFor = (count) => `    ... +${count} more, ${SKILL_MORE_HINT}`;
    while (used + estimateTokens(tailFor(remaining)) > budgetTokens && lines.length > 1) {
      used -= estimateTokens(lines.pop());
      remaining += 1;
    }
    if (lines.length === 1) return stub();
    const tail = tailFor(remaining);
    const tailTokens = estimateTokens(tail);
    if (used + tailTokens <= budgetTokens) {
      lines.push(tail);
      return { lines, used: used + tailTokens, dropped: remaining };
    }
    return { lines, used, dropped: remaining };
  }
  return { lines, used, dropped: 0 };
}

function renderSkillGroups(groups, budgetTokens, withDescriptions) {
  const lists = groups.map((group) => (withDescriptions
    ? group
    : group.map((entry) => ({ ...entry, abstract: '' }))));
  // A group takes its fair share of what is left, or everything the later
  // groups leave when listed in full, whichever is larger.
  const fullCost = lists.map((entries) => formatSkillGroup(groupRoot(entries), entries, Infinity).used);
  const lines = [];
  let used = 0;
  let dropped = 0;
  for (let index = 0; index < lists.length; index += 1) {
    const remaining = budgetTokens - used;
    const laterCost = fullCost.slice(index + 1).reduce((total, cost) => total + cost, 0);
    const share = Math.max(Math.floor(remaining / (lists.length - index)), remaining - laterCost);
    const block = formatSkillGroup(groupRoot(lists[index]), lists[index], share);
    lines.push(...block.lines);
    used += block.used;
    dropped += block.dropped;
  }
  return { lines, used, dropped };
}

// The skills root a group of entries sits under (every entry in a group has
// the same one).
function groupRoot(entries) {
  return entries[0]?.root || OV_SKILL_ROOT;
}

// <available-skills> for one session, within its own token budget: every
// skill with its description when that fits, otherwise names only (so more
// skills stay visible, with a "+N more" tail), and one line with the count
// when not even one name fits. '' when there are no skills at all.
export function renderSkillCatalog(skills, { budgetTokens = SKILL_CATALOG_TOKENS } = {}) {
  const own = [];
  const shared = [];
  for (const skill of Array.isArray(skills) ? skills : []) {
    const name = trimmed(skill?.name);
    const root = skillsRootOf(skill);
    if (!name || !root.includes('/')) continue;
    const entry = { name, root, abstract: truncateToTokens(sanitizeInline(skill.description), SKILL_DESCRIPTION_TOKENS) };
    (root === OV_SHARED_SKILL_ROOT ? shared : own).push(entry);
  }
  const ownNames = new Set(own.map((entry) => entry.name));
  const byName = (a, b) => a.name.localeCompare(b.name);
  const groups = [own.sort(byName), shared.filter((entry) => !ownNames.has(entry.name)).sort(byName)]
    .filter((group) => group.length > 0);
  const count = groups.reduce((total, group) => total + group.length, 0);
  if (count === 0) return '';
  const open = '<available-skills>';
  const close = '</available-skills>';
  const frameTokens = estimateTokens(open) + estimateTokens(SKILL_USAGE_LINE) + estimateTokens(close);
  const listingBudget = Math.max(0, budgetTokens - frameTokens);
  let body = renderSkillGroups(groups, listingBudget, true);
  if (body.dropped > 0) body = renderSkillGroups(groups, listingBudget, false);
  if (body.dropped >= count) {
    const stub = `${open}${count} OpenViking skills; ${SKILL_MORE_HINT}.${close}`;
    return estimateTokens(stub) > budgetTokens ? '' : stub;
  }
  return [open, SKILL_USAGE_LINE, ...body.lines, close].join('\n');
}

// One skill as one model-facing line: what it is, where its package sits and
// which file to read before following it.
function skillLine(skill, { withScore = false } = {}) {
  const name = trimmed(skill?.name) || '?';
  const root = packageOf(skill);
  const md = trimmed(skill?.skill_md_uri) || (root ? `${root}/SKILL.md` : '');
  const description = sanitizeInline(skill?.description);
  const score = withScore && Number.isFinite(Number(skill?.score)) ? ` ${Math.round(Number(skill.score) * 100)}%` : '';
  return `- ${name}${score}${description ? `: ${description}` : ''}${md ? ` (${md})` : ''}`;
}

function formatList(result, targetUri) {
  const skills = Array.isArray(result?.skills) ? result.skills : [];
  if (skills.length === 0) {
    return {
      text: `No skills are installed${targetUri ? ` under ${targetUri}` : ''}. Install one with openviking_skill_add.`,
      data: { skills: [], total: 0, roots: result?.root_uris ?? (targetUri ? [targetUri] : []) },
    };
  }
  const lines = skills.map((skill) => skillLine(skill));
  let text = `Skills (${skills.length}):\n${lines.join('\n')}\n\nRead one with openviking_skill_show or openviking_read; search them with openviking_skill_find.`;
  if (text.length > LIST_TEXT_MAX_CHARS) text = `${text.slice(0, LIST_TEXT_MAX_CHARS - 1)}…`;
  return { text, data: { skills, total: skills.length, roots: result?.root_uris ?? (targetUri ? [targetUri] : []) } };
}

function formatFind(result) {
  const skills = Array.isArray(result?.skills) ? result.skills : [];
  if (skills.length === 0) {
    return { text: 'No matching skill found.', data: { skills: [], total: 0, roots: result?.root_uris ?? [] } };
  }
  const lines = skills.map((skill) => skillLine(skill, { withScore: true }));
  const text = `Skills matching the query (${skills.length}):\n${lines.join('\n')}\n\nRead the one you follow with openviking_read on its SKILL.md.`;
  return { text, data: { skills, total: Number(result?.total ?? skills.length), roots: result?.root_uris ?? [] } };
}

function formatShow(detail, { name }) {
  if (!detail || typeof detail !== 'object') return { text: `Skill ${name} came back empty.`, data: { skill: null } };
  const root = packageOf(detail);
  const md = trimmed(detail.skill_md_uri) || (root ? `${root}/SKILL.md` : '');
  const lines = [trimmed(detail.description) || '(no description)'];
  if (root) lines.push(`Root: ${root}`);
  if (md) lines.push(`Definition: ${md}`);
  if (Array.isArray(detail.tags) && detail.tags.length) lines.push(`Tags: ${detail.tags.join(', ')}`);
  if (Array.isArray(detail.allowed_tools) && detail.allowed_tools.length) lines.push(`Allowed tools: ${detail.allowed_tools.join(', ')}`);
  if (Array.isArray(detail.files) && detail.files.length) {
    lines.push(`Files: ${detail.files.map((file) => trimmed(file?.path) || trimmed(file?.name)).filter(Boolean).join(', ')}`);
  }
  if (detail.source && typeof detail.source === 'object') {
    const source = detail.source;
    const where = trimmed(source.source) || trimmed(source.clone_url) || trimmed(source.type);
    lines.push(`Source: ${where || 'unknown'}${source.operation ? ` (${source.operation})` : ''}`);
  }
  let content = typeof detail.content === 'string' ? detail.content : '';
  let cut = false;
  if (content.length > SHOW_CONTENT_MAX_CHARS) {
    content = content.slice(0, SHOW_CONTENT_MAX_CHARS);
    cut = true;
  }
  const text = `Skill ${trimmed(detail.name) || name}:\n${lines.join('\n')}${content ? `\n\nSKILL.md:\n${content}${cut ? '\n(cut; read the rest with openviking_read on the definition above)' : ''}` : ''}`;
  return { text, data: { skill: detail } };
}

// A crash-free CRC32 for the ZIP container: a stored (uncompressed) archive is
// all the server needs, and Node ships no writer.
const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? 0xEDB88320 ^ (value >>> 1) : value >>> 1;
    table[index] = value;
  }
  return table;
})();

function crc32(bytes) {
  let value = -1;
  for (const byte of bytes) value = CRC_TABLE[(value ^ byte) & 0xFF] ^ (value >>> 8);
  return (value ^ -1) >>> 0;
}

// A ZIP of the entries as they are: "stored", no compression, UTF-8 names.
// `entries` is [{ path, data }]; an empty directory carries a trailing slash.
export function zipStore(entries) {
  const dosTime = 0;
  const dosDate = (1 << 5) | 1; // 1980-01-01, the ZIP epoch
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const entry of entries) {
    const name = Buffer.from(entry.path, 'utf8');
    const data = entry.data ?? Buffer.alloc(0);
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6); // UTF-8 names
    local.writeUInt16LE(0, 8); // stored
    local.writeUInt16LE(dosTime, 10);
    local.writeUInt16LE(dosDate, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    local.writeUInt16LE(0, 28);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x0800, 8);
    central.writeUInt16LE(0, 10);
    central.writeUInt16LE(dosTime, 12);
    central.writeUInt16LE(dosDate, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(name.length, 28);
    central.writeUInt16LE(0, 30);
    central.writeUInt16LE(0, 32);
    central.writeUInt16LE(0, 34);
    central.writeUInt16LE(0, 36);
    central.writeUInt32LE(0, 38);
    central.writeUInt32LE(offset, 42);
    locals.push(local, name, data);
    centrals.push(central, name);
    offset += local.length + name.length + data.length;
  }
  const directory = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);
  return Buffer.concat([...locals, directory, end]);
}

// One workspace directory as ZIP entries, each path keeping the directory's
// name as its top segment (what `zip -r` does, and what the installer reads
// back as a single skill). Symlinks are refused: a link could carry a file
// from outside the workspace into what is uploaded.
export function readWorkspaceTree(relative, workspace, { maxBytes = UPLOAD_MAX_BYTES, maxFiles = TREE_MAX_FILES } = {}) {
  if (typeof relative !== 'string' || !relative || path.isAbsolute(relative) || relative.includes('\0')
    || relative.includes('\\') || relative.split('/').includes('..') || /^[a-z][a-z0-9+.-]*:/i.test(relative)) {
    throw new Error('directory must be relative to the workspace without traversal or URLs');
  }
  const root = fs.realpathSync(workspace);
  const directory = fs.realpathSync(path.resolve(root, relative));
  if (directory !== root && !directory.startsWith(root + path.sep)) throw new Error('directory escapes the workspace');
  if (!fs.statSync(directory).isDirectory()) throw new Error('directory must name a directory in the workspace');
  const base = path.basename(directory);
  const entries = [];
  let total = 0;
  const walk = (current, prefix) => {
    for (const name of fs.readdirSync(current).sort()) {
      if (name === '.git') continue;
      const full = path.join(current, name);
      const stat = fs.lstatSync(full);
      if (stat.isSymbolicLink()) throw new Error('directory must not contain symlinks');
      const zipPath = `${prefix}/${name}`;
      if (stat.isDirectory()) {
        entries.push({ path: `${zipPath}/`, data: Buffer.alloc(0) });
        walk(full, zipPath);
        continue;
      }
      if (!stat.isFile()) continue;
      if (entries.length >= maxFiles) throw new Error(`directory must hold at most ${maxFiles} files`);
      total += stat.size;
      if (total > maxBytes) throw new Error(`directory must hold at most ${maxBytes / 1024 / 1024} MiB`);
      entries.push({ path: zipPath, data: fs.readFileSync(full) });
    }
  };
  walk(directory, base);
  if (!entries.some((entry) => entry.path === `${base}/SKILL.md`)) {
    throw new Error('directory must contain a SKILL.md at its top level (the skill definition)');
  }
  return { name: `${base}.zip`, bytes: zipStore(entries) };
}

export class OVSkillClient {
  // `timeoutMs` bounds one request; installs may clone a repository, so the
  // install calls get more. `uploadMaxBytes` bounds one local package.
  constructor({ configPath, fetch: fetchImpl = globalThis.fetch, timeoutMs = 20_000,
    installTimeoutMs = 120_000, uploadMaxBytes = UPLOAD_MAX_BYTES } = {}) {
    this.configPath = configPath;
    this.fetchImpl = fetchImpl;
    this.timeoutMs = timeoutMs;
    this.installTimeoutMs = installTimeoutMs;
    this.uploadMaxBytes = uploadMaxBytes;
    this.connection = null;
  }

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

  // What the server refused, in the vocabulary of the skill routes: a 404 is a
  // missing skill, a 400 a payload OV would not accept (its own message names
  // the field, but carrying it through would risk echoing content).
  static failure(error, options = {}) {
    return toolError(error, {
      notFound: 'No skill is installed under that name',
      invalid: 'OpenViking rejected the input: a skill\'s SKILL.md needs frontmatter with name and description, and an update must keep the same name',
      ...options,
    });
  }

  async call(route, body, timeoutMs = this.timeoutMs, options = {}) {
    try {
      return await this.conn().json(route, body, { timeoutMs });
    } catch (error) {
      throw OVSkillClient.failure(error, options);
    }
  }

  // One request the client does not retry: PUT and DELETE go this way.
  async send(method, route, body, timeoutMs = this.timeoutMs, options = {}) {
    try {
      const headers = { ...this.conn().headers };
      return await this.conn().send(route, {
        method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      }, timeoutMs);
    } catch (error) {
      throw OVSkillClient.failure(error, options);
    }
  }

  async list({ target_uri, limit = SKILL_CATALOG_LIMIT } = {}) {
    const root = trimmed(target_uri) ? normalizeSkillRoot(target_uri) : '';
    const query = `node_limit=${Math.max(0, Math.min(1000, Number(limit) || SKILL_CATALOG_LIMIT))}`
      + (root ? `&target_uri=${encodeURIComponent(root)}` : '');
    return formatList((await this.call(`/api/v1/skills?${query}`, undefined, this.timeoutMs, {
      notFound: 'This OpenViking server has no skills API (it answered 404); upgrade it to use skills',
    })).result, root);
  }

  async find({ query, limit = 10, min_score, target_uri, level } = {}) {
    const text = trimmed(query);
    if (!text) throw ovError('invalid_input', 'query must not be empty');
    const body = { query: text, limit: Math.max(1, Math.min(50, Number(limit) || 10)) };
    if (min_score !== undefined) body.score_threshold = Number(min_score);
    if (Array.isArray(level) && level.length) body.level = level.map((value) => Number(value));
    const root = trimmed(target_uri) ? normalizeSkillRoot(target_uri) : '';
    if (root) body.target_uri = root;
    return formatFind((await this.call('/api/v1/skills/find', body, this.timeoutMs, {
      notFound: 'This OpenViking server has no skills API (it answered 404); upgrade it to use skills',
    })).result);
  }

  async show({ name, target_uri, include_content, include_files = true, include_source = false, level } = {}) {
    const skill = checkSkillName(name);
    const params = [`include_files=${include_files ? 'true' : 'false'}`, `include_source=${include_source ? 'true' : 'false'}`];
    if (include_content !== undefined) params.push(`include_content=${include_content ? 'true' : 'false'}`);
    if (level !== undefined) params.push(`level=${Number(level)}`);
    const root = trimmed(target_uri) ? normalizeSkillRoot(target_uri) : '';
    if (root) params.push(`target_uri=${encodeURIComponent(root)}`);
    const detail = (await this.call(`/api/v1/skills/${encodeURIComponent(skill)}?${params.join('&')}`)).result;
    return formatShow(detail, { name: skill });
  }

  // A local package (a SKILL.md, a .zip or a directory) as a temp upload; a
  // remote Git source or inline text needs no upload.
  async upload({ file_path, directory, workspace }) {
    let file;
    if (file_path) file = readWorkspaceFile(String(file_path), workspace, this.uploadMaxBytes);
    else if (directory) file = readWorkspaceTree(String(directory), workspace, { maxBytes: this.uploadMaxBytes });
    else throw ovError('invalid_input', 'Pass one of data, url, file_path or directory');
    try {
      const upload = (await this.conn().upload('/api/v1/resources/temp_upload', { data: file.bytes, name: file.name },
        { timeoutMs: this.timeoutMs })).result;
      if (!upload?.temp_file_id) throw ovError('openviking_failed', 'OpenViking did not accept the upload');
      return upload.temp_file_id;
    } catch (error) {
      throw toolError(error);
    }
  }

  // The `data` a source needs: inline SKILL.md text, or a Git URL the server
  // clones itself. A local package becomes a temp_file_id instead, which only
  // the add and update calls accept.
  async source({ data, url, file_path, directory, workspace }) {
    const given = [data, url, file_path, directory].filter((value) => trimmed(value) !== '').length;
    if (given !== 1) throw ovError('invalid_input', 'Pass exactly one of data, url, file_path or directory');
    if (trimmed(data)) return { data: String(data) };
    if (trimmed(url)) {
      const value = trimmed(url);
      if (!/^(?:https?:\/\/|git@|ssh:\/\/|git:\/\/)\S+$/.test(value)) throw ovError('invalid_input', 'url must be a Git source (https, git@, ssh or git)');
      return { data: value };
    }
    return { temp_file_id: await this.upload({ file_path, directory, workspace }) };
  }

  async add({ skills, target_uri, list_only = false, wait = false, ...source } = {}) {
    const body = { ...(await this.source(source)), wait: Boolean(wait) };
    // A single SKILL.md text is always one skill: nothing to choose from and
    // nothing to list.
    const inline = trimmed(body.data) && !/^(?:https?:\/\/|git@|ssh:\/\/|git:\/\/)/.test(trimmed(body.data));
    if (inline && (list_only || (Array.isArray(skills) && skills.length))) {
      throw ovError('invalid_input', 'skills and list_only need a source that can hold several skills: a Git URL, a file_path or a directory');
    }
    if (Array.isArray(skills) && skills.length) body.skills = skills.map((name) => checkSkillName(name));
    if (list_only) body.list_only = true;
    if (trimmed(target_uri)) body.target_uri = normalizeSkillRoot(target_uri);
    const result = (await this.call('/api/v1/skills', body, this.installTimeoutMs)).result;
    if (list_only) {
      const found = Array.isArray(result?.skills) ? result.skills : [];
      const lines = found.map((item) => `- ${item.name} (${item.path ?? ''}): ${sanitizeInline(item.description)}`);
      return {
        text: found.length
          ? `Skills in the source (${found.length}); nothing was installed:\n${lines.join('\n')}\n\nInstall one with openviking_skill_add and skills: ["<name>"].`
          : 'No skill found in that source.',
        data: { changed: false, list_only: true, skills: found, total: found.length },
      };
    }
    return this.installed(result, { action: 'add' });
  }

  // `from_source` refetches the Git source the server recorded at install
  // time; anything else replaces the package with the given content.
  async update({ name, from_source = false, wait = false, target_uri, ...source } = {}) {
    const skill = checkSkillName(name);
    const body = { wait: Boolean(wait), ...(from_source ? { from_source: true } : await this.source(source)) };
    if (trimmed(target_uri)) body.target_uri = normalizeSkillRoot(target_uri);
    const result = (await this.send('PUT', `/api/v1/skills/${encodeURIComponent(skill)}`, body, this.installTimeoutMs)).result;
    return this.installed(result, { action: 'update' });
  }

  // Every installed skill from one root (the caller's own by default; the
  // account-shared root holds skills this instance may not own).
  async names({ target_uri } = {}) {
    const listing = await this.list({ target_uri, limit: 1000 });
    return (listing.data.skills ?? []).map((skill) => trimmed(skill.name)).filter(Boolean);
  }

  // `ov skills update` with no names: refetch every installed skill that has a
  // recorded Git source and report the ones that do not.
  async updateInstalled({ names: requested, wait = false, target_uri } = {}) {
    const skills = Array.isArray(requested) && requested.length
      ? requested.map((name) => checkSkillName(name))
      : await this.names({ target_uri });
    if (skills.length === 0) return { text: 'No skills to update.', data: { changed: false, updated: [], skipped: [] } };
    const updated = [];
    const skipped = [];
    for (const name of skills) {
      try {
        let fromSource = true;
        try {
          const shown = await this.show({ name, target_uri, include_content: false, include_files: false, include_source: true, level: 0 });
          const source = shown.data.skill?.source;
          fromSource = trimmed(source?.type) === 'git';
        } catch (error) {
          if (error.code !== 'openviking_not_found') throw error;
          skipped.push({ name, reason: 'not installed' });
          continue;
        }
        if (!fromSource) {
          const reason = 'no recorded Git source; reinstall it with openviking_skill_add';
          if (Array.isArray(requested) && requested.length) throw ovError('openviking_invalid_input', `Skill ${name} has ${reason}`);
          skipped.push({ name, reason });
          continue;
        }
        const result = await this.update({ name, from_source: true, wait, target_uri });
        updated.push({ name, ...(result.data.installed?.[0] ?? {}) });
      } catch (error) {
        if (Array.isArray(requested) && requested.length) throw error;
        skipped.push({ name, reason: error.message });
      }
    }
    const lines = [
      ...updated.map((item) => `- updated ${item.name ?? item.root_uri ?? ''}`.trimEnd()),
      ...skipped.map((item) => `- skipped ${item.name}: ${item.reason}`),
    ];
    return {
      text: `Skill update: ${updated.length} updated, ${skipped.length} skipped${lines.length ? `\n${lines.join('\n')}` : '.'}`,
      data: { changed: updated.length > 0, updated, skipped, total: updated.length },
    };
  }

  async remove({ name, all = false, target_uri } = {}) {
    const root = trimmed(target_uri) ? normalizeSkillRoot(target_uri) : '';
    const skills = all ? await this.names({ target_uri: root }) : [checkSkillName(name)];
    if (skills.length === 0) return { text: 'No skills to remove.', data: { changed: false, removed: [] } };
    const removed = [];
    for (const skill of skills) {
      const query = root ? `?target_uri=${encodeURIComponent(root)}` : '';
      removed.push((await this.send('DELETE', `/api/v1/skills/${encodeURIComponent(skill)}${query}`)).result ?? { name: skill });
    }
    return {
      text: `Removed ${removed.length} skill(s): ${skills.join(', ')}.`,
      data: { changed: true, removed, removed_names: skills, total: removed.length },
    };
  }

  // The text of a workspace SKILL.md or skill directory, for validating one
  // before it is installed (the CLI's `ov skills validate <path>`).
  localDefinition({ file_path, directory, workspace }) {
    if (trimmed(directory)) {
      const file = readWorkspaceFile(`${trimmed(directory).replace(/\/+$/, '')}/SKILL.md`, workspace, this.uploadMaxBytes);
      return file.bytes.toString('utf8');
    }
    return readWorkspaceFile(String(file_path), workspace, this.uploadMaxBytes).bytes.toString('utf8');
  }

  async validate({ data, strict = false, file_path, directory, workspace } = {}) {
    const text = trimmed(data) ? String(data) : (trimmed(file_path) || trimmed(directory)
      ? this.localDefinition({ file_path, directory, workspace }) : '');
    if (!text.trim()) throw ovError('invalid_input', 'Pass the SKILL.md text as data, or a workspace file_path or directory');
    const result = (await this.call('/api/v1/skills/validate', { data: text, strict: Boolean(strict) })).result;
    const valid = result?.valid !== false;
    const errors = (Array.isArray(result?.errors) ? result.errors : [])
      .map((item) => (typeof item === 'string' ? item : item?.message)).filter(Boolean);
    const warnings = (Array.isArray(result?.warnings) ? result.warnings : [])
      .map((item) => (typeof item === 'string' ? item : item?.message)).filter(Boolean);
    const lines = [
      valid ? 'SKILL.md is valid.' : 'SKILL.md is not valid.',
      ...errors.map((item) => `- error: ${item}`),
      ...warnings.map((item) => `- warning: ${item}`),
      ...(trimmed(result?.name) ? [`- name: ${result.name}`] : []),
      ...(trimmed(result?.description) ? [`- description: ${sanitizeInline(result.description)}`] : []),
    ];
    return { text: lines.join('\n'), data: { valid, errors, warnings, result } };
  }

  // The catalog renderSkillCatalog needs: both roots from one list call.
  async catalog({ limit = SKILL_CATALOG_LIMIT } = {}) {
    const result = (await this.call(`/api/v1/skills?node_limit=${limit}`)).result;
    return Array.isArray(result?.skills) ? result.skills : [];
  }

  async catalogBlock({ budgetTokens = SKILL_CATALOG_TOKENS } = {}) {
    return renderSkillCatalog(await this.catalog(), { budgetTokens });
  }

  // One install or update answer, the way the API reports it: one result, or
  // { installed, total } when a source held several skills.
  installed(result, { action }) {
    const installed = Array.isArray(result?.installed) ? result.installed : [result].filter(Boolean);
    if (installed.length === 0) {
      return { text: `OpenViking reported no skill ${action === 'add' ? 'installed' : 'updated'}.`, data: { changed: false, action } };
    }
    const lines = installed.map((item) => {
      const root = packageOf(item);
      const md = trimmed(item?.skill_md_uri) || (root ? `${root}/SKILL.md` : '');
      const task = trimmed(item?.task_id);
      return `- ${trimmed(item?.name) || root || '?'}: ${md || '(no uri)'}${task ? ` (processing in the background, task_id=${task}; check with openviking_skill_show)` : ''}`;
    });
    const verb = action === 'add' ? 'added' : 'updated';
    return {
      text: `Skill ${verb} (${installed.length}):\n${lines.join('\n')}\n\nRead the SKILL.md above to use it now; semantic search finds it once indexing completes.`,
      data: { changed: true, action, installed, total: installed.length, root_uri: packageOf(installed[0]) || null },
    };
  }
}
