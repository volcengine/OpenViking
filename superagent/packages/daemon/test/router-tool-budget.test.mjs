// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { RouterToolBudget } from '../src/router-tool-budget.mjs';
import { routerToolHookOutput } from '../src/router-tool-hook.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { startActionApi, stopActionApi } from '../src/actions/api.mjs';
import { requestAction } from '../src/actions/client.mjs';

it('shares the six-call budget across native work tools, leaving routing and transport usable', () => {
  const budget = new RouterToolBudget();
  for (const name of ['bash', 'Read', 'apply_patch', 'Glob', 'exec_command', 'grep_files']) assert.equal(budget.check(name), null);
  for (const name of ['Bash', 'Edit', 'ls', 'WebFetch']) assert.match(budget.check(name), /used your own tools 6 times/);
  for (const name of ['mcp__personal_agent__task_create', 'mcp__personal_agent__reply_message', 'mcp__personal_agent__search_messages',
    'mcp__other__lookup', 'functions.exec', 'exec', 'write_stdin', 'update_plan']) assert.equal(budget.check(name), null);
});

it('denies before execution and keeps routing usable when the budget endpoint fails', async () => {
  const event = { hook_event_name: 'PreToolUse', tool_name: 'Bash' };
  assert.equal(await routerToolHookOutput(event, async () => ({ reason: null })), null);
  const output = await routerToolHookOutput(event, async () => ({ reason: 'exhausted' }));
  assert.deepEqual(output, { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'deny', permissionDecisionReason: 'exhausted' } });
  const offline = async () => { throw new Error('offline'); };
  assert.equal((await routerToolHookOutput(event, offline)).hookSpecificOutput.permissionDecision, 'deny');
  assert.equal(await routerToolHookOutput({ ...event, tool_name: 'mcp__personal_agent__task_create' }, offline), null);
});

it('serializes parallel hook processes, resets at turn end and authority change, and excludes workers', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'router-budget-'));
  let authority = 'router-1';
  const service = new ActionService({ config: { scheduler: 'workers' },
    store: { getCognitionState: () => ({ authorityToken: authority }) }, logger: { warn() {} } });
  service.tasks = { workerFor: (token) => token === 'worker' ? 't1' : null };
  const socketPath = path.join(dir, 'actions.sock');
  const server = await startActionApi({ socketPath, service });
  t.after(async () => { await stopActionApi(server); fs.rmSync(dir, { recursive: true, force: true }); });
  const check = (token = authority, tool = 'Bash') => requestAction({ socketPath, authority: token, client: 'hook' },
    'POST', '/v1/router-tool-check', { tool });
  const results = await Promise.all(Array.from({ length: 10 }, () => check()));
  assert.equal(results.filter((r) => r.ok && r.result.reason === null).length, 6);
  assert.equal(results.filter((r) => r.ok && r.result.reason).length, 4);
  assert.equal((await check('worker')).result.reason, null);
  assert.equal((await check(authority, 'mcp__personal_agent__task_create')).result.reason, null);
  assert.ok((await check()).result.reason, 'neither worker calls nor inbox/routing calls replenish the budget');
  service.resetLoopGuard();
  assert.equal((await check()).result.reason, null);
  authority = 'router-2';
  assert.equal((await check('router-1')).ok, false, 'revoked runtimes cannot spend or reset the budget');
  assert.equal((await check()).result.reason, null);
  service.config.scheduler = 'inbox';
  for (let i = 0; i < 8; i++) assert.equal((await check()).result.reason, null);
});
