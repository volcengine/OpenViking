// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomBytes } from 'node:crypto';

// Protocol implementation derived from Tencent/openclaw-weixin 2.4.9 (MIT).
// See NOTICE.md in this directory for attribution and the pinned upstream revision.
export const DEFAULT_WEIXIN_API_BASE_URL = 'https://ilinkai.weixin.qq.com';
export const DEFAULT_WEIXIN_CDN_BASE_URL = 'https://novac2c.cdn.weixin.qq.com/c2c';
export const WEIXIN_CHANNEL_VERSION = '2.4.9';
const ILINK_APP_ID = 'bot';
const ILINK_APP_CLIENT_VERSION = (2 << 16) | (4 << 8) | 9;
const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;

function baseInfo() {
  return {
    channel_version: WEIXIN_CHANNEL_VERSION,
    bot_agent: 'vika/0.4.0',
  };
}

function baseUrl(value) {
  return value.endsWith('/') ? value : `${value}/`;
}

function commonHeaders() {
  return {
    'iLink-App-Id': ILINK_APP_ID,
    'iLink-App-ClientVersion': String(ILINK_APP_CLIENT_VERSION),
  };
}

function authenticatedHeaders(token = null) {
  const uin = randomBytes(4).readUInt32BE(0);
  return {
    ...commonHeaders(),
    'Content-Type': 'application/json',
    AuthorizationType: 'ilink_bot_token',
    'X-WECHAT-UIN': Buffer.from(String(uin), 'utf8').toString('base64'),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

function timeoutSignal(timeoutMs, externalSignal = null) {
  const controller = new AbortController();
  const timer = timeoutMs > 0
    ? setTimeout(() => controller.abort(new Error('request timeout')), timeoutMs)
    : null;
  timer?.unref?.();
  const abort = () => controller.abort(externalSignal?.reason);
  if (externalSignal?.aborted) abort();
  else externalSignal?.addEventListener('abort', abort, { once: true });
  return {
    signal: controller.signal,
    cleanup() {
      if (timer) clearTimeout(timer);
      externalSignal?.removeEventListener('abort', abort);
    },
  };
}

async function request({ apiBaseUrl, endpoint, method = 'POST', token = null, body = null,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS, signal = null, authenticated = true }) {
  const url = new URL(endpoint, baseUrl(apiBaseUrl));
  const bounded = timeoutSignal(timeoutMs, signal);
  try {
    const response = await fetch(url, {
      method,
      headers: authenticated ? authenticatedHeaders(token) : commonHeaders(),
      ...(body === null ? {} : { body: JSON.stringify(body) }),
      signal: bounded.signal,
    });
    const text = await response.text();
    if (!response.ok) throw new Error(`Weixin ${endpoint} failed with HTTP ${response.status}`);
    return text;
  } finally {
    bounded.cleanup();
  }
}

const LOSSLESS_ID_FIELDS = new Set(['message_id', 'msg_id', 'svr_id']);

export function parseWeixinJson(rawText) {
  let output = '';
  let index = 0;
  while (index < rawText.length) {
    if (rawText[index] !== '"') {
      output += rawText[index++];
      continue;
    }
    const start = index++;
    let escaped = false;
    while (index < rawText.length) {
      const char = rawText[index++];
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') break;
    }
    const token = rawText.slice(start, index);
    output += token;
    let cursor = index;
    while (/\s/.test(rawText[cursor] ?? '')) cursor += 1;
    if (rawText[cursor] !== ':') continue;
    let key;
    try { key = JSON.parse(token); } catch { continue; }
    if (!LOSSLESS_ID_FIELDS.has(key)) continue;
    output += rawText.slice(index, cursor + 1);
    cursor += 1;
    while (/\s/.test(rawText[cursor] ?? '')) output += rawText[cursor++];
    const numberStart = cursor;
    if (rawText[cursor] === '-') cursor += 1;
    while (/\d/.test(rawText[cursor] ?? '')) cursor += 1;
    if (cursor > numberStart && !(cursor === numberStart + 1 && rawText[numberStart] === '-')) {
      output += `"${rawText.slice(numberStart, cursor)}"`;
      index = cursor;
    } else {
      index = numberStart;
    }
  }
  return JSON.parse(output);
}

export async function fetchLoginQr({ localTokens = [] } = {}) {
  const text = await request({
    apiBaseUrl: DEFAULT_WEIXIN_API_BASE_URL,
    endpoint: 'ilink/bot/get_bot_qrcode?bot_type=3',
    body: { local_token_list: localTokens.slice(-10) },
    timeoutMs: 0,
  });
  return JSON.parse(text);
}

export async function pollLoginQr({ qrcode, verifyCode = null, apiBaseUrl = DEFAULT_WEIXIN_API_BASE_URL, signal = null }) {
  let endpoint = `ilink/bot/get_qrcode_status?qrcode=${encodeURIComponent(qrcode)}`;
  if (verifyCode) endpoint += `&verify_code=${encodeURIComponent(verifyCode)}`;
  try {
    const text = await request({
      apiBaseUrl,
      endpoint,
      method: 'GET',
      authenticated: false,
      timeoutMs: 35_000,
      signal,
    });
    return JSON.parse(text);
  } catch (error) {
    if (error?.name === 'AbortError' || /timeout/i.test(String(error?.message))) return { status: 'wait' };
    throw error;
  }
}

export async function getUpdates({ apiBaseUrl, token, cursor = '', timeoutMs = 35_000, signal = null }) {
  try {
    const text = await request({
      apiBaseUrl,
      endpoint: 'ilink/bot/getupdates',
      token,
      body: { get_updates_buf: cursor, base_info: baseInfo() },
      timeoutMs,
      signal,
    });
    return parseWeixinJson(text);
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error?.name === 'AbortError' || /timeout/i.test(String(error?.message))) {
      return { ret: 0, msgs: [], get_updates_buf: cursor };
    }
    throw error;
  }
}

export async function sendItems({ apiBaseUrl, token, to, items, contextToken = null, clientId }) {
  const body = {
    msg: {
      from_user_id: '',
      to_user_id: to,
      client_id: clientId,
      message_type: 2,
      message_state: 2,
      context_token: contextToken || undefined,
      item_list: items,
    },
    base_info: baseInfo(),
  };
  const raw = await request({
    apiBaseUrl,
    endpoint: 'ilink/bot/sendmessage',
    token,
    body,
  });
  const response = parseWeixinJson(raw);
  if (response.ret !== undefined && response.ret !== 0) {
    throw new Error(`Weixin sendMessage failed: ret=${response.ret} ${response.errmsg ?? ''}`.trim());
  }
  return response;
}

export function sendText({ apiBaseUrl, token, to, text, contextToken = null, clientId }) {
  return sendItems({
    apiBaseUrl,
    token,
    to,
    contextToken,
    clientId,
    items: [{ type: 1, text_item: { text } }],
  });
}

export async function getUploadUrl({ apiBaseUrl, token, fileKey, mediaType, to, rawSize,
  rawMd5, encryptedSize, aesKey }) {
  const raw = await request({
    apiBaseUrl,
    endpoint: 'ilink/bot/getuploadurl',
    token,
    body: {
      filekey: fileKey,
      media_type: mediaType,
      to_user_id: to,
      rawsize: rawSize,
      rawfilemd5: rawMd5,
      filesize: encryptedSize,
      no_need_thumb: true,
      aeskey: aesKey,
      base_info: baseInfo(),
    },
  });
  return JSON.parse(raw);
}

async function lifecycle(endpoint, { apiBaseUrl, token, signal = null }) {
  const raw = await request({
    apiBaseUrl,
    endpoint,
    token,
    body: { base_info: baseInfo() },
    signal,
  });
  return JSON.parse(raw);
}

export function notifyStart(options) {
  return lifecycle('ilink/bot/msg/notifystart', options);
}

export function notifyStop(options) {
  return lifecycle('ilink/bot/msg/notifystop', options);
}
