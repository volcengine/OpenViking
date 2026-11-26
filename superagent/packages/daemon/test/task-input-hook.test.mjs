// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { localSocketPath } from '../src/platform/index.mjs';
import { TASK_INPUT_HOOK, taskInputHookOutput } from '../src/task-input-hook.mjs';

const INPUTS = 'New inputs for your task, oldest first:\n[chat=oc_a msg=om_2 event=e2] 只看上周';

describe('task input hook (Claude Code / Codex workers)', () => {
  it('adds new task inputs after a tool call, and keeps a run that is about to end going', async () => {
    const found = async () => ({ text: INPUTS, data: { count: 1 } });
    const after = await taskInputHookOutput({ hook_event_name: 'PostToolUse', tool_name: 'Bash' }, found);
    assert.equal(after.hookSpecificOutput.hookEventName, 'PostToolUse');
    assert.match(after.hookSpecificOutput.additionalContext, /^\[task input\] New inputs for your task, oldest first:\n.*只看上周\nHandle what changes or answers your task; hand back with task_handback/);
    const stop = await taskInputHookOutput({ hook_event_name: 'Stop', stop_hook_active: true }, found);
    assert.equal(stop.decision, 'block');
    assert.match(stop.reason, /^\[task input\] .*只看上周/s);
  });

  it('leaves the run alone with nothing new, on other events, and when the daemon cannot be reached', async () => {
    const none = async () => ({ text: 'No new inputs for your task.', data: { count: 0 } });
    assert.equal(await taskInputHookOutput({ hook_event_name: 'Stop' }, none), null);
    assert.equal(await taskInputHookOutput({ hook_event_name: 'PreToolUse' }, async () => { throw new Error('not asked'); }), null);
    assert.equal(await taskInputHookOutput({ hook_event_name: 'PostToolUse' }, async () => { throw new Error('ECONNREFUSED'); }), null);
    assert.equal(await taskInputHookOutput(null), null);
  });

  it('runs as the hook command a CLI starts, under the worker authority', async (t) => {
    // The daemon's action socket, in its own process: spawnSync blocks this one.
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hook-'));
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    // A named pipe on Windows, where a socket path in the filesystem is not
    // something an AF_UNIX listener can bind.
    const socketPath = localSocketPath(dir, 'hook');
    const serverScript = path.join(dir, 'server.mjs');
    fs.writeFileSync(serverScript, `import http from 'node:http'; import fs from 'node:fs';
      http.createServer((q, s) => { let b = ''; q.on('data', (c) => { b += c; }); q.on('end', () => {
        fs.appendFileSync(${JSON.stringify(path.join(dir, 'calls'))}, JSON.stringify({ ...JSON.parse(b), authority: q.headers['x-agent-authority'] }) + '\\n');
        s.setHeader('content-type', 'application/json'); s.end(JSON.stringify({ ok: true, result: { text: ${JSON.stringify(INPUTS)}, data: { count: 1 } } })); }); })
        .listen(${JSON.stringify(socketPath)}, () => console.log('ready'));`);
    const daemon = spawn(process.execPath, [serverScript], { stdio: ['ignore', 'pipe', 'inherit'] });
    t.after(() => daemon.kill());
    await new Promise((resolve) => daemon.stdout.once('data', resolve));
    const child = spawnSync(process.execPath, [TASK_INPUT_HOOK], { input: JSON.stringify({ hook_event_name: 'Stop' }), encoding: 'utf8',
      env: { ...process.env, PERSONAL_AGENT_SOCKET: socketPath, PERSONAL_AGENT_AUTHORITY: 'worker-token' } });
    assert.equal(child.status, 0);
    const output = JSON.parse(child.stdout);
    assert.equal(output.decision, 'block');
    assert.match(output.reason, /只看上周/);
    const [call] = fs.readFileSync(path.join(dir, 'calls'), 'utf8').trim().split('\n').map((line) => JSON.parse(line));
    assert.deepEqual([call.tool, call.authority], ['task_check', 'worker-token']);
  });
});
