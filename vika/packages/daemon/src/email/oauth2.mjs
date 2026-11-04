// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

// Microsoft's OAuth2 endpoints for Exchange Online mailboxes: the device-code
// sign-in a person completes once, the refreshes the Connector does on its own
// afterwards, and the SASL XOAUTH2 string both mail protocols carry the access
// token in. One token covers IMAP and SMTP.

const AUTHORITY = 'https://login.microsoftonline.com';
// The two Exchange Online delegated scopes, plus `offline_access` for the
// refresh token. One request covers both protocols.
const SCOPE = 'offline_access https://outlook.office.com/IMAP.AccessAsUser.All '
  + 'https://outlook.office.com/SMTP.Send';
const TIMEOUT_MS = 30_000;
// An error body is a short JSON document; a proxy page could be long.
const MAX_BODY_CHARS = 100_000;
const EXPIRY_MARGIN_MS = 5 * 60_000;

// `user=<address>^Aauth=Bearer <access token>^A^A`, base64: the initial
// response both IMAP AUTHENTICATE XOAUTH2 and SMTP AUTH XOAUTH2 carry.
export function xoauth2(user, accessToken) {
  return Buffer.from(`user=${user}\u0001auth=Bearer ${accessToken}\u0001\u0001`, 'utf8').toString('base64');
}

// A token response as the mail protocols need it: the access token, the
// refresh token it came with (rotated ones replace the saved one), and when
// the access token stops working.
function mailTokens(data, previousRefreshToken = '', now = Date.now()) {
  if (typeof data?.access_token !== 'string' || !data.access_token) {
    throw new Error('Microsoft sign-in returned no access token');
  }
  return {
    accessToken: data.access_token,
    refreshToken: typeof data.refresh_token === 'string' && data.refresh_token
      ? data.refresh_token : previousRefreshToken,
    expiresAt: now + Math.max(60, Number(data.expires_in) || 3_600) * 1_000,
  };
}

// `tenant` and `clientId` are the owner's own Entra application; both end up in
// a URL, so only characters a tenant or an app id contains may pass.
export function endpointFor(oauth2, name) {
  const tenant = String(oauth2?.tenant || 'common').trim();
  const clientId = String(oauth2?.clientId ?? '').trim();
  // `.` and `..` are path segments, not tenants: they pass the character class
  // and then the URL quietly loses the tenant entirely. A tenant has to have a
  // name in it, so the dots may not be all of it.
  if (!/^[A-Za-z0-9.-]+$/.test(tenant) || !/[A-Za-z0-9]/.test(tenant)) {
    throw new Error('OAuth2 tenant is invalid');
  }
  if (!/^[A-Za-z0-9._-]+$/.test(clientId)) throw new Error('OAuth2 client ID is invalid');
  return { clientId, url: `${AUTHORITY}/${tenant}/oauth2/v2.0/${name}` };
}

async function post(fetchImpl, url, form) {
  let response;
  let text;
  try {
    response = await fetchImpl(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(form).toString(),
      redirect: 'error',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    text = (await response.text()).slice(0, MAX_BODY_CHARS);
  } catch (error) {
    throw new Error(`Microsoft sign-in endpoint is unreachable: ${error.message}`);
  }
  let data;
  try { data = JSON.parse(text); } catch {
    throw new Error(`Microsoft sign-in endpoint answered HTTP ${response.status} with something unreadable`);
  }
  return { ok: response.ok, status: response.status, data };
}

// What the owner has to do differs by code, so keep the code and the first
// line of Microsoft's description; the rest is stack noise.
function failure(data) {
  const code = String(data?.error ?? 'unknown');
  const description = String(data?.error_description ?? '').split('\n')[0].slice(0, 400);
  if (description.includes('AADSTS65001')) {
    return new Error('Microsoft sign-in needs an administrator to consent to '
      + 'IMAP.AccessAsUser.All and SMTP.Send for this application (AADSTS65001)');
  }
  return new Error(`Microsoft sign-in failed (${code}${description ? `: ${description}` : ''})`);
}

// Starts a device-code sign-in: the address and the short code to type there.
export async function requestDeviceCode(oauth2, { fetch: fetchImpl = globalThis.fetch, now = Date.now } = {}) {
  const { clientId, url } = endpointFor(oauth2, 'devicecode');
  const { ok, status, data } = await post(fetchImpl, url, { client_id: clientId, scope: SCOPE });
  if (!ok) throw failure(data);
  const verificationUri = String(data.verification_uri ?? data.verification_url ?? '');
  if (!data.device_code || !data.user_code || !verificationUri) {
    throw new Error('Microsoft sign-in did not return a device code');
  }
  return {
    deviceCode: String(data.device_code),
    userCode: String(data.user_code),
    verificationUri,
    intervalMs: Math.max(1, Number(data.interval) || 5) * 1_000,
    expiresAt: now() + Math.max(60, Number(data.expires_in) || 900) * 1_000,
  };
}

// Waits until the owner has approved the code. `authorization_pending` is the
// normal answer meanwhile, `slow_down` asks for a longer wait, anything else
// ends the sign-in.
export async function waitForDeviceCode(oauth2, code, {
  fetch: fetchImpl = globalThis.fetch, now = Date.now, sleep = (ms) => new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    timer.unref?.();
  }),
} = {}) {
  const { clientId, url } = endpointFor(oauth2, 'token');
  let intervalMs = Math.max(1_000, Number(code.intervalMs) || 5_000);
  for (;;) {
    if (now() >= code.expiresAt) throw new Error('The Microsoft sign-in code expired; start the sign-in again');
    await sleep(intervalMs);
    const { ok, status, data } = await post(fetchImpl, url, {
      grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
      client_id: clientId,
      device_code: code.deviceCode,
    });
    if (ok) return mailTokens(data, '', now());
    if (data?.error === 'authorization_pending') continue;
    if (data?.error === 'slow_down') { intervalMs += 5_000; continue; }
    throw failure(data);
  }
}

// A fresh access token for an account that already signed in. The refresh
// token rotates; the caller decides whether the new one is written back.
export async function refreshAccessToken(oauth2, { fetch: fetchImpl = globalThis.fetch, now = Date.now } = {}) {
  const refreshToken = String(oauth2?.refreshToken ?? '').trim();
  if (!refreshToken) throw new Error('OAuth2 refresh token is missing');
  const { clientId, url } = endpointFor(oauth2, 'token');
  const { ok, status, data } = await post(fetchImpl, url, {
    grant_type: 'refresh_token',
    client_id: clientId,
    refresh_token: refreshToken,
    scope: SCOPE,
  });
  if (!ok) {
    if (data?.error === 'invalid_grant') {
      throw new Error('The Microsoft sign-in expired; authorize the mailbox again with `vika login email`');
    }
    throw failure(data);
  }
  return mailTokens(data, refreshToken, now());
}

// JSON only the daemon user reads, replaced in one step so a crash cannot
// leave half a credential file behind.
export function writePrivateJson(file, value) {
  const directory = path.dirname(file);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  // `mode` only applies when the directory is created; one that already exists
  // keeps whatever permissions it had, so the file's neighbours stay readable.
  try { fs.chmodSync(directory, 0o700); } catch {}
  const temporary = path.join(directory, `.${path.basename(file)}.${process.pid}.${randomUUID()}.tmp`);
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    fs.chmodSync(temporary, 0o600);
    fs.renameSync(temporary, file);
  } catch (error) {
    try { fs.unlinkSync(temporary); } catch {}
    throw error;
  }
}

// Only the rotated refresh token changes; everything else in the account file
// (the owner's `name`, a `username`, the servers) is kept as written.
//
// `onlyIfRefreshedFrom` guards against a stale write: a refresh spends one
// refresh token and gets a new one, and a sign-in done in the meantime leaves a
// newer token in the file. The write is skipped when the file no longer holds
// the token this refresh spent, so the newer credential is never overwritten.
// Resolves with whether the file was written.
export function saveRefreshToken(file, oauth2, { onlyIfRefreshedFrom } = {}) {
  let value;
  try { value = JSON.parse(fs.readFileSync(file, 'utf8')); } catch {
    throw new Error('email config file is unreadable or not JSON');
  }
  if (onlyIfRefreshedFrom !== undefined
    && String(value?.oauth2?.refreshToken ?? '') !== onlyIfRefreshedFrom) {
    return false;
  }
  value.oauth2 = {
    tenant: oauth2.tenant,
    clientId: oauth2.clientId,
    refreshToken: oauth2.refreshToken,
  };
  writePrivateJson(file, value);
  return true;
}

// An access token is reused until it is about to expire.
export function tokenFresh(tokens, now = Date.now()) {
  return Boolean(tokens) && tokens.expiresAt - EXPIRY_MARGIN_MS > now;
}
