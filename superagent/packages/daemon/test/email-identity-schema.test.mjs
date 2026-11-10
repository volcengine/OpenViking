// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import { it } from 'node:test';
import { TOOLS, validateInput } from '../src/actions/tools.mjs';
import { mailboxIdentity } from '../src/email/mailbox.mjs';

it('read_email accepts both legacy and folder-scoped message IDs', () => {
  const tool = TOOLS.find(({ name }) => name === 'read_email');
  for (const folder of ['INBOX', 'Projects', '收件箱']) {
    assert.doesNotThrow(() => validateInput(tool.inputSchema, {
      message_id: `mail:${mailboxIdentity('12345678', folder, 1, 2)}`,
    }));
  }
  assert.throws(() => validateInput(tool.inputSchema, { message_id: 'mail:12345678:../escape:1:2' }));
});
