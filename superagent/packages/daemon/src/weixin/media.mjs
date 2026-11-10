// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import {
  createCipheriv,
  createDecipheriv,
  createHash,
  randomBytes,
} from 'node:crypto';
import { DEFAULT_WEIXIN_CDN_BASE_URL, getUploadUrl } from './api.mjs';

// Media transport is derived from Tencent/openclaw-weixin 2.4.9 (MIT).
// See NOTICE.md for the pinned upstream revision and license attribution.
export const WEIXIN_MEDIA_MAX_BYTES = 20 * 1024 * 1024;
const MAX_INBOUND_MEDIA_ITEMS = 4;
const CDN_TIMEOUT_MS = 60_000;
const UPLOAD_ATTEMPTS = 3;

const IMAGE_TYPES = new Map([
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.png', 'image/png'],
  ['.gif', 'image/gif'],
  ['.webp', 'image/webp'],
  ['.bmp', 'image/bmp'],
]);

const FILE_TYPES = new Map([
  ['.pdf', 'application/pdf'],
  ['.txt', 'text/plain'],
  ['.md', 'text/markdown'],
  ['.json', 'application/json'],
  ['.csv', 'text/csv'],
  ['.zip', 'application/zip'],
  ['.doc', 'application/msword'],
  ['.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  ['.xls', 'application/vnd.ms-excel'],
  ['.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
  ['.ppt', 'application/vnd.ms-powerpoint'],
  ['.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'],
]);

function timeoutSignal(timeoutMs, externalSignal = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error('media request timeout')), timeoutMs);
  timer.unref?.();
  const abort = () => controller.abort(externalSignal?.reason);
  if (externalSignal?.aborted) abort();
  else externalSignal?.addEventListener('abort', abort, { once: true });
  return {
    signal: controller.signal,
    cleanup() {
      clearTimeout(timer);
      externalSignal?.removeEventListener('abort', abort);
    },
  };
}

function safeRemoteUrl(value, label) {
  let url;
  try { url = new URL(value); } catch { throw new Error(`${label} URL is invalid`); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error(`${label} URL must be HTTP(S) without credentials`);
  }
  return url.toString();
}

function cdnDownloadUrl(media) {
  if (typeof media?.full_url === 'string' && media.full_url.trim()) {
    return safeRemoteUrl(media.full_url.trim(), 'Weixin media');
  }
  if (typeof media?.encrypt_query_param !== 'string' || !media.encrypt_query_param) {
    throw new Error('Weixin media has no download reference');
  }
  const url = new URL('download', `${DEFAULT_WEIXIN_CDN_BASE_URL}/`);
  url.searchParams.set('encrypted_query_param', media.encrypt_query_param);
  return url.toString();
}

async function fetchBounded(url, { signal = null, maxBytes = WEIXIN_MEDIA_MAX_BYTES + 16 } = {}) {
  const bounded = timeoutSignal(CDN_TIMEOUT_MS, signal);
  try {
    const response = await fetch(url, { signal: bounded.signal });
    if (!response.ok) throw new Error(`Weixin CDN download failed with HTTP ${response.status}`);
    const announced = Number.parseInt(response.headers.get('content-length') ?? '', 10);
    if (Number.isFinite(announced) && announced > maxBytes) {
      throw new Error(`Weixin media exceeds the ${WEIXIN_MEDIA_MAX_BYTES}-byte limit`);
    }
    const chunks = [];
    let size = 0;
    const reader = response.body?.getReader();
    if (!reader) throw new Error('Weixin CDN download returned no body');
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) {
        await reader.cancel().catch(() => {});
        throw new Error(`Weixin media exceeds the ${WEIXIN_MEDIA_MAX_BYTES}-byte limit`);
      }
      chunks.push(Buffer.from(value));
    }
    if (size < 1) {
      throw new Error(`Weixin media must be nonempty and at most ${WEIXIN_MEDIA_MAX_BYTES} bytes`);
    }
    return Buffer.concat(chunks, size);
  } finally {
    bounded.cleanup();
  }
}

function parseAesKey(value) {
  if (typeof value !== 'string' || !value) throw new Error('Weixin media AES key is missing');
  const decoded = Buffer.from(value, 'base64');
  if (decoded.length === 16) return decoded;
  if (decoded.length === 32 && /^[0-9a-f]{32}$/i.test(decoded.toString('ascii'))) {
    return Buffer.from(decoded.toString('ascii'), 'hex');
  }
  throw new Error('Weixin media AES key is invalid');
}

function decryptAesEcb(ciphertext, key) {
  if (ciphertext.length % 16 !== 0) throw new Error('Weixin encrypted media has an invalid length');
  const decipher = createDecipheriv('aes-128-ecb', key, null);
  const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  if (plaintext.length < 1 || plaintext.length > WEIXIN_MEDIA_MAX_BYTES) {
    throw new Error(`Weixin media must be nonempty and at most ${WEIXIN_MEDIA_MAX_BYTES} bytes`);
  }
  return plaintext;
}

function encryptAesEcb(plaintext, key) {
  const cipher = createCipheriv('aes-128-ecb', key, null);
  return Buffer.concat([cipher.update(plaintext), cipher.final()]);
}

function imageFormat(bytes) {
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) {
    return { extension: '.jpg', mimeType: 'image/jpeg' };
  }
  if (bytes.length >= 8 && bytes.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
    return { extension: '.png', mimeType: 'image/png' };
  }
  if (bytes.length >= 6 && /^GIF8[79]a$/.test(bytes.subarray(0, 6).toString('ascii'))) {
    return { extension: '.gif', mimeType: 'image/gif' };
  }
  if (bytes.length >= 12 && bytes.subarray(0, 4).toString('ascii') === 'RIFF'
    && bytes.subarray(8, 12).toString('ascii') === 'WEBP') {
    return { extension: '.webp', mimeType: 'image/webp' };
  }
  if (bytes.length >= 2 && bytes.subarray(0, 2).toString('ascii') === 'BM') {
    return { extension: '.bmp', mimeType: 'image/bmp' };
  }
  return { extension: '.jpg', mimeType: 'image/jpeg' };
}

function safeFileName(value, fallback) {
  const base = path.basename(String(value ?? '')).replace(/[\u0000-\u001f\u007f]/g, '_').trim();
  return (base || fallback).slice(0, 180);
}

function fileFormat(fileName) {
  const extension = path.extname(fileName).toLowerCase();
  return {
    extension: /^\.[a-z0-9]{1,10}$/.test(extension) ? extension : '.bin',
    mimeType: FILE_TYPES.get(extension) ?? 'application/octet-stream',
  };
}

function inboundDescriptors(message) {
  const output = [];
  for (const item of message?.item_list ?? []) {
    if (output.length >= MAX_INBOUND_MEDIA_ITEMS) break;
    if (item?.type === 2 && item.image_item?.media
      && (item.image_item.media.full_url || item.image_item.media.encrypt_query_param)) {
      output.push({ kind: 'image', item });
    } else if (item?.type === 4 && item.file_item?.media
      && (item.file_item.media.full_url || item.file_item.media.encrypt_query_param)) {
      output.push({ kind: 'file', item });
    }
  }
  return output;
}

export function inboundText(message) {
  const parts = [];
  for (const item of message?.item_list ?? []) {
    if (item?.type === 1 && item.text_item?.text != null) {
      const text = String(item.text_item.text).trim();
      if (text) parts.push(text);
    } else if (item?.type === 3 && item.voice_item?.text != null) {
      const text = String(item.voice_item.text).trim();
      if (text) parts.push(`[voice transcript] ${text}`);
    }
  }
  return parts.join('\n').trim();
}

async function downloadDescriptor(descriptor, { localMessageId, ordinal, mediaDirectory, signal }) {
  let bytes;
  let format;
  let fileName;
  if (descriptor.kind === 'image') {
    const image = descriptor.item.image_item;
    const encrypted = await fetchBounded(cdnDownloadUrl(image.media), { signal });
    if (typeof image.aeskey === 'string' && /^[0-9a-f]{32}$/i.test(image.aeskey)) {
      bytes = decryptAesEcb(encrypted, Buffer.from(image.aeskey, 'hex'));
    } else if (image.media.aes_key) {
      bytes = decryptAesEcb(encrypted, parseAesKey(image.media.aes_key));
    } else {
      bytes = encrypted;
      if (bytes.length > WEIXIN_MEDIA_MAX_BYTES) throw new Error('Weixin image exceeds the size limit');
    }
    format = imageFormat(bytes);
    fileName = `image${format.extension}`;
  } else {
    const file = descriptor.item.file_item;
    const encrypted = await fetchBounded(cdnDownloadUrl(file.media), { signal });
    bytes = decryptAesEcb(encrypted, parseAesKey(file.media.aes_key));
    fileName = safeFileName(file.file_name, 'attachment.bin');
    format = fileFormat(fileName);
  }

  fs.mkdirSync(mediaDirectory, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(mediaDirectory, 0o700); } catch {}
  const stem = createHash('sha256').update(`${localMessageId}\0${ordinal}`).digest('hex').slice(0, 32);
  const localPath = path.join(mediaDirectory, `${stem}${format.extension}`);
  const tempPath = `${localPath}.${process.pid}.${randomBytes(6).toString('hex')}.tmp`;
  try {
    fs.writeFileSync(tempPath, bytes, { mode: 0o600, flag: 'wx' });
    fs.renameSync(tempPath, localPath);
    try { fs.chmodSync(localPath, 0o600); } catch {}
  } finally {
    try { fs.rmSync(tempPath, { force: true }); } catch {}
  }
  return {
    ordinal,
    kind: descriptor.kind,
    localPath,
    mimeType: format.mimeType,
    fileName,
    byteSize: bytes.length,
  };
}

export async function downloadInboundMedia(message, options) {
  const descriptors = inboundDescriptors(message);
  const records = [];
  const markers = [];
  const failures = [];
  for (let ordinal = 0; ordinal < descriptors.length; ordinal += 1) {
    const descriptor = descriptors[ordinal];
    try {
      const record = await downloadDescriptor(descriptor, { ...options, ordinal });
      records.push(record);
      markers.push(record.kind === 'image'
        ? '[image attachment]'
        : `[file attachment name=${JSON.stringify(record.fileName)}]`);
    } catch (error) {
      failures.push({ kind: descriptor.kind, error });
      markers.push(`[${descriptor.kind} attachment unavailable]`);
    }
  }
  return { records, markers, failures };
}

// The Core has already validated and frozen the upload under its private
// directory; only the size limit is Weixin-specific.
export function readOutboundMedia(filePath) {
  const bytes = fs.readFileSync(filePath);
  if (bytes.length < 1 || bytes.length > WEIXIN_MEDIA_MAX_BYTES) {
    throw new Error(`Weixin file must be nonempty and at most ${WEIXIN_MEDIA_MAX_BYTES} bytes`);
  }
  const fileName = safeFileName(path.basename(filePath), 'attachment.bin');
  const extension = path.extname(fileName).toLowerCase();
  return {
    bytes,
    fileName,
    kind: IMAGE_TYPES.has(extension) ? 'image' : 'file',
    mimeType: IMAGE_TYPES.get(extension) ?? FILE_TYPES.get(extension) ?? 'application/octet-stream',
  };
}

function uploadUrl(response, fileKey) {
  if (typeof response?.upload_full_url === 'string' && response.upload_full_url.trim()) {
    return safeRemoteUrl(response.upload_full_url.trim(), 'Weixin upload');
  }
  if (typeof response?.upload_param !== 'string' || !response.upload_param) {
    throw new Error('Weixin getUploadUrl returned no upload URL');
  }
  const url = new URL('upload', `${DEFAULT_WEIXIN_CDN_BASE_URL}/`);
  url.searchParams.set('encrypted_query_param', response.upload_param);
  url.searchParams.set('filekey', fileKey);
  return url.toString();
}

async function uploadCiphertext(url, ciphertext) {
  let lastError = null;
  for (let attempt = 1; attempt <= UPLOAD_ATTEMPTS; attempt += 1) {
    const bounded = timeoutSignal(CDN_TIMEOUT_MS);
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: new Uint8Array(ciphertext),
        signal: bounded.signal,
      });
      if (response.status >= 400 && response.status < 500) {
        throw new Error(`Weixin CDN upload failed with HTTP ${response.status}`);
      }
      if (response.status !== 200) throw new Error(`Weixin CDN upload failed with HTTP ${response.status}`);
      const downloadParam = response.headers.get('x-encrypted-param');
      if (!downloadParam) throw new Error('Weixin CDN upload response has no download reference');
      return downloadParam;
    } catch (error) {
      lastError = error;
      if (/HTTP 4\d\d/.test(String(error?.message))) throw error;
    } finally {
      bounded.cleanup();
    }
  }
  throw lastError ?? new Error('Weixin CDN upload failed');
}

export async function uploadOutboundMedia({ account, to, media }) {
  const aesKey = randomBytes(16);
  const aesKeyHex = aesKey.toString('hex');
  const fileKey = randomBytes(16).toString('hex');
  const ciphertext = encryptAesEcb(media.bytes, aesKey);
  const response = await getUploadUrl({
    apiBaseUrl: account.baseUrl,
    token: account.token,
    fileKey,
    mediaType: media.kind === 'image' ? 1 : 3,
    to,
    rawSize: media.bytes.length,
    rawMd5: createHash('md5').update(media.bytes).digest('hex'),
    encryptedSize: ciphertext.length,
    aesKey: aesKeyHex,
  });
  const downloadParam = await uploadCiphertext(uploadUrl(response, fileKey), ciphertext);
  const reference = {
    encrypt_query_param: downloadParam,
    aes_key: Buffer.from(aesKeyHex, 'utf8').toString('base64'),
    encrypt_type: 1,
  };
  if (media.kind === 'image') {
    return {
      type: 2,
      image_item: { media: reference, mid_size: ciphertext.length },
    };
  }
  return {
    type: 4,
    file_item: { media: reference, file_name: media.fileName, len: String(media.bytes.length) },
  };
}
