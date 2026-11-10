// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { fetchLoginQr, pollLoginQr, DEFAULT_WEIXIN_API_BASE_URL } from './api.mjs';

const LOGIN_TIMEOUT_MS = 8 * 60_000;

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readLine(prompt) {
  process.stdout.write(prompt);
  process.stdin.resume();
  process.stdin.setEncoding('utf8');
  return new Promise((resolve) => {
    let input = '';
    const onData = (chunk) => {
      input += chunk;
      const index = input.indexOf('\n');
      if (index < 0) return;
      process.stdin.off('data', onData);
      process.stdin.pause();
      resolve(input.slice(0, index).trim());
    };
    process.stdin.on('data', onData);
  });
}

async function displayQr(value) {
  try {
    const module = await import('qrcode-terminal');
    const qr = module.default ?? module;
    qr.generate(value, { small: true });
  } catch {
    process.stdout.write('终端二维码组件不可用，请打开下面的链接后扫码。\n');
  }
  process.stdout.write(`${value}\n`);
}

function redirectedBaseUrl(host) {
  if (typeof host !== 'string' || !host.trim()) return null;
  const candidate = host.includes('://') ? host : `https://${host}`;
  const parsed = new URL(candidate);
  if (parsed.protocol !== 'https:') throw new Error('Weixin login redirect must use HTTPS');
  return parsed.origin;
}

function sayLine(text) {
  process.stdout.write(`${text}\n`);
}

// readVerifyCode, showQr and say let `vika setup` ask and draw through its
// own prompt: showQr(value, { refreshed }) gets each code to scan, say(text,
// { kind, accountId }) each status line. `kind` (scanned, already-bound,
// connected) and an error's `code` let the caller say it in its own words.
// Once `signal` aborts, the login stops and binds nothing, even if the scan
// is confirmed on the phone afterwards.
export async function loginWeixin({
  state, timeoutMs = LOGIN_TIMEOUT_MS, readVerifyCode = readLine, showQr = null, say = sayLine, signal = null,
} = {}) {
  const stopped = () => {
    if (signal?.aborted) throw Object.assign(new Error('Weixin login cancelled'), { name: 'AbortError' });
  };
  const display = showQr ?? (async (value, { refreshed }) => {
    say(refreshed ? '二维码已刷新，请重新扫码：' : '用手机微信扫描以下二维码并确认授权：');
    await displayQr(value);
  });
  const existing = state.account();
  let apiBaseUrl = existing?.baseUrl || DEFAULT_WEIXIN_API_BASE_URL;
  let qr = await fetchLoginQr({ localTokens: existing?.token ? [existing.token] : [] });
  if (!qr?.qrcode || !qr?.qrcode_img_content) throw new Error('Weixin QR endpoint returned no QR code');
  await display(qr.qrcode_img_content, { refreshed: false });

  const deadline = Date.now() + timeoutMs;
  let scanned = false;
  let verifyCode = null;
  while (Date.now() < deadline) {
    stopped();
    const status = await pollLoginQr({ qrcode: qr.qrcode, verifyCode, apiBaseUrl, signal });
    stopped();
    verifyCode = null;
    if (status.status === 'wait') {
      await delay(1_000);
      continue;
    }
    if (status.status === 'scaned') {
      if (!scanned) say('已扫码，请在手机上确认。', { kind: 'scanned' });
      scanned = true;
      await delay(1_000);
      continue;
    }
    if (status.status === 'need_verifycode') {
      verifyCode = await readVerifyCode('请输入微信显示的验证码：');
      if (!/^\d{4,8}$/.test(verifyCode)) throw loginError('验证码格式无效', 'weixin_bad_verify_code');
      continue;
    }
    if (status.status === 'verify_code_blocked') {
      throw loginError('验证码错误次数过多，请稍后重新扫码', 'weixin_verify_code_blocked');
    }
    if (status.status === 'scaned_but_redirect') {
      apiBaseUrl = redirectedBaseUrl(status.redirect_host) ?? apiBaseUrl;
      await delay(1_000);
      continue;
    }
    if (status.status === 'expired') {
      qr = await fetchLoginQr({ localTokens: existing?.token ? [existing.token] : [] });
      if (!qr?.qrcode || !qr?.qrcode_img_content) throw new Error('Weixin QR refresh returned no QR code');
      scanned = false;
      await display(qr.qrcode_img_content, { refreshed: true });
      continue;
    }
    if (status.status === 'binded_redirect') {
      if (!existing?.token) throw loginError('微信端显示已绑定，但本地没有可用凭证', 'weixin_bound_elsewhere');
      say(`本地已有微信账号 ${existing.accountId} 的凭证，无需重复绑定。`, { kind: 'already-bound', accountId: existing.accountId });
      return existing;
    }
    if (status.status === 'confirmed') {
      if (!status.bot_token || !status.ilink_bot_id) {
        throw new Error('Weixin login confirmation did not include account credentials');
      }
      const account = state.saveAccount({
        accountId: status.ilink_bot_id,
        token: status.bot_token,
        baseUrl: status.baseurl || apiBaseUrl,
        userId: status.ilink_user_id || null,
      });
      say(`微信连接成功：${account.accountId}`, { kind: 'connected', accountId: account.accountId });
      return account;
    }
    throw new Error(`Unsupported Weixin login status: ${status.status ?? 'unknown'}`);
  }
  throw loginError('微信扫码登录超时，请重新执行登录命令', 'weixin_login_timeout');
}

function loginError(message, code) {
  return Object.assign(new Error(message), { code });
}
