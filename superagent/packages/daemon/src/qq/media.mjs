// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { MEDIA_BYTES, QQError, identifier, openid, readBounded } from './api.mjs';

const hash = (algorithm, bytes) => createHash(algorithm).update(bytes).digest('hex');
const filename = (name) => path.basename(String(name).replaceAll('\\', '/')).replace(/[\x00-\x1f\x7f]/g, '_').slice(0, 200) || 'attachment';

export function uploadContent(content) {
  const fd = fs.openSync(content.path, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  let bytes;
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size < 1 || stat.size > MEDIA_BYTES) throw new Error('QQ upload must be a nonempty regular file of at most 20 MiB');
    bytes = Buffer.alloc(stat.size);
    if (fs.readSync(fd, bytes, 0, bytes.length, 0) !== bytes.length) throw new Error('QQ upload file changed');
  } finally { fs.closeSync(fd); }
  const name = filename(content.name || content.path);
  const image = /\.(png|jpe?g)$/i.test(name) && bytes.length <= 20_000_000;
  return { bytes, name, fileType: image ? 1 : 4 };
}

function trustedUrl(value, upload = false) {
  let url;
  try { url = new URL(value.startsWith('//') ? `https:${value}` : value); } catch { throw new Error('Invalid QQ media URL'); }
  // Incoming QQ attachments sometimes use http; upgrade known CDN hosts to TLS.
  if (url.protocol === 'http:' && !upload) url.protocol = 'https:';
  const cos = /^[a-z0-9-]+\.cos\.[a-z0-9-]+\.myqcloud\.com$/.test(url.hostname);
  const cdn = ['multimedia.nt.qq.com', 'gchat.qpic.cn', 'c2cpicdw.qpic.cn'].includes(url.hostname);
  if (url.protocol !== 'https:' || !(upload ? cos : cos || cdn) || url.username || url.password
    || url.hash || url.port && url.port !== '443') throw new Error('QQ media host is not supported');
  return url.href;
}

export async function uploadFile(api, to, file, signal) {
  signal.throwIfAborted();
  const base = `/v2/users/${openid(to)}`;
  const common = { file_type: file.fileType, file_name: file.name, srv_send_msg: false };
  let result;
  if (file.bytes.length < 5_000_000) {
    result = await api.request(`${base}/files`, { ...common, file_data: file.bytes.toString('base64') }, signal);
  } else {
    const prepared = await api.request(`${base}/upload_prepare`, {
      file_type: file.fileType, file_name: file.name, file_size: file.bytes.length,
      md5: hash('md5', file.bytes), sha1: hash('sha1', file.bytes), md5_10m: hash('md5', file.bytes.subarray(0, 10_002_432)),
    }, signal);
    identifier(prepared.upload_id, 'upload id');
    const size = prepared.block_size;
    const parts = prepared.parts;
    if (!Number.isSafeInteger(size) || size < 1 || size > MEDIA_BYTES || !Array.isArray(parts)
      || parts.length > 100 || parts.length !== Math.ceil(file.bytes.length / size)) throw new Error('Invalid QQ upload parts');
    const indices = new Set(parts.map((part) => part.index));
    if (indices.size !== parts.length || parts.some(({ index }) => !Number.isSafeInteger(index) || index < 1 || index > parts.length)) {
      throw new Error('Invalid QQ upload part indices');
    }
    // Validate the whole upload plan before transmitting any file bytes.
    const urls = parts.map((part) => trustedUrl(part.presigned_url, true));
    for (let i = 0; i < parts.length; i++) {
      signal.throwIfAborted();
      const part = parts[i];
      const bytes = file.bytes.subarray((part.index - 1) * size, part.index * size);
      try {
        const response = await api.fetchImpl(urls[i], { method: 'PUT', redirect: 'error', signal, body: bytes });
        await response.body?.cancel();
        if (!response.ok) throw new Error('rejected');
      } catch { throw new Error('QQ file part upload failed or timed out'); }
      for (let attempt = 0; ; attempt++) {
        try {
          await api.request(`${base}/upload_part_finish`, {
            upload_id: prepared.upload_id, part_index: part.index, block_size: bytes.length, md5: hash('md5', bytes),
          }, signal);
          break;
        } catch (error) {
          // Tencent retries 40093001 while the uploaded part becomes available.
          // This acknowledges bytes only, never sends a message.
          if (attempt >= 2 || !(error instanceof QQError) || error.code !== 40093001) throw error;
          await delay(1000 * (attempt + 1), undefined, { signal });
        }
      }
    }
    signal.throwIfAborted();
    result = await api.request(`${base}/files`, { ...common, upload_id: prepared.upload_id }, signal);
  }
  return identifier(result.file_info, 'uploaded file info', 16384);
}

export async function downloadFile(api, media, directory, signal) {
  if (media.size !== undefined && (!Number.isSafeInteger(media.size) || media.size < 1 || media.size > MEDIA_BYTES)) {
    throw new Error('QQ downloads support files up to 20 MiB');
  }
  const url = trustedUrl(media.url);
  let bytes;
  try {
    signal.throwIfAborted();
    // Signed CDN URLs are used without a Bot Authorization header or redirects.
    const response = await api.fetchImpl(url, { redirect: 'error', signal });
    if (!response.ok) { await response.body?.cancel(); throw new Error('rejected'); }
    bytes = await readBounded(response, MEDIA_BYTES);
    signal.throwIfAborted();
  } catch { throw new Error('QQ media download failed or exceeded the size limit'); }
  if (!bytes.length || media.size !== undefined && media.size !== bytes.length) throw new Error('QQ media size mismatch');
  const destination = path.join(directory, hash('sha256', bytes));
  const temp = `${destination}.${randomUUID()}`;
  try { fs.writeFileSync(temp, bytes, { flag: 'wx', mode: 0o600 }); fs.renameSync(temp, destination); }
  finally { fs.rmSync(temp, { force: true }); }
  return { path: destination, name: filename(media.name ?? 'attachment'), kind: media.kind,
    mime_type: media.mimeType ?? 'application/octet-stream', size: bytes.length };
}
