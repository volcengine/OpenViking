// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { DOCUMENT_BYTES, PHOTO_BYTES, DOWNLOAD_BYTES, integer } from './api.mjs';

export function filePayload(content, chatId, quote = null) {
  const fd = fs.openSync(content.path, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  let bytes;
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size < 1 || stat.size > DOCUMENT_BYTES) throw new Error('Telegram upload must be a nonempty regular file of at most 50 MB');
    bytes = Buffer.alloc(stat.size);
    if (fs.readSync(fd, bytes, 0, bytes.length, 0) !== bytes.length) throw new Error('Telegram upload file changed');
  } finally { fs.closeSync(fd); }
  const name = path.basename((content.name || content.path).replaceAll('\\', '/'));
  const mime = ({ '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png' })[path.extname(name).toLowerCase()];
  // Large images can still be sent as documents without recompression.
  const type = mime && bytes.length <= PHOTO_BYTES ? 'photo' : 'document';
  const body = new FormData();
  body.set('chat_id', chatId);
  body.set(type, new Blob([bytes], { type: mime ?? 'application/octet-stream' }), name);
  if (quote) body.set('reply_parameters', JSON.stringify({ message_id: quote }));
  return { method: type === 'photo' ? 'sendPhoto' : 'sendDocument', body };
}

export async function downloadFile(api, media, directory, signal) {
  if (media.size !== undefined && integer(media.size, 'file size', 1) > DOWNLOAD_BYTES) throw new Error('Telegram downloads support files up to 20 MB');
  const meta = await api.request('getFile', { file_id: media.id }, signal);
  if (meta?.file_id !== media.id || (meta.file_size !== undefined && integer(meta.file_size, 'file size', 1) > DOWNLOAD_BYTES)) {
    throw new Error('Invalid Telegram file metadata or size');
  }
  const bytes = await api.download(meta.file_path, signal);
  if (!bytes.length || (meta.file_size !== undefined && bytes.length !== meta.file_size)
    || (media.size !== undefined && bytes.length !== media.size)) throw new Error('Telegram media size mismatch');
  const filename = path.join(directory, createHash('sha256').update(bytes).digest('hex'));
  const temp = `${filename}.${randomUUID()}`;
  try { fs.writeFileSync(temp, bytes, { flag: 'wx', mode: 0o600 }); fs.renameSync(temp, filename); }
  finally { fs.rmSync(temp, { force: true }); }
  return { path: filename, name: media.name ? path.basename(media.name.replaceAll('\\', '/')) : `${media.kind}-attachment`,
    kind: media.kind, mime_type: media.mimeType ?? 'application/octet-stream', size: bytes.length };
}
