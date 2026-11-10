// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import test from 'node:test';
import { CodexAgent } from '../src/codex.mjs';
import { TraexAgent } from '../src/traex.mjs';
import { PiAgent } from '../src/pi.mjs';
import { ClaudeAgent } from '../src/claude.mjs';
import { PI_MCP_STATUS } from '../src/pi-runtime.mjs';

function fixture(Runtime) {
  const writes = [];
  const logs = [];
  const activities = [];
  const agent = Object.create(Runtime.prototype);
  Object.assign(agent, {
    pending: new Map(), pendingRpc: new Map(), logPrefix: 'test', activitySeq: 0,
    logger: { warn: (event, fields) => logs.push({ event, ...fields }) },
    store: { recordActivity: (row) => activities.push(row) },
    child: { stdin: { writable: true, write: (line) => writes.push(JSON.parse(line)) } },
  });
  return { agent, writes, logs, activities, feed: (message) => agent._handleLine(JSON.stringify(message)) };
}

for (const Runtime of [CodexAgent, TraexAgent]) {
  test(`${Runtime.name} answers server requests without consuming pending client requests or inventing approvals`, () => {
    const { agent, writes, logs, activities, feed } = fixture(Runtime);
    let resolved = false;
    agent.pending.set(0, { resolve: () => { resolved = true; } });
    const cases = [
      ['item/commandExecution/requestApproval', { decision: 'decline' }],
      ['item/fileChange/requestApproval', { decision: 'decline' }],
      // Deny the call but let the turn go on, as the v2 'decline' does.
      ['execCommandApproval', { decision: 'denied' }],
      ['applyPatchApproval', { decision: 'denied' }],
      ['item/permissions/requestApproval', { permissions: {}, scope: 'turn' }],
      ['item/tool/requestUserInput', { answers: {} }],
      ['mcpServer/elicitation/request', { action: 'cancel', content: null }],
    ];
    for (const [method, expected] of cases) {
      // Request ids belong to each direction independently. Also answer requests
      // from an abandoned thread, so the old turn can finish shutting down.
      feed({ id: 0, method, params: { threadId: 'old-thread' } });
      assert.deepEqual(writes.at(-1), { id: 0, result: expected });
      assert.equal(agent.pending.has(0), true);
    }
    feed({ id: 'future-request', method: 'future/interactive', params: {} });
    assert.equal(writes.at(-1).error.code, -32601);
    assert.equal(writes.at(-1).id, 'future-request');
    feed({ method: 'serverRequest/resolved', params: {} });
    assert.equal(writes.length, cases.length + 1, 'notifications get no response');
    feed({ id: 0, result: { models: [] } });
    assert.equal(resolved, true);
    assert.equal(agent.pending.size, 0);
    assert.ok(logs.every(({ reason }) => reason === 'unattended'));
    assert.deepEqual(activities.map(({ kind, metadata }) => [kind, metadata.method]),
      [...cases.map(([method]) => ['agent.interaction_declined', method]), ['agent.interaction_declined', 'future/interactive']]);
  });
}

test('Pi cancels every dialog without waiting for acknowledgement and leaves notifications alone', () => {
  const { agent, writes, logs, activities, feed } = fixture(PiAgent);
  for (const method of ['confirm', 'select', 'input', 'editor']) {
    feed({ type: 'extension_ui_request', id: `dialog-${method}`, method, title: 'secret prompt' });
    assert.deepEqual(writes.at(-1), { id: `dialog-${method}`, type: 'extension_ui_response', cancelled: true });
  }
  assert.equal(agent.pendingRpc.size, 0);
  let ready = false;
  agent.onMcpStartup = (value) => { ready = value; };
  feed({ type: 'extension_ui_request', method: 'notify', message: `${PI_MCP_STATUS}:ready` });
  feed({ type: 'extension_ui_request', method: 'setStatus', id: 'status' });
  assert.equal(ready, true);
  assert.equal(writes.length, 4);
  assert.equal(logs.length, 4);
  assert.equal(activities.length, 4);
  assert.ok(activities.every(({ kind }) => kind === 'agent.interaction_declined'));
  assert.doesNotMatch(JSON.stringify([logs, activities]), /secret prompt/);
});

test('Claude denies unexpected permission controls and returns errors for unsupported controls', () => {
  const { writes, logs, activities, feed } = fixture(ClaudeAgent);
  feed({ type: 'control_request', request_id: 'permission', request: { subtype: 'can_use_tool', tool_name: 'Bash' } });
  assert.equal(writes[0].type, 'control_response');
  assert.equal(writes[0].response.request_id, 'permission');
  assert.equal(writes[0].response.subtype, 'success');
  assert.equal(writes[0].response.response.behavior, 'deny');
  feed({ type: 'control_request', request_id: 'unknown', request: { subtype: 'future_interaction' } });
  assert.equal(writes[1].response.request_id, 'unknown');
  assert.equal(writes[1].response.subtype, 'error');
  feed({ type: 'system', subtype: 'permission_denied' });
  assert.equal(writes.length, 2);
  assert.equal(logs.length, 3);
  assert.deepEqual(activities.map(({ metadata }) => metadata.method), ['can_use_tool', 'future_interaction', 'permission_denied']);
});
