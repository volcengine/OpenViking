// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { OVUnavailableError } from './ov-connection.mjs';

// What the daemon's own archives in the owner's private OpenViking area share
// (mail, meeting notes). OV keeps one vector per file, from the first 4096
// (estimated) tokens of it, and stores a split document only as its pieces.
// So the whole text is kept for reading, and a text too long for one vector is
// imported a second time, split by OV's own Markdown parser, for recall alone.
// Text is in place when a call returns; OV only builds vectors afterwards
// (`vectors_only`), so storing never asks an LLM for a summary.

const VECTORS_ONLY = 'vectors_only';
// OV's Markdown parser keeps a text whole up to these (parsers.markdown).
const SECTION_TOKENS = 2048;
const SECTION_CHARS = 6000;
const CJK = /[一-鿿぀-ヿ가-힯]/g;
const QUIET = { info() {}, warn() {} };
const UPLOAD_TIMEOUT_MS = 120_000;

// Whether OV would split this text, by its own estimate (CJK ~0.7 token per
// character, anything else not blank ~0.3). Only then are chunks stored.
export function needsChunks(text) {
  if (text.length > SECTION_CHARS) return true;
  const cjk = text.match(CJK)?.length ?? 0;
  return cjk * 0.7 + (text.replace(/\s/g, '').length - cjk) * 0.3 > SECTION_TOKENS;
}

// Where a chunk's text starts in the whole text: the line holding the first
// of its lines found there, or 0. OV may change headings and spacing, so lines
// are matched by their start.
export function chunkOffset(text, chunk) {
  const lines = String(chunk).split('\n').map((line) => line.replace(/^#+\s*/, '').trim()).filter((line) => line.length >= 8);
  for (const line of lines.slice(0, 8)) {
    const index = text.indexOf(line.slice(0, 80));
    if (index >= 0) return text.lastIndexOf('\n', index) + 1;
  }
  return 0;
}

// The text stored at a uri: a file as it is; a directory as its text files in
// name order, each under its own heading when there are several. `get(route)`
// answers an /api/v1 GET with its result.
export async function readStoredText(get, uri) {
  const stat = await get(`/fs/stat?uri=${encodeURIComponent(uri)}`);
  if (!stat?.isDir) return String(await get(`/content/read?uri=${encodeURIComponent(uri)}&offset=0&limit=-1&raw=true`) ?? '');
  // A tree stops three levels down unless told otherwise; split headings nest deeper.
  const files = (await get(`/fs/tree?uri=${encodeURIComponent(uri)}&level_limit=16&include_abstract=false`) ?? [])
    .filter((entry) => !entry.isDir && /\.(md|txt)$/i.test(entry.uri)
      && !String(entry.rel_path ?? '').split('/').some((part) => part.startsWith('.')))
    .sort((left, right) => String(left.rel_path).localeCompare(String(right.rel_path)));
  if (files.length === 0) throw new OVUnavailableError('nothing readable there yet', 404);
  const parts = [];
  for (const file of files) {
    const content = String(await get(`/content/read?uri=${encodeURIComponent(file.uri)}&offset=0&limit=-1&raw=true`) ?? '');
    parts.push(files.length > 1 ? `## ${file.rel_path}\n\n${content}` : content);
  }
  return parts.join('\n\n');
}

// An OV that cannot be reached, is overloaded or failed, as opposed to one
// that refused this one request.
export function ovOutage(error) {
  return !(error instanceof OVUnavailableError) || error.httpStatus === undefined
    || error.httpStatus === 429 || error.httpStatus >= 500;
}

// Writes and imports under one OV connection. `kind` names the archive in the
// one log line it writes itself (`<kind>_chunks_failed`).
export class OVArchive {
  constructor(connection, { pollMs = 500, logger = QUIET, kind = 'archive' } = {}) {
    this.connection = connection;
    this.pollMs = pollMs;
    this.logger = logger;
    this.kind = kind;
  }

  get(route) {
    return this.connection.json(`/api/v1${route}`).then((data) => data.result);
  }

  async write(uri, content) {
    await this.connection.json('/api/v1/content/write',
      { uri, content, mode: 'replace', wait: false, processing_mode: VECTORS_ONLY });
    return uri;
  }

  // Imports `data` as a file named `name` to exactly `to`; resolves with the task id.
  async importFile(to, { data, name, type }, args = {}) {
    const upload = await this.connection.upload('/api/v1/resources/temp_upload', { data, name, type },
      { timeoutMs: UPLOAD_TIMEOUT_MS });
    const added = await this.connection.json('/api/v1/resources', {
      temp_file_id: upload.result.temp_file_id, to, source_name: name, wait: false,
      processing_mode: VECTORS_ONLY, ...(Object.keys(args).length ? { args } : {}),
    }, { timeoutMs: UPLOAD_TIMEOUT_MS });
    return added.result?.task_id ?? null;
  }

  // A text too long for one vector, split by OV under `to` for recall alone.
  // Nobody waits for it, and its failure loses only recall: it is logged.
  async chunk(to, text, name) {
    if (!needsChunks(text)) return false;
    try {
      await this.importFile(to, { data: Buffer.from(text, 'utf8'), name, type: 'text/markdown' });
      return true;
    } catch (error) {
      this.logger.warn(`${this.kind}_chunks_failed`, { error: error.message });
      return false;
    }
  }
}
