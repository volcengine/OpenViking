// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import test from 'node:test';
import {
  CALLBACKS, EVENTS, LarkCredentialsError, LarkPolicyError, grantedUserScopes, inspectApp, lacksAny, missingConfig, missingScopes, scanApp,
} from '../src/lark-app.mjs';

const SCOPES = { tenant: ['im:message:send_as_bot', 'im:message.group_msg'], user: ['search:message'] };

test('one scan creates the app through the official SDK, with the full request', async () => {
  let options;
  const statuses = [];
  const shown = [];
  const app = await scanApp({
    name: 'Vika', description: 'desc', scopes: SCOPES,
    onQRCode: (qr) => shown.push(qr), onStatus: (status) => statuses.push(status),
    register: async (given) => {
      options = given;
      given.onQRCodeReady({ url: 'https://open.feishu.cn/page/launcher?user_code=A', expireIn: 3600 });
      given.onStatusChange({ status: 'polling' });
      return { client_id: 'cli_new', client_secret: 'secret', user_info: { open_id: 'ou_owner', tenant_brand: 'feishu' } };
    },
  });
  assert.deepEqual(app, { appId: 'cli_new', appSecret: 'secret', brand: 'feishu', ownerOpenId: 'ou_owner' });
  assert.equal(options.source, 'vika');
  assert.equal(options.createOnly, true);
  assert.deepEqual(options.appPreset, { name: 'Vika', desc: 'desc' });
  assert.deepEqual(options.addons, { scopes: SCOPES, events: { items: { tenant: EVENTS } }, callbacks: { items: CALLBACKS } });
  assert.deepEqual(shown, [{ url: 'https://open.feishu.cn/page/launcher?user_code=A', expiresIn: 3600 }]);
  assert.deepEqual(statuses, ['polling']);
});

test('picking an existing app leaves the page its app list; updating one names it', async () => {
  const seen = [];
  const register = async (given) => {
    seen.push(given);
    return { client_id: 'cli_old', client_secret: 's', user_info: { open_id: 'ou_owner' } };
  };
  await scanApp({ mode: 'select', name: 'Vika', scopes: SCOPES, onQRCode() {}, register });
  await scanApp({ mode: 'update', appId: 'cli_old', name: 'Vika', scopes: SCOPES, onQRCode() {}, register });
  const [select, update] = seen;
  assert.equal('createOnly' in select, false, 'the landing page offers the apps the scanner owns or administers');
  assert.equal('appId' in select, false);
  assert.deepEqual(select.appPreset, { name: 'Vika' }, 'making a new one there still gets the name');
  assert.equal(update.appId, 'cli_old');
  assert.equal('createOnly' in update, false, 'createOnly would turn the update back into a create');
  assert.equal('appPreset' in update, false, 'an update keeps the app\'s name');
  for (const given of seen) {
    assert.deepEqual(given.addons, { scopes: SCOPES, events: { items: { tenant: EVENTS } }, callbacks: { items: CALLBACKS } });
  }
});

test('a Lark tenant is recognised, and platform refusals keep their code', async () => {
  const lark = await scanApp({ scopes: SCOPES, onQRCode() {},
    register: async () => ({ client_id: 'cli_lark', client_secret: 's', user_info: { tenant_brand: 'lark' } }) });
  assert.equal(lark.brand, 'lark');
  await assert.rejects(scanApp({ scopes: SCOPES, onQRCode() {},
    register: async () => { throw { code: 'access_denied', description: 'user cancelled' }; } }),
  { name: 'LarkRegistrationError', code: 'access_denied', message: 'user cancelled' });
  await assert.rejects(scanApp({ scopes: SCOPES, onQRCode() {}, register: async () => ({ client_id: 'cli_x' }) }),
    { code: 'invalid_response' });
});

// The platform as the app's own token sees it.
function platform({ app = {}, version = null, token = { code: 0, tenant_access_token: 't-1' } } = {}) {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    calls.push({ url, auth: init.headers?.authorization });
    if (url.endsWith('/tenant_access_token/internal')) return { json: async () => token };
    if (url.includes('/applications/me?')) {
      return { json: async () => ({ code: 0, data: { app: {
        app_id: 'cli_1', app_name: '小助手', online_version_id: 'oav_1', unaudit_version_id: '',
        scopes: [{ scope: 'im:message:send_as_bot', token_types: ['tenant'] }, { scope: 'search:message', token_types: ['user'] }],
        callback_info: { callback_type: 'websocket', subscribed_callbacks: ['card.action.trigger'] },
        ...app,
      } } }) };
    }
    if (url.includes('/app_versions/oav_1?')) {
      return { json: async () => (version ?? { code: 0, data: { app_version: { event_infos: [
        { event_type: 'im.message.receive_v1' }, { event_type: 'drive.notice.comment_add_v1' },
      ] } } }) };
    }
    return { json: async () => ({ code: 0, bot: { activate_status: 2, app_name: '小助手' } }) };
  };
  return { calls, fetchImpl };
}

test('reads back what the app has and names what is missing', async () => {
  const { calls, fetchImpl } = platform();
  const inspected = await inspectApp({ appId: 'cli_1', appSecret: 's', brand: 'feishu' }, { fetchImpl });
  assert.equal(inspected.appId, 'cli_1');
  assert.equal(inspected.online, true);
  assert.equal(inspected.pendingReview, false);
  assert.equal(inspected.botActive, true);
  assert.equal(inspected.botName, '小助手');
  assert.deepEqual(inspected.events, ['im.message.receive_v1', 'drive.notice.comment_add_v1']);
  assert.ok(calls.slice(1).every(({ auth }) => auth === 'Bearer t-1'));
  assert.ok(calls.some(({ url }) => url === 'https://open.feishu.cn/open-apis/application/v6/applications/me?lang=zh_cn'));
  assert.deepEqual(missingScopes(SCOPES, inspected.granted), { tenant: ['im:message.group_msg'], user: [] });
  const missing = missingConfig(SCOPES, inspected);
  assert.deepEqual(missing, { scopes: { tenant: ['im:message.group_msg'], user: [] }, events: [], callbacks: [] });
  assert.equal(lacksAny(missing), true);
  assert.equal(lacksAny(missingConfig({ tenant: ['im:message:send_as_bot'], user: [] }, inspected)), false);
});

test('an app made by hand may lack the message event or have its callbacks on a webhook', async () => {
  const { fetchImpl } = platform({
    app: { callback_info: { callback_type: 'webhook', subscribed_callbacks: ['card.action.trigger'] } },
    version: { code: 0, data: { app_version: { event_infos: [{ event_type: 'im.chat.member.bot.added_v1' }] } } },
  });
  const inspected = await inspectApp({ appId: 'cli_1', appSecret: 's', brand: 'feishu' }, { fetchImpl });
  assert.deepEqual(missingConfig(SCOPES, inspected), {
    scopes: { tenant: ['im:message.group_msg'], user: [] }, events: ['im.message.receive_v1', 'drive.notice.comment_add_v1'],
    callbacks: ['card.action.trigger'],
  });
  // No callbacks at all, and no online version to read the events of.
  const bare = platform({ app: { callback_info: undefined, online_version_id: '' } });
  const unpublished = await inspectApp({ appId: 'cli_1', appSecret: 's', brand: 'feishu' }, { fetchImpl: bare.fetchImpl });
  assert.equal(unpublished.online, false);
  assert.equal(unpublished.events, null);
  assert.deepEqual(missingConfig(SCOPES, unpublished).callbacks, ['card.action.trigger']);
  assert.ok(!bare.calls.some(({ url }) => url.includes('/app_versions/')));
});

test('refused credentials are told apart from an app whose settings cannot be read', async () => {
  const refused = platform({ token: { code: 10014, msg: 'app secret invalid' } });
  await assert.rejects(inspectApp({ appId: 'cli_1', appSecret: 'bad', brand: 'feishu' }, { fetchImpl: refused.fetchImpl }),
    { name: 'LarkCredentialsError', code: 10014 });
  const fetchImpl = async (url) => ({ json: async () => (url.endsWith('/internal')
    ? { code: 0, tenant_access_token: 't' } : { code: 99991672, msg: 'no permission' }) });
  await assert.rejects(inspectApp({ appId: 'cli_1', appSecret: 's', brand: 'lark' }, { fetchImpl }),
    (error) => error.name === 'Error' && /code=99991672/.test(error.message));
});

test('a token the account\'s policy refuses is told apart from wrong credentials', async () => {
  const policy = platform({ token: { code: 21001, msg: 'access denied by access control policy' } });
  await assert.rejects(inspectApp({ appId: 'cli_1', appSecret: 's', brand: 'feishu' }, { fetchImpl: policy.fetchImpl }),
    (error) => error instanceof LarkPolicyError && error instanceof LarkCredentialsError && error.code === 21001
      && /code=21001 access denied by access control policy/.test(error.message));
  const wrong = platform({ token: { code: 10014, msg: 'app secret invalid' } });
  await assert.rejects(inspectApp({ appId: 'cli_1', appSecret: 'bad', brand: 'feishu' }, { fetchImpl: wrong.fetchImpl }),
    (error) => error instanceof LarkCredentialsError && !(error instanceof LarkPolicyError));
});

test('the owner is asked only for user scopes the app has, unless it has none of them', async () => {
  const scopes = { tenant: [], user: ['search:message', 'calendar:calendar:read'] };
  const granted = (list) => ({ granted: list.map((scope) => ({ scope, tokenTypes: ['user'] })) });
  assert.deepEqual(grantedUserScopes(scopes, granted(['search:message'])), ['search:message']);
  assert.deepEqual(grantedUserScopes(scopes, granted([])), scopes.user);
  assert.deepEqual(grantedUserScopes(scopes, null), scopes.user, 'unread, every scope is asked for');
});
