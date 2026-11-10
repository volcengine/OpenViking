// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  applyOpenVikingChildEnv,
  isOpenVikingEnvKey,
  openVikingChildEnv,
} from '../src/openviking.mjs';
import { sanitizeAgentChildEnv } from '../src/process-utils.mjs';

describe('OpenViking env seam', () => {
  it('copies only OPENVIKING_ keys', () => {
    const env = openVikingChildEnv({
      PATH: '/bin',
      OPENVIKING_CLI_CONFIG_FILE: '/tmp/ov.conf',
      OPENVIKING_AUTO_RECALL: '0',
      LARK_APP_SECRET: 'no',
    });
    assert.deepEqual(env, {
      OPENVIKING_CLI_CONFIG_FILE: '/tmp/ov.conf',
      OPENVIKING_AUTO_RECALL: '0',
    });
    assert.equal(isOpenVikingEnvKey('OPENVIKING_CLI_CONFIG_FILE'), true);
    assert.equal(isOpenVikingEnvKey('VIKA_DATA_DIR'), false);
  });

  it('is the only writer of OPENVIKING_ keys on a child env', () => {
    const next = applyOpenVikingChildEnv({
      PATH: '/bin',
      OPENVIKING_CLI_CONFIG_FILE: 'stale',
      OPENVIKING_AUTO_CAPTURE: 'drop-me',
    }, {
      OPENVIKING_CLI_CONFIG_FILE: '/tmp/fresh.conf',
    });
    assert.equal(next.PATH, '/bin');
    assert.equal(next.OPENVIKING_CLI_CONFIG_FILE, '/tmp/fresh.conf');
    assert.equal(next.OPENVIKING_AUTO_CAPTURE, undefined);
  });

  it('lets agent children keep OPENVIKING_ after Lark/Ark stripping', () => {
    const env = sanitizeAgentChildEnv({
      PATH: '/bin',
      LARK_APP_SECRET: 'no',
      ARK_API_KEY: 'no',
      VIKA_EMAIL_ACCOUNTS_JSON: '[{"id":"email-gmail","config":"/private/mailbox.json"}]',
      OPENVIKING_CLI_CONFIG_FILE: '/tmp/ov.conf',
      OPENVIKING_MEMORY_ENABLED: '1',
    }, { profile: 'fixed' });
    assert.equal(env.LARK_APP_SECRET, undefined);
    // The daemon's own settings never reach a runtime.
    assert.equal(env.VIKA_EMAIL_ACCOUNTS_JSON, undefined);
    assert.equal(env.ARK_API_KEY, undefined);
    assert.equal(env.OPENVIKING_CLI_CONFIG_FILE, '/tmp/ov.conf');
    assert.equal(env.OPENVIKING_MEMORY_ENABLED, '1');
    assert.equal(env.LARKSUITE_CLI_PROFILE, 'fixed');
  });
});
