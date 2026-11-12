// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { getConnectorManifest } from '../src/connectors/catalog.mjs';
import { invokeOperation } from '../src/connectors/operations.mjs';
import { LarkClient } from '../src/lark.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

export const larkSuccess = (data) => ({ code: 0, stdout: JSON.stringify({ ok: true, data }), stderr: '', timedOut: false });

// A ConnectorManager stand-in: the real built-in manifests, and every invoke
// recorded. `run(connector, action, input)` scripts the Connector's answer.
export function fakeConnectors({ ids = ['lark'], run = null, owners = {} } = {}) {
  const calls = [];
  const updates = [];
  return {
    calls,
    updates,
    owners,
    async updateCard(messageId, card) { updates.push({ messageId, card }); },
    // An instance id names its connector before any -suffix (email-gmail).
    manifests: () => ids.map((id) => [id, getConnectorManifest(id.split('-')[0])]),
    owner: (id) => owners[id] ?? null,
    async invoke(connector, action, input) {
      calls.push({ connector, action, input });
      return run ? run(connector, action, input) : { message_id: 'om_delivered123456' };
    },
  };
}

// The real Lark Connector client in-process, with lark-cli replaced by
// `run(bin, args, options)`; every CLI call is recorded.
export function larkConnectors({ profile = 'test-profile', tempDir, run }) {
  const calls = [];
  const client = new LarkClient({ larkCliBin: '/fake/lark-cli', profile, projectDir: tempDir, tempDir, sendTimeoutMs: 1_000 }, {
    async run(bin, args, options) {
      calls.push({ bin, args, options });
      return run(bin, args, options);
    },
  }, quietLogger);
  return {
    calls,
    manifests: () => [['lark', getConnectorManifest('lark')]],
    invoke: (_connector, action, input) => invokeOperation(client, action, input),
  };
}
