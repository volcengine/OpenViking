// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { preparePrivateDirectory } from './private-temp.mjs';
import { inlineImageBlocks } from './inline-images.mjs';
import { downloadMAFile } from './ma-download.mjs';

const invalid = (message) => Object.assign(new Error(message), { code: 'invalid_content' });
const hash = (value) => createHash('sha256').update(value).digest('hex');
export function outputRelative(value) {
  if (typeof value !== 'string' || !value || value.includes('\\') || value.includes('\0')
    || value.startsWith('/') || value.split('/').some((part) => !part || part === '.' || part === '..')
    || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(value)) throw invalid('Use a relative file path under /mnt/session/outputs/ without traversal.');
  return value;
}

export class MAFiles {
  constructor({ config, json, fetch = globalThis.fetch }) {
    this.config = config; this.json = json; this.fetch = fetch;
    this.enabled = config.volcFiles !== 'off';
  }
  async upload(localPath, sessionId, toolUseId) {
    if (!this.enabled) throw invalid('MA file transfer is disabled by VIKA_VOLC_FILES=off.');
    const fd = await fs.promises.open(localPath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const digest = createHash('sha256');
    try {
      const before = await fd.stat();
      if (!before.isFile() || before.size < 1) throw invalid('Attachment must be a nonempty regular file.');
      for await (const chunk of fd.createReadStream({ autoClose: false })) digest.update(chunk);
      const after = await fd.stat();
      if (before.size !== after.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs) {
        throw invalid('Attachment changed while preparing its upload.');
      }
    } finally { await fd.close(); }
    const filename = path.basename(localPath);
    const mountPath = `vika/${hash(toolUseId).slice(0, 24)}/${digest.digest('hex').slice(0, 16)}-${filename}`;
    // openAsBlob is disk-backed; FormData streams it without a whole-file Buffer.
    const blob = await fs.openAsBlob(localPath);
    const form = new FormData();
    form.set('purpose', 'agent'); form.set('file', blob, filename);
    const response = await this.fetch(`${this.config.volcBaseUrl}/files`, { method: 'POST',
      headers: { Authorization: `Bearer ${this.config.volcApiKey}` }, body: form,
      signal: AbortSignal.timeout(this.config.volcTransferTimeoutMs ?? 30 * 60 * 1000) })
      .catch((error) => { throw invalid(`MA attachment upload transport failed (${error.cause?.code ?? error.name ?? 'network_error'})`); });
    if (!response.ok) throw invalid(`MA attachment upload failed (${response.status})`);
    const uploaded = await response.json();
    if (!uploaded.id) throw invalid('MA attachment upload returned no file ID');
    await this.json(`/sessions/${encodeURIComponent(sessionId)}/resources`, { method: 'POST',
      body: { type: 'file', file_id: uploaded.id, mount_path: mountPath } });
    return { file_id: uploaded.id, path: `/mnt/session/uploads/${mountPath}` };
  }
  async outputFiles(sessionId) {
    const files = []; let after = '';
    for (let page = 0; page < 100; page++) {
      const result = await this.json(`/files?scope_id=${encodeURIComponent(sessionId)}&purpose=agent&limit=100${after ? `&after=${encodeURIComponent(after)}` : ''}`);
      // MA may return JSON null while an output registration is pending.
      if (result === null) return files;
      if (!result || !Array.isArray(result.data ?? [])) throw invalid('MA output listing has an invalid shape');
      files.push(...(result.data ?? []));
      if (!result.has_more) return files;
      const next = result.last_id ?? result.data?.at(-1)?.id;
      if (!next || next === after) throw invalid('MA output file pagination did not advance');
      after = next;
    }
    throw invalid('MA output file listing exceeded the pagination limit');
  }
  async download(relative, sessionId, toolUseId) {
    outputRelative(relative);
    if (!this.enabled) throw invalid('MA file transfer is disabled by VIKA_VOLC_FILES=off.');
    const workspace = this.config.workspace;
    if (!workspace) throw invalid('The Channel workspace is not configured');
    let selected;
    for (let retry = 0; retry < 12; retry++) {
      const matches = (await this.outputFiles(sessionId)).filter((file) =>
        file.filename === relative || file.filename === `/mnt/session/outputs/${relative}`);
      if (matches.length > 1) throw invalid('MA output path is ambiguous; write a new uniquely named output file');
      selected = matches[0];
      if (selected) break;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    if (!selected?.id) throw invalid('Output is not registered yet. Write the file under /mnt/session/outputs/ and retry after registration.');
    const fileId = selected.id;
    // Resource identity, rather than the model's retry tool ID, owns the partial
    // bytes. A fresh tool attempt can resume transfer without replaying a send.
    const staged = `.ma-staging/${hash(sessionId)}/${hash(fileId)}/${relative}`;
    const destination = path.join(workspace, staged);
    let directory = fs.realpathSync(workspace);
    if (!fs.statSync(directory).isDirectory()) throw invalid('Workspace is not a directory');
    for (const part of path.dirname(staged).split('/')) directory = preparePrivateDirectory(path.join(directory, part));
    this.downloads ??= new Map();
    if (!this.downloads.has(destination)) {
      const transfer = downloadMAFile({ destination, fileId, sessionId, fetch: this.fetch,
        detail: () => this.json(`/files/${encodeURIComponent(fileId)}`) });
      this.downloads.set(destination, transfer);
      transfer.finally(() => this.downloads.delete(destination)).catch(() => {});
    }
    await this.downloads.get(destination);
    return staged;
  }
  async prepareInput(input, sessionId, toolUseId) {
    const result = structuredClone(input);
    if (result.file_path !== undefined) result.file_path = await this.download(result.file_path, sessionId, toolUseId);
    if (result.card_images && typeof result.card_images === 'object') {
      for (const [alias, relative] of Object.entries(result.card_images)) result.card_images[alias] = await this.download(relative, sessionId, toolUseId);
    }
    // finish_timer_occurrence can contain a delivery with the same media schema.
    if (result.delivery?.input) result.delivery.input = await this.prepareInput(result.delivery.input, sessionId, toolUseId);
    return result;
  }
  async resultContent(result, sessionId, toolUseId) {
    if (!result.media?.length) return [{ type: 'text', text: JSON.stringify(result) }];
    const mapped = [];
    let text = result.text ?? '';
    for (const item of result.media) {
      const mounted = await this.upload(item.path, sessionId, toolUseId);
      text = text.replaceAll(item.path, mounted.path);
      mapped.push({ ...item, ...mounted });
    }
    // Substitute all occurrences, including nested metadata and escaped paths.
    let serialized = JSON.stringify({ ...result, text, media: mapped, data: { ...result.data, media: mapped } });
    for (let i = 0; i < result.media.length; i++) {
      serialized = serialized.replaceAll(JSON.stringify(result.media[i].path).slice(1, -1), JSON.stringify(mapped[i].path).slice(1, -1));
    }
    let inlineBytes = 0;
    const images = (await inlineImageBlocks(result.media)).filter((block) => {
      if (block.type !== 'image' || inlineBytes + block.data.length > 3_000_000) return false;
      inlineBytes += block.data.length; return true;
    }).map((block) => ({ type: 'image', source: { type: 'base64', media_type: block.mimeType, data: block.data } }));
    return [{ type: 'text', text: serialized }, ...images];
  }
}
