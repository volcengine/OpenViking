// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// OpenViking skills, the way vikingbot uses them: the agent is told what may
// fit as each skill's frontmatter (name, description, the SKILL.md to read),
// and reading that definition makes the package itself available next to it,
// so a skill that ships scripts can run them. Only the work is told: a worker's
// policy carries the skills that rank for the line its task came from, and the
// router's own lines carry none. Nothing here manages skills: installing and
// updating belong to OpenViking's own `ov skills` CLI and API, and this module
// only reads.
//
// Two reads make it work, both against the connection the caller already has:
// POST /api/v1/skills/find for the candidates of one line (ranked only when a
// line becomes a worker's task), and
// GET /api/v1/skills/{name}?include_content&include_files&include_integrity for
// the package a read of its SKILL.md activates: one snapshot under OV's own
// package lock, whose SKILL.md is the page the model reads, so the instructions
// and the files on disk cannot be two revisions. The cache keeps one directory
// per skill and revision (<dataDir>/skills/<name>/<revision>), marked whole only
// when every file that could arrive is there.

import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomBytes } from 'node:crypto';

// What one line may be shown: few candidates, weak ones dropped, each
// description cut short (the full text is in the SKILL.md).
export const SKILL_DISCOVERY_LIMIT = 5;
export const SKILL_DISCOVERY_THRESHOLD = 0.35;
export const SKILL_SUMMARY_TOKENS = 600;
const SKILL_DESCRIPTION_TOKENS = 40;

// A package is downloaded whole, and only within these bounds: a skill is
// instructions and small scripts, not a dataset.
const MAX_FILES = 200;
const MAX_FILE_BYTES = 1024 * 1024;
const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
// OV's own summaries of a package, not part of it.
const NOT_PACKAGE_FILES = new Set(['.abstract.md', '.overview.md', '.source.json', '.relations.json', '.meta.json']);

// The token estimate OpenViking's own plugin budgets with: CJK counts 1.5,
// the rest a quarter per character. It overcounts rather than undercounts.
export function estimateTokens(text) {
  if (!text) return 0;
  let cjk = 0;
  for (let index = 0; index < text.length; index += 1) {
    if (text.charCodeAt(index) >= 0x3000) cjk += 1;
  }
  return Math.ceil(cjk * 1.5 + (text.length - cjk) / 4);
}

// A skill's description is written by whoever installed it, and the shared
// root is account-wide: it must not close the element it sits in, nor run over
// a line.
export function escapeXml(text) {
  return String(text ?? '')
    .replace(/[<>&"]/g, (char) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[char]))
    .replace(/[\u0000-\u001f\u007f]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function truncateToTokens(text, maxTokens) {
  if (estimateTokens(text) <= maxTokens) return text;
  // Roughly the characters that fit: 1.5 tokens per CJK character, 0.25 per
  // other; overcount rather than undercount.
  return `${text.slice(0, Math.max(1, Math.floor(maxTokens * 2)) - 1).trimEnd()}…`;
}

// Where a skill sits, from the one uri the model would read:
// <root>/<name>/SKILL.md, where <root> is the caller's own skills or the
// account-shared one. null for anything else, so a plain read stays a read.
export function skillFromDefinitionUri(uri) {
  const value = String(uri ?? '').replace(/\/+$/, '');
  if (!value.endsWith('/SKILL.md')) return null;
  const packageUri = value.slice(0, -'/SKILL.md'.length);
  const split = packageUri.lastIndexOf('/');
  if (split <= 'viking://'.length) return null;
  const rootUri = packageUri.slice(0, split);
  const name = packageUri.slice(split + 1);
  if (!/^viking:\/\/(?:~|user\/[^/]+|agent)\/skills$/.test(rootUri)) return null;
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) return null;
  return { name, rootUri, packageUri, skillMdUri: value };
}

const skillSource = (rootUri) => (rootUri === 'viking://agent/skills' ? 'openviking_agent' : 'openviking_user');

// The definition of one candidate, as the frontmatter the agent reads: what it
// is, where the definition sits, and which tool reads it. This is what
// vikingbot shows for a skill and what OpenViking's plugin calls the catalog.
function skillElement(skill, { withDescriptions }) {
  const name = String(skill?.name ?? '').trim();
  const uri = String(skill?.skill_md_uri ?? '').trim()
    || (skill?.root_uri ? `${String(skill.root_uri).replace(/\/+$/, '')}/SKILL.md` : '');
  if (!name || !uri) return '';
  const root = skillFromDefinitionUri(uri)?.rootUri
    || String(skill?.root_uri ?? '').replace(/\/+$/, '').replace(/\/[^/]+$/, '');
  const description = withDescriptions
    ? truncateToTokens(escapeXml(skill?.description), SKILL_DESCRIPTION_TOKENS)
    : '';
  return [
    `  <skill source="${skillSource(root)}">`,
    `    <name>${escapeXml(name)}</name>`,
    ...(description ? [`    <description>${description}</description>`] : []),
    `    <location>${escapeXml(uri)}</location>`,
    '    <read_tool>openviking_read</read_tool>',
    '  </skill>',
  ].join('\n');
}

// The <skills> block for one line: each candidate's frontmatter, whole while it
// fits, then names and locations only, and finally a count when not even those
// do. '' when there is nothing to show. Only a worker's policy renders one.
export function renderSkillSummary(skills, { reason = 'may fit this task', budgetTokens = SKILL_SUMMARY_TOKENS } = {}) {
  const seen = new Set();
  const unique = [];
  for (const skill of Array.isArray(skills) ? skills : []) {
    const element = skillElement(skill, { withDescriptions: false });
    if (!element) continue;
    const uri = String(skill?.skill_md_uri ?? '').trim() || `${String(skill?.root_uri ?? '').replace(/\/+$/, '')}/SKILL.md`;
    if (seen.has(uri)) continue;
    seen.add(uri);
    unique.push(skill);
  }
  if (unique.length === 0) return '';
  const open = `<skills source="openviking" for="${escapeXml(reason)}">`;
  const close = '</skills>';
  const frame = estimateTokens(open) + estimateTokens(close);
  const budget = Math.max(64, Number(budgetTokens) || SKILL_SUMMARY_TOKENS);
  // Whole entries while they fit; a list that has to drop entries moves to
  // names only first, so more skills stay visible than descriptions allow.
  const build = (withDescriptions) => {
    const elements = unique.map((skill) => skillElement(skill, { withDescriptions })).filter(Boolean);
    const kept = [];
    let used = frame;
    for (let index = 0; index < elements.length; index += 1) {
      const cost = estimateTokens(elements[index]);
      if (used + cost > budget) return { block: '', dropped: elements.length - index, complete: false };
      kept.push(elements[index]);
      used += cost;
    }
    return { block: [open, ...kept, close].join('\n'), dropped: 0, complete: true };
  };
  for (const withDescriptions of [true, false]) {
    const full = build(withDescriptions);
    if (full.complete) return full.block;
  }
  // Neither fits whole: as many names as the budget holds, plus a tail that
  // says how many were left out.
  const names = unique.map((skill) => skillElement(skill, { withDescriptions: false })).filter(Boolean);
  const kept = [];
  let used = frame;
  for (let index = 0; index < names.length; index += 1) {
    const dropped = names.length - index - 1;
    const tailCost = dropped > 0 ? estimateTokens(`  ... +${dropped} more, openviking_find finds them`) : 0;
    const cost = estimateTokens(names[index]);
    if (used + cost + tailCost > budget) break;
    kept.push(names[index]);
    used += cost;
  }
  if (kept.length > 0) {
    const dropped = names.length - kept.length;
    const block = [open, ...kept, ...(dropped > 0 ? [`  ... +${dropped} more, openviking_find finds them`] : []), close].join('\n');
    if (estimateTokens(block) <= budget) return block;
  }
  const stub = `<skills source="openviking" for="${escapeXml(reason)}: ${unique.length} skills; openviking_find finds them"></skills>`;
  return estimateTokens(stub) <= budget ? stub : '';
}

// The candidates for one line: OpenViking ranks its installed skills by what
// they do. A failure leaves the line without skills; it never blocks it.
export async function discoverSkills(connection, query, {
  limit = SKILL_DISCOVERY_LIMIT, scoreThreshold = SKILL_DISCOVERY_THRESHOLD, timeoutMs,
} = {}) {
  const text = String(query ?? '').trim();
  if (!text) return [];
  try {
    const { result } = await connection.json('/api/v1/skills/find', {
      query: text,
      limit: Math.max(1, Math.min(20, Number(limit) || SKILL_DISCOVERY_LIMIT)),
      score_threshold: scoreThreshold,
    }, { timeoutMs });
    return Array.isArray(result?.skills) ? result.skills : [];
  } catch (error) {
    return [];
  }
}

function safeRelativePath(value) {
  const text = String(value ?? '');
  if (!text || text.startsWith('/') || text.includes('\\') || text.includes('\0')) return null;
  const parts = text.split('/');
  if (parts.some((part) => part === '' || part === '.' || part === '..')) return null;
  return parts.join('/');
}

function sameBytes(bytes, item) {
  if (Number.isFinite(Number(item?.size)) && Number(item.size) !== bytes.length) return false;
  if (!item?.sha256) return true;
  return createHash('sha256').update(bytes).digest('hex') === String(item.sha256);
}

// How long a revision stays after the skill moved on: long enough that a task
// already handed the directory is not still running when it goes.
const REVISION_KEEP_MS = 24 * 60 * 60_000;
// A write that died before its rename leaves a scratch directory; it is gone
// once it is clearly not being written any more.
const SCRATCH_MAX_AGE_MS = 60 * 60_000;
// What was written for one revision: whether it is whole (only files that can
// never arrive may be missing) and which were left out. Inside the package, so
// a copied cache carries it.
const STATE_FILE = '.materialized.json';

// One skill's own directory in the cache: <dataDir>/skills/<source>/<name>,
// with one directory per revision under it. Sharing is by skill, never by a
// prefix of its name, so `chart` clearing its old revisions cannot touch
// `chart-tools`; the source keeps this instance's `chart` and the account's
// `chart` apart, so neither evicts the other's revisions.
function skillCacheRoot(dataDir, skillsRoot, name) {
  const source = String(skillsRoot).replace(/\/+$/, '') === 'viking://agent/skills' ? 'agent' : 'user';
  return path.join(dataDir, 'skills', source, name);
}

// The same package named its way: OV answers with the canonical
// `viking://user/<id>/skills/<name>`, while a read may have said
// `viking://~/skills/<name>`.
function samePackageRoot(answered, asked) {
  const value = String(answered ?? '').replace(/\/+$/, '');
  const wanted = String(asked ?? '').replace(/\/+$/, '');
  if (value === wanted) return true;
  if (!wanted.startsWith('viking://~/')) return false;
  return value.replace(/^viking:\/\/user\/[^/]+\//, '') === wanted.slice('viking://~/'.length);
}

// When this revision was last handed out: cleanup measures the keep window from
// here, so a directory a task is still using does not look old because the
// skill has not changed since.
function touch(dir) {
  try {
    const now = new Date();
    fs.utimesSync(dir, now, now);
  } catch { /* best effort */ }
}

function readState(dir) {
  try {
    const state = JSON.parse(fs.readFileSync(path.join(dir, STATE_FILE), 'utf8'));
    return state && typeof state === 'object' ? state : null;
  } catch {
    return null;
  }
}

function writeState(dir, state) {
  try {
    fs.writeFileSync(path.join(dir, STATE_FILE), JSON.stringify(state), { mode: 0o600 });
    return true;
  } catch {
    return false;
  }
}

// Removes what this skill no longer needs: revisions older than the keep
// window, and scratch directories nobody is writing. The revision in use is
// always kept, and a directory handed to a task stays while it could still be
// running.
function pruneRevisions(skillRoot, keep) {
  let entries;
  try { entries = fs.readdirSync(skillRoot, { withFileTypes: true }); } catch { return; }
  const now = Date.now();
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name === keep) continue;
    const full = path.join(skillRoot, entry.name);
    let stat;
    try { stat = fs.statSync(full); } catch { continue; }
    const scratch = entry.name.includes('.part-');
    if (now - stat.mtimeMs < (scratch ? SCRATCH_MAX_AGE_MS : REVISION_KEEP_MS)) continue;
    try { fs.rmSync(full, { recursive: true, force: true }); } catch { /* best effort */ }
  }
}

// The package of one skill as one snapshot: the SKILL.md text, the revision
// that names it, and the manifest with a digest per file, all read under OV's
// own package lock. Reading a skill's definition takes this once, so the page
// the model reads and the files it can run are the same revision. null when
// the address is not a skill's, or OV does not return the package.
export async function fetchSkillPackage(connection, { uri, timeoutMs, logger = null } = {}) {
  const info = skillFromDefinitionUri(uri);
  if (!info) return null;
  const skip = (reason, fields = {}) => {
    logger?.debug?.('ov_skill_package_skipped', { name: info.name, reason, ...fields });
    return null;
  };
  let detail;
  try {
    detail = (await connection.json(`/api/v1/skills/${encodeURIComponent(info.name)}`
      + `?include_content=true&include_files=true&include_integrity=true&target_uri=${encodeURIComponent(info.rootUri)}`,
    undefined, { timeoutMs })).result;
  } catch (error) {
    return skip('unavailable', { status: error?.httpStatus ?? null });
  }
  const rootUri = String(detail?.root_uri ?? detail?.uri ?? '').replace(/\/+$/, '');
  const content = typeof detail?.content === 'string' ? detail.content : '';
  if (!samePackageRoot(rootUri, info.packageUri) || !content) return skip('not-the-same-package');
  return {
    info,
    rootUri,
    content,
    revision: String(detail?.revision ?? '').slice(0, 12) || 'latest',
    files: Array.isArray(detail?.files) ? detail.files : [],
  };
}

// Writes one snapshot into this skill's cache and returns where it sits, so
// the scripts and references it ships can be run. The directory is named by
// the revision, so a read of the same revision costs one manifest request and
// no download. A package with a file that failed for a reason that may pass
// (the fetch failed, the bytes did not match) is written but not marked whole:
// the next read fills it in and says again what is missing. Never throws: a
// package that cannot be written leaves the read as it is.
export async function writeSkillPackage(connection, snapshot, { dataDir, timeoutMs, logger = null } = {}) {
  if (!connection || !snapshot?.info || !dataDir) return null;
  const { info, rootUri, content, revision, files: manifest } = snapshot;
  const fail = (reason, fields = {}) => {
    logger?.debug?.('ov_skill_package_skipped', { name: info.name, reason, ...fields });
    return null;
  };
  const cacheRoot = skillCacheRoot(dataDir, info.rootUri, info.name);
  const target = path.join(cacheRoot, revision);
  try {
    fs.mkdirSync(cacheRoot, { recursive: true, mode: 0o700 });
  } catch {
    return fail('cache-unwritable');
  }
  pruneRevisions(cacheRoot, revision);
  const state = readState(target);
  if (state?.complete && fs.existsSync(path.join(target, 'SKILL.md'))) {
    touch(target);
    return {
      path: target, name: info.name, rootUri, revision, files: null, complete: true,
      skipped: Array.isArray(state.skipped) ? state.skipped : [], cached: true,
    };
  }

  // A revision that was left open keeps what it already verified: a retry
  // fetches only what is still missing, so two files failing on different
  // attempts do not trade places, and what a task already has stays.
  const kept = (relative, item) => {
    try {
      const bytes = fs.readFileSync(path.join(target, relative));
      return sameBytes(bytes, item) ? bytes : null;
    } catch {
      return null;
    }
  };

  // One download is bounded as a whole, not per file: a package with many
  // files must not hold the read that activated it.
  const budgetMs = Math.max(1_000, Number(timeoutMs) || 20_000);
  const deadline = Date.now() + budgetMs;
  const definition = Buffer.from(content, 'utf8');
  if (definition.length > MAX_FILE_BYTES) return fail('definition-too-large');
  const files = manifest.filter((file) => file && !file.is_dir && file.path
    && path.posix.basename(String(file.path)) !== 'SKILL.md'
    && !NOT_PACKAGE_FILES.has(path.posix.basename(String(file.path))));
  if (files.length > MAX_FILES) return fail('too-many-files', { files: files.length });
  let total = definition.length;
  const staged = [];
  const skipped = [];
  for (const item of files) {
    const relative = safeRelativePath(item.path);
    if (!relative || Number(item.size) > MAX_FILE_BYTES) { skipped.push(String(item.path)); continue; }
    const held = kept(relative, item);
    if (held) {
      total += held.length;
      if (total > MAX_TOTAL_BYTES) return fail('too-large', { bytes: total });
      staged.push({ path: relative, bytes: held });
      continue;
    }
    const remaining = deadline - Date.now();
    if (remaining <= 0) { skipped.push(relative); continue; }
    let text;
    try {
      text = (await connection.json(`/api/v1/content/read?uri=${encodeURIComponent(String(item.uri))}&limit=-1`,
        undefined, { timeoutMs: remaining })).result;
    } catch {
      skipped.push(relative);
      continue;
    }
    const bytes = Buffer.from(String(text ?? ''), 'utf8');
    // A file whose bytes do not match its manifest is not kept: what could not
    // be verified is not run.
    if (!sameBytes(bytes, item)) { skipped.push(relative); continue; }
    total += bytes.length;
    if (total > MAX_TOTAL_BYTES) return fail('too-large', { bytes: total });
    staged.push({ path: relative, bytes });
  }

  // Whole means every file that could arrive is here. A file that can never
  // arrive (an unsafe path, one past the per-file bound) is remembered in the
  // state instead, so the package is not downloaded again for it, and every
  // read still says it is missing.
  const complete = files.every((item) => {
    const relative = safeRelativePath(item.path);
    if (!relative || Number(item.size) > MAX_FILE_BYTES) return true;
    return !skipped.includes(relative);
  });
  const scratch = `${target}.part-${process.pid}-${randomBytes(4).toString('hex')}`;
  fs.rmSync(scratch, { recursive: true, force: true });
  try {
    fs.mkdirSync(scratch, { recursive: true, mode: 0o700 });
    fs.writeFileSync(path.join(scratch, 'SKILL.md'), definition, { mode: 0o600 });
    for (const file of staged) {
      const full = path.join(scratch, file.path);
      fs.mkdirSync(path.dirname(full), { recursive: true, mode: 0o700 });
      fs.writeFileSync(full, file.bytes, { mode: 0o600 });
    }
    writeState(scratch, { revision, complete, skipped, atMs: Date.now() });
    fs.rmSync(target, { recursive: true, force: true });
    fs.renameSync(scratch, target);
  } catch (error) {
    fs.rmSync(scratch, { recursive: true, force: true });
    logger?.warn?.('ov_skill_package_write_failed', { name: info.name, error: String(error?.code ?? 'failed') });
    return null;
  }
  return {
    path: target, name: info.name, rootUri, revision, files: staged.length,
    complete, skipped, cached: false,
  };
}

// The package behind one skill definition, fetched and written in one step.
export async function materializeSkill(connection, { uri, dataDir, timeoutMs, logger = null } = {}) {
  const snapshot = await fetchSkillPackage(connection, { uri, timeoutMs, logger });
  return snapshot ? await writeSkillPackage(connection, snapshot, { dataDir, timeoutMs, logger }) : null;
}
