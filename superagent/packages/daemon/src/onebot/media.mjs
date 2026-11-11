// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { cleanName, segmentText } from './events.mjs';

// The Core's own workspace-file limit, for uploads and downloads alike.
const FILE_BYTES = 20 * 1024 * 1024;
const BASE64_CHARS = Math.ceil(FILE_BYTES / 3) * 4;
const FORWARD_NODES = 200;
const FORWARD_BYTES = 64 * 1024;

const filename = (name) => path.basename(String(name).replaceAll('\\', '/')).replace(/[\x00-\x1f\x7f]/g, '_').slice(0, 200) || 'attachment';
const httpUrl = (value) => typeof value === 'string' && /^https?:\/\//i.test(value);

// Media comes from QQ's servers, never from this machine or a private
// network, so an address that names one (loopback, link-local, private,
// shared or unspecified) is refused: a message cannot make the daemon fetch
// from its own network. Only literal addresses are checked; no name is resolved.
const LOCAL = new net.BlockList();
for (const [address, prefix] of [['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10], ['127.0.0.0', 8], ['169.254.0.0', 16],
  ['172.16.0.0', 12], ['192.168.0.0', 16]]) LOCAL.addSubnet(address, prefix, 'ipv4');
for (const [address, prefix] of [['::', 128], ['::1', 128], ['fc00::', 7], ['fe80::', 10]]) LOCAL.addSubnet(address, prefix, 'ipv6');

function localHost(url) {
  const host = new URL(url).hostname.replace(/^\[(.*)\]$/, '$1').replace(/\.$/, '').toLowerCase();
  if (host === 'localhost' || host.endsWith('.localhost')) return true;
  const type = net.isIP(host);
  // An IPv4-mapped IPv6 address is checked against the IPv4 ranges.
  return type !== 0 && LOCAL.check(host, type === 6 ? 'ipv6' : 'ipv4');
}

// Pictures QQ shows inline, told by their first bytes rather than by name.
function imageType(bytes) {
  const head = bytes.subarray(0, 12).toString('latin1');
  if (head.startsWith('\x89PNG\r\n\x1a\n')) return 'image/png';
  if (head.startsWith('\xff\xd8\xff')) return 'image/jpeg';
  if (/^GIF8[79]a/.test(head)) return 'image/gif';
  if (head.startsWith('RIFF') && head.slice(8) === 'WEBP') return 'image/webp';
  return null;
}

// The file the model asked to send, read without following a link.
export function readUpload(content) {
  const fd = fs.openSync(content.path, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  let bytes;
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size < 1 || stat.size > FILE_BYTES) throw new Error('OneBot upload must be a nonempty regular file of at most 20 MiB');
    bytes = Buffer.alloc(stat.size);
    if (fs.readSync(fd, bytes, 0, bytes.length, 0) !== bytes.length) throw new Error('OneBot upload file changed');
  } finally { fs.closeSync(fd); }
  return { bytes, name: filename(content.name || content.path) };
}

// The bytes travel inside the segment, so the implementation never opens a
// path or an address the model chose. NapCat and LLOneBot take `base64://`
// for both segment types; a file in a message (rather than upload_group_file)
// gets a message id, so its echo is known as the agent's own and it can be
// retracted.
export function fileSegment({ bytes, name }) {
  const file = `base64://${bytes.toString('base64')}`;
  return imageType(bytes) ? { type: 'image', data: { file } } : { type: 'file', data: { file, name } };
}

async function readBounded(response, maxBytes) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error('OneBot download has no body');
  const parts = [];
  let length = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maxBytes) throw new Error('OneBot download exceeds the size limit');
      parts.push(Buffer.from(value));
    }
    return Buffer.concat(parts);
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

// Media addresses carry download keys: they never appear in an error.
async function download(fetchImpl, url, signal) {
  try {
    signal.throwIfAborted();
    if (localHost(url)) throw new Error('local address');
    const response = await fetchImpl(url, { redirect: 'error', signal });
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error('download rejected');
    }
    const bytes = await readBounded(response, FILE_BYTES);
    signal.throwIfAborted();
    if (!bytes.length) throw new Error('empty download');
    return bytes;
  } catch { throw new Error('OneBot media download failed or exceeded the size limit'); }
}

function save(directory, bytes, { name, kind, mimeType }) {
  const destination = path.join(directory, createHash('sha256').update(bytes).digest('hex'));
  const temp = `${destination}.${randomUUID()}`;
  try { fs.writeFileSync(temp, bytes, { flag: 'wx', mode: 0o600 }); fs.renameSync(temp, destination); }
  finally { fs.rmSync(temp, { force: true }); }
  return { path: destination, name: filename(name), kind, mime_type: mimeType, size: bytes.length };
}

// A forwarded chat as one `name: text` line per message, bounded. NapCat
// answers with messages ({ sender, message }), LLOneBot with { sender,
// content }, Lagrange with node segments ({ data: { nickname, content } }).
async function transcript(socket, file, { chat, account, signal }) {
  const data = await socket.call('get_forward_msg', { id: file.file }, signal);
  const nodes = Array.isArray(data?.messages) ? data.messages : Array.isArray(data?.message) ? data.message : null;
  if (!nodes) throw new Error('OneBot returned an invalid forwarded chat');
  const lines = [];
  let bytes = 0;
  for (const item of nodes.slice(0, FORWARD_NODES)) {
    const node = item?.type === 'node' && item.data ? item.data : item;
    const name = cleanName(node?.sender?.card) ?? cleanName(node?.sender?.nickname) ?? cleanName(node?.nickname)
      ?? cleanName(node?.sender?.user_id ?? node?.user_id) ?? 'unknown';
    const segments = node?.message ?? node?.content;
    const line = `${name}: ${segmentText(segments, { chat, account }).replace(/\n/g, '\n  ') || '[empty]'}`;
    bytes += Buffer.byteLength(line, 'utf8') + 1;
    if (bytes > FORWARD_BYTES) {
      lines.push('[transcript truncated]');
      break;
    }
    lines.push(line);
  }
  if (nodes.length > FORWARD_NODES && lines.at(-1) !== '[transcript truncated]') {
    lines.push(`[${nodes.length - FORWARD_NODES} more messages not shown]`);
  }
  return Buffer.from(`${lines.join('\n')}\n`, 'utf8');
}

// One attachment of a received message, saved under `directory`. The
// implementation is asked first (get_image, get_file: NapCat and LLOneBot
// keep a fresh copy); its bytes or its address are used, else the address
// the event carried. Lagrange has neither action and gives the address only.
export async function fetchMedia({ socket, fetchImpl, file, chat, account, directory, signal }) {
  if (file.kind === 'forward') {
    const bytes = await transcript(socket, file, { chat, account, signal });
    return save(directory, bytes, { name: file.name, kind: 'file', mimeType: 'text/plain; charset=utf-8' });
  }
  if (file.size !== null && file.size > FILE_BYTES) throw new Error('OneBot downloads support files up to 20 MiB');
  const image = file.kind === 'image';
  const params = image ? (file.file ? { file: file.file } : null)
    : file.fileId ? { file_id: file.fileId } : file.file ? { file: file.file } : null;
  const answer = params ? await socket.call(image ? 'get_image' : 'get_file', params, signal).catch(() => null) : null;
  signal.throwIfAborted();
  let bytes = null;
  if (typeof answer?.base64 === 'string' && answer.base64) {
    if (answer.base64.length > BASE64_CHARS + 4) throw new Error('OneBot downloads support files up to 20 MiB');
    bytes = Buffer.from(answer.base64, 'base64');
    if (!bytes.length || bytes.length > FILE_BYTES) throw new Error('OneBot returned an invalid attachment');
  } else {
    const urls = [...new Set([answer?.url, file.url].filter(httpUrl))];
    if (!urls.length) throw new Error('OneBot did not provide this attachment');
    for (const [index, url] of urls.entries()) {
      try {
        bytes = await download(fetchImpl, url, signal);
        break;
      } catch (error) {
        if (index === urls.length - 1) throw error;
      }
    }
  }
  return save(directory, bytes, { name: file.name, kind: image ? 'image' : 'file',
    mimeType: imageType(bytes) ?? 'application/octet-stream' });
}
