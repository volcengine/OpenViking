// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import http from 'node:http';
import { after, before, describe, test } from 'node:test';
import { startFakeProvider } from './fake-provider.mjs';
import {
  cleanupLiveDshRoot, hermeticDshEnv, prepareLiveDshRoot, realDshAvailability, startLiveDsh,
} from './real-dsh.mjs';
import { localSocketPath } from '../src/platform/index.mjs';
import { WorkerExecutor } from '../src/worker-executor.mjs';

// A full worker turn through the real DSH model loop, with no external model or
// credential. Two native sessions run concurrently in one profile and receive
// independent results through the same WorkerExecutor bridge contract.
const available = await realDshAvailability();
describe('DSH workers live', { skip: available.ok ? false : available.reason }, () => {
  let root;
  let provider;
  let dsh;
  let actionServer;
  before(async () => {
    if (!available.ok) return;
    provider = await startFakeProvider({ reply: 'worker completed' });
    root = prepareLiveDshRoot({ prefix: 'superagent-dsh-worker-live-' });
    const socketPath = localSocketPath(root.dataDir);
    actionServer = http.createServer((request, response) => {
      response.setHeader('content-type', 'application/json');
      const tools = [
        { name: 'task_check', description: 'Read task inputs', inputSchema: { type: 'object', properties: {} } },
        { name: 'task_report', description: 'Report task state', inputSchema: { type: 'object', properties: {} } },
      ];
      response.end(JSON.stringify(request.url === '/v1/tools' ? { tools } : { ok: false, error: 'not implemented' }));
    });
    await new Promise((resolve, reject) => {
      actionServer.once('error', reject);
      actionServer.listen(socketPath, resolve);
    });
    dsh = await startLiveDsh({
      root,
      env: hermeticDshEnv({
        root, apiKey: 'worker-live-key', baseURL: provider.baseURL,
        env: { ...process.env, PERSONAL_AGENT_SOCKET: socketPath },
      }),
    });
    await dsh.rpc('initialize', {
      cwd: root.workspace, provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'high',
    });
  });
  after(async () => {
    await dsh?.stop();
    await provider?.close();
    if (actionServer) await new Promise(resolve => actionServer.close(resolve));
    cleanupLiveDshRoot(root?.root);
  });

  test('two workers complete independent real model turns in one DSH profile', async () => {
    const executor = new WorkerExecutor({
      config: {
        dataDir: root.dataDir, workspace: root.workspace, agentName: 'WorkerLive',
        sendTimeoutMs: 30_000, dshStartTimeoutMs: 30_000,
      },
      settings: () => ({
        runtime: 'dsh', model: 'deepseek-official/deepseek-flash', effort: 'high',
        dshRequest: (method, params, timeoutMs) => dsh.rpc(method, params, { timeoutMs: timeoutMs || 60_000 }),
      }),
    });
    const task = (id) => ({
      id, runtime: 'dsh', session_id: `worker-${id}`, native_session: null,
      workdir: root.workspace, turns: 1,
    });
    const [one, two] = await Promise.all([
      executor.start({ task: task('one'), prompt: 'Task one: return the fixed answer.', authority: 'one-authority' }).done,
      executor.start({ task: task('two'), prompt: 'Task two: return the fixed answer.', authority: 'two-authority' }).done,
    ]);
    for (const [taskId, result] of [['one', one], ['two', two]]) {
      assert.equal(result.error, null, `${taskId}: ${JSON.stringify(result)}\n${dsh.stderr()}`);
      assert.equal(result.text, 'worker completed');
      assert.equal(result.nativeSession, `worker-${taskId}`);
      assert.ok(result.usage.outputTokens > 0);
    }
    const prompts = provider.requests.filter(request => request.path.endsWith('/messages'));
    assert.equal(prompts.length, 2);
    assert.ok(prompts.some(request => request.raw.includes('Task one')));
    assert.ok(prompts.some(request => request.raw.includes('Task two')));
  });
});
