// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { it } from 'node:test';
import { childConfig } from '../src/connectors/process.mjs';

it('a Lark Connector worker gets the bot-only owner the daemon was started with; no other Connector does', () => {
  const config = { profile: 'superagent-sre', larkCliBin: '/fake/lark-cli', larkOwnerOpenId: 'ou_owner12345678' };
  assert.equal(childConfig(config, { id: 'lark', type: 'lark' }).larkOwnerOpenId, 'ou_owner12345678');
  assert.equal(childConfig(config, { id: 'weixin', type: 'weixin' }).larkOwnerOpenId, null);
  assert.equal(childConfig({ ...config, larkOwnerOpenId: null }, { id: 'lark', type: 'lark' }).larkOwnerOpenId, null);
});
