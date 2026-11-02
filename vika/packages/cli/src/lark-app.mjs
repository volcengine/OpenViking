// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { registerApp } from '@larksuiteoapi/node-sdk';

// Get a Feishu/Lark bot app by QR scan, then read back what it has. The scan
// goes through the official SDK's registerApp (the device flow of the app
// registration endpoint): a hand-written client of the same endpoint saw
// every scan create the app while its poll stayed "pending", so the SDK's own
// HTTP client is the one that is known to complete. One scan creates an app
// (`create`), or picks one the scanner owns or administers (`select`; the page
// can still make a new one), or updates the app given (`update`). The confirm
// page adds what the app lacks from the request below, moves its events and
// callbacks to the long connection, and publishes a version. The platform
// drops unknown scope names silently, so the caller compares the granted
// scopes with the request.

// Messages to the bot, and comments on documents that mention it or answer
// it (drive.notice.comment_add_v1).
export const EVENTS = Object.freeze(['im.message.receive_v1', 'drive.notice.comment_add_v1']);
export const MESSAGE_EVENT = 'im.message.receive_v1';
export const CALLBACKS = Object.freeze(['card.action.trigger']);

export class LarkRegistrationError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'LarkRegistrationError';
    this.code = code;
  }
}

// The App ID and App Secret were refused (or belong to the other tenant brand).
export class LarkCredentialsError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'LarkCredentialsError';
    this.code = code;
  }
}

// The App ID and App Secret are right, but the access policy of the account
// the app belongs to refuses it a token (21001, as lark-cli classifies it).
// Feishu signed in with a Doubao account is refused so: it can make and
// publish an app, never run it.
export class LarkPolicyError extends LarkCredentialsError {
  constructor(code, message) {
    super(code, message);
    this.name = 'LarkPolicyError';
  }
}

const POLICY_REFUSED = 21001;

export function openApiBase(brand) {
  return brand === 'lark' ? 'https://open.larksuite.com' : 'https://open.feishu.cn';
}

// scopes: { tenant: [...], user: [...] }. onQRCode({ url, expiresIn }) shows
// the code; onStatus gets polling / slow_down / domain_switched. `appId` is
// the app an `update` scan changes. Resolves with
// { appId, appSecret, brand, ownerOpenId }.
export async function scanApp({
  mode = 'create',
  appId = null,
  name,
  description,
  scopes,
  onQRCode,
  onStatus = () => {},
  signal = undefined,
  register = registerApp,
}) {
  let result;
  try {
    result = await register({
      source: 'vika',
      ...(mode === 'create' ? { createOnly: true } : {}),
      ...(mode === 'update' ? { appId } : { appPreset: { ...(name ? { name } : {}), ...(description ? { desc: description } : {}) } }),
      addons: {
        scopes: { tenant: [...scopes.tenant], user: [...scopes.user] },
        events: { items: { tenant: [...EVENTS] } },
        callbacks: { items: [...CALLBACKS] },
      },
      signal,
      onQRCodeReady: ({ url, expireIn }) => onQRCode({ url, expiresIn: expireIn }),
      onStatusChange: ({ status }) => onStatus(status),
    });
  } catch (error) {
    // The SDK rejects with a plain { code, description } for platform outcomes.
    if (error instanceof Error) throw error;
    throw new LarkRegistrationError(error?.code ?? 'unknown', error?.description ?? 'app registration failed');
  }
  if (!result?.client_id || !result?.client_secret) {
    throw new LarkRegistrationError('invalid_response', 'app registration returned no credentials');
  }
  return {
    appId: result.client_id,
    appSecret: result.client_secret,
    brand: result.user_info?.tenant_brand === 'lark' ? 'lark' : 'feishu',
    ownerOpenId: result.user_info?.open_id ?? null,
  };
}

// The app's own token: the check that an App ID and App Secret are right.
export async function tenantToken({ appId, appSecret, brand }, { fetchImpl = globalThis.fetch } = {}) {
  const response = await fetchImpl(`${openApiBase(brand)}/open-apis/auth/v3/tenant_access_token/internal`, {
    method: 'POST',
    headers: { 'content-type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
    signal: AbortSignal.timeout(15_000),
  });
  const token = await response.json().catch(() => ({ code: -1, msg: `HTTP ${response.status} without JSON` }));
  if (token.code !== 0 || !token.tenant_access_token) {
    const Refused = token.code === POLICY_REFUSED ? LarkPolicyError : LarkCredentialsError;
    throw new Refused(token.code, `tenant_access_token failed: code=${token.code} ${token.msg ?? ''}`.trim());
  }
  return token.tenant_access_token;
}

const APP_INFO = '/open-apis/application/v6/applications/me';

// What an app has: its name, online version, bot, granted scopes, card
// callbacks, and the events of its online version (null when there is none,
// or it cannot be read). get(pathname, query) resolves with the response's
// { code, msg, data }; the app's own information has to be readable.
export async function readApp(get) {
  const info = await get(APP_INFO, { lang: 'zh_cn' });
  const app = info.code === 0 ? info.data?.app : null;
  if (!app) throw new Error(`reading the app failed: code=${info.code} ${info.msg ?? ''}`.trim());
  const bot = await get('/open-apis/bot/v3/info', {});
  let events = null;
  if (app.online_version_id && app.app_id) {
    const version = await get(`/open-apis/application/v6/applications/${app.app_id}/app_versions/${app.online_version_id}`,
      { lang: 'zh_cn', user_id_type: 'open_id' });
    const infos = version.code === 0 ? version.data?.app_version?.event_infos : null;
    if (Array.isArray(infos)) events = infos.map(({ event_type: type }) => type).filter(Boolean);
  }
  return {
    appId: app.app_id ?? null,
    name: app.app_name ?? null,
    online: Boolean(app.online_version_id),
    pendingReview: Boolean(app.unaudit_version_id),
    botActive: bot.code === 0 && bot.data?.activate_status === 2,
    botName: bot.code === 0 ? bot.data?.app_name ?? null : null,
    granted: (app.scopes ?? []).map(({ scope, token_types: tokenTypes }) => ({ scope, tokenTypes: tokenTypes ?? [] })),
    callbacks: { type: app.callback_info?.callback_type ?? null, items: app.callback_info?.subscribed_callbacks ?? [] },
    events,
  };
}

async function openApi(fetchImpl, brand, token, pathname, query) {
  const search = new URLSearchParams(query).toString();
  const response = await fetchImpl(`${openApiBase(brand)}${pathname}${search ? `?${search}` : ''}`, {
    headers: { authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(15_000),
  });
  const body = await response.json().catch(() => ({ code: -1, msg: `HTTP ${response.status} without JSON` }));
  // The bot info answers with `bot` where the others have `data`.
  return { code: body.code, msg: body.msg, data: body.data ?? body.bot };
}

// readApp with the App Secret in hand (or the token it was just checked
// with). Rejects with LarkCredentialsError when the credentials are refused.
export async function inspectApp(app, { fetchImpl = globalThis.fetch, token = null } = {}) {
  const bearer = token ?? await tenantToken(app, { fetchImpl });
  return readApp((pathname, query) => openApi(fetchImpl, app.brand, bearer, pathname, query));
}

// Requested scopes the platform did not grant, per token type.
export function missingScopes(scopes, granted) {
  const has = (scope, type) => granted.some((entry) => entry.scope === scope && entry.tokenTypes.includes(type));
  return {
    tenant: scopes.tenant.filter((scope) => !has(scope, 'tenant')),
    user: scopes.user.filter((scope) => !has(scope, 'user')),
  };
}

// What the app lacks of the request: scopes per token type, the events
// (null when the online version could not be read) and the card callback,
// which counts only on the long connection.
export function missingConfig(scopes, inspected) {
  const { type, items } = inspected.callbacks;
  return {
    scopes: missingScopes(scopes, inspected.granted),
    events: inspected.events ? EVENTS.filter((event) => !inspected.events.includes(event)) : null,
    callbacks: CALLBACKS.filter((callback) => type !== 'websocket' || !items.includes(callback)),
  };
}

export function lacksAny(missing) {
  return missing.scopes.tenant.length + missing.scopes.user.length + (missing.events?.length ?? 0) + missing.callbacks.length > 0;
}

// The user scopes to ask the owner for: those the app has, when it has any.
// The authorization cannot grant a scope the app was not given.
export function grantedUserScopes(scopes, inspected) {
  const missing = new Set(inspected ? missingScopes(scopes, inspected.granted).user : []);
  const granted = scopes.user.filter((scope) => !missing.has(scope));
  return granted.length ? granted : scopes.user;
}
