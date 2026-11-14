// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { WorkerExecutor } from '../src/worker-executor.mjs';
import { PI_MCP_EXTENSION } from '../src/pi-runtime.mjs';

// Execute a real Node fixture on every platform. Windows cannot spawn a
// shebang-only file, so its entry point is a small cmd launcher instead.
function nodeFixture(dir, name, source) {
  const script = path.join(dir, `${name}.cjs`);
  fs.writeFileSync(script, `#!/usr/bin/env node\n${source}`, { mode: 0o755 });
  if (process.platform !== 'win32') return script;
  const launcher = path.join(dir, `${name}.cmd`);
  // The product resolves only the Node invocation of an npm-style shim, so the
  // fixture keeps npm's variable layout instead of a bare `%~dp0...` call.
  fs.writeFileSync(launcher, [
    '@echo off',
    'set dp0=%~dp0',
    `set _prog=${process.execPath}`,
    `"%_prog%" "%dp0%\\${name}.cjs" %*`,
    '',
  ].join('\r\n'));
  return launcher;
}

// A CLI stand-in: records its argv, cwd and the agent env, then prints the
// JSON lines the test gives it and exits with the given code.
function fakeCli(dir, name, lines, code = 0) {
  return nodeFixture(dir, name, `
const fs = require('node:fs');
fs.appendFileSync(${JSON.stringify(path.join(dir, `${name}.calls`))}, JSON.stringify({ argv: process.argv.slice(2), cwd: process.cwd(),
  mcp: process.env.PERSONAL_AGENT_MCP_CONFIG ?? null, authority: process.env.PERSONAL_AGENT_AUTHORITY ?? null,
  shellLimit: process.env.PERSONAL_AGENT_SHELL_LIMIT_S ?? null }) + '\\n');
for (const line of ${JSON.stringify(lines)}) process.stdout.write(JSON.stringify(line) + '\\n');
${code ? `process.stderr.write('upstream said HTTP 429'); process.exit(${code});` : ''}
`);
}

const calls = (dir, name) => fs.readFileSync(path.join(dir, `${name}.calls`), 'utf8').trim().split('\n').map((line) => JSON.parse(line));

function setup(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'worker-exec-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const previous = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = path.join(dir, 'pi-agent');
  t.after(() => { if (previous === undefined) delete process.env.PI_CODING_AGENT_DIR; else process.env.PI_CODING_AGENT_DIR = previous; });
  const config = {
    dataDir: path.join(dir, 'data'), workspace: path.join(dir, 'workspace'), sendTimeoutMs: 30_000,
    actionSocketPath: path.join(dir, 'data', 'actions.sock'), actionMcpPath: '/core/actions/mcp.mjs',
    agentName: 'Vika', profile: 'test-profile',
  };
  const task = (fields = {}) => ({ id: 't1', runtime: 'pi', session_id: '6d0f2a51-0000-4000-8000-000000000001', native_session: null,
    workdir: path.join(dir, 'workspace', 'tasks', 't1'), turns: 1, ...fields });
  return { dir, config, task };
}

describe('WorkerExecutor', () => {
  it('runs a Pi worker turn in json print mode in its own session, with the worker authority and policy', async (t) => {
    const { dir, config, task } = setup(t);
    config.piBin = fakeCli(dir, 'pi', [
      { type: 'message_end', message: { role: 'assistant', content: [{ type: 'text', text: 'looking' }], usage: { input: 100, output: 5, cacheRead: 50 } } },
      { type: 'message_end', message: { role: 'assistant', content: [{ type: 'text', text: '原因是 A' }], usage: { input: 200, output: 20, cacheRead: 150 } } },
      { type: 'agent_settled' },
    ]);
    const executor = new WorkerExecutor({ config, settings: () => ({ runtime: 'pi', model: 'ark/deepseek', effort: 'medium' }) });
    const result = await executor.start({ task: task(), prompt: '[task t1] 查一下', authority: 'worker-token' }).done;
    assert.deepEqual(result, { text: '原因是 A', usage: { inputTokens: 300, cachedInputTokens: 200, outputTokens: 25 }, error: null, transient: false });
    const [call] = calls(dir, 'pi');
    assert.deepEqual(call.argv.slice(0, 3), ['--mode', 'json', '-p']);
    assert.equal(call.argv.at(-1), '[task t1] 查一下');
    const arg = (flag) => call.argv[call.argv.indexOf(flag) + 1];
    assert.equal(arg('--session-id'), '6d0f2a51-0000-4000-8000-000000000001');
    assert.equal(arg('--session-dir'), path.join(config.dataDir, 'workers', 't1'));
    assert.equal(arg('--model'), 'ark/deepseek');
    assert.equal(arg('--thinking'), 'medium');
    assert.ok(call.argv.includes(PI_MCP_EXTENSION));
    assert.match(fs.readFileSync(arg('--append-system-prompt'), 'utf8'), /^## Worker\n\nYou are a worker for Vika\./);
    assert.equal(call.cwd, task().workdir);
    assert.deepEqual(JSON.parse(call.mcp).args.slice(-2), ['--authority', 'worker-token']);
    assert.equal(call.shellLimit, null, 'a worker shell is not capped like the router\'s');
  });

  it('reports a failed turn, and whether it is worth another try', async (t) => {
    const { dir, config, task } = setup(t);
    config.piBin = fakeCli(dir, 'pi', [], 1);
    const result = await new WorkerExecutor({ config }).start({ task: task(), prompt: 'p', authority: 'x' }).done;
    assert.match(result.error, /^exit 1: upstream said HTTP 429/);
    assert.equal(result.transient, true);
  });

  it('runs Codex with exec --json, resuming the thread it named on the first turn', async (t) => {
    const { dir, config, task } = setup(t);
    config.codexBin = fakeCli(dir, 'codex', [
      { type: 'thread.started', thread_id: 'th_123' },
      { type: 'item.completed', item: { type: 'agent_message', text: 'done it' } },
      { type: 'turn.completed', usage: { input_tokens: 10, cached_input_tokens: 4, output_tokens: 2 } },
    ]);
    const executor = new WorkerExecutor({ config, settings: () => ({ runtime: 'codex', model: 'gpt-6-astra', effort: 'high' }) });
    const first = await executor.start({ task: task({ runtime: 'codex' }), prompt: 'first', authority: 'tok' }).done;
    assert.equal(first.nativeSession, 'th_123');
    assert.equal(first.text, 'done it');
    await executor.start({ task: task({ runtime: 'codex', native_session: 'th_123', turns: 2 }), prompt: 'second', authority: 'tok' }).done;
    const [one, two] = calls(dir, 'codex');
    assert.deepEqual(one.argv.slice(0, 3), ['exec', '--json', '--skip-git-repo-check']);
    assert.ok(!one.argv.includes('resume'));
    assert.match(one.argv.join(' '), /developer_instructions="## Worker/);
    assert.match(one.argv.join(' '), /mcp_servers\.personal_agent\.args=\[.*"--authority","tok"\]/);
    assert.deepEqual(two.argv.slice(-3), ['resume', 'th_123', 'second']);
    assert.ok(two.argv.includes('gpt-6-astra'));
  });

  it('runs Claude Code with --print, resuming its session after the first turn', async (t) => {
    const { dir, config, task } = setup(t);
    config.claudeBin = fakeCli(dir, 'claude', [
      { type: 'assistant', message: { content: [{ type: 'text', text: 'thinking aloud' }] } },
      { type: 'result', result: '结果在这里', is_error: false, usage: { input_tokens: 7, cache_read_input_tokens: 3, output_tokens: 1 } },
    ]);
    const executor = new WorkerExecutor({ config, settings: () => ({ runtime: 'claude', model: 'sonnet', effort: 'medium' }) });
    const first = await executor.start({ task: task({ runtime: 'claude' }), prompt: 'one', authority: 'tok' }).done;
    assert.equal(first.text, '结果在这里');
    await executor.start({ task: task({ runtime: 'claude', native_session: task().session_id, turns: 2 }), prompt: 'two', authority: 'tok' }).done;
    const [one, two] = calls(dir, 'claude');
    const arg = (call, flag) => call.argv[call.argv.indexOf(flag) + 1];
    assert.equal(arg(one, '--session-id'), task().session_id);
    assert.equal(arg(two, '--resume'), task().session_id);
    const mcp = JSON.parse(fs.readFileSync(arg(one, '--mcp-config'), 'utf8'));
    assert.deepEqual(mcp.mcpServers.personal_agent.args.slice(-2), ['--authority', 'tok']);
  });

  it('starts a fresh Claude session after an attempt without a successful session checkpoint', async (t) => {
    const { dir, config, task } = setup(t);
    config.claudeBin = fakeCli(dir, 'claude', [{ type: 'result', result: 'ok', is_error: false }]);
    await new WorkerExecutor({ config }).start({ task: task({ runtime: 'claude', turns: 2 }), prompt: 'replayed original', authority: 'tok' }).done;
    const [call] = calls(dir, 'claude');
    assert.ok(!call.argv.includes('--resume'));
    assert.equal(call.argv[call.argv.indexOf('--session-id') + 1], task().session_id);
    assert.equal(call.argv.at(-1), 'replayed original');
  });

  it('runs a DSH worker in the router profile with a stable task/turn request id', async (t) => {
    const { config, task } = setup(t);
    const requests = [];
    const dshRequest = async (method, input) => {
      requests.push({ method, input });
      if (method === 'superagent/worker/ensure') return { sessionId: input.sessionId, preset: 'ov-superagent-worker' };
      if (method === 'superagent/worker/submit') return { messageId: 'm1', reused: false };
      if (method === 'superagent/worker/wait') {
        return { text: 'DSH 完成', usage: { inputTokens: 12, cachedInputTokens: 4, outputTokens: 2 }, nativeSession: input.sessionId, error: null, transient: false };
      }
      throw new Error(method);
    };
    const executor = new WorkerExecutor({ config, settings: () => ({
      runtime: 'dsh', model: 'deepseek/deepseek-chat', effort: 'high', dshRequest,
    }) });
    const result = await executor.start({ task: task({ runtime: 'dsh' }), prompt: 'work', authority: 'worker-token' }).done;
    assert.equal(result.text, 'DSH 完成');
    assert.equal(result.nativeSession, task().session_id);
    assert.deepEqual(requests.map(request => request.method), [
      'superagent/worker/ensure', 'superagent/worker/submit', 'superagent/worker/wait',
    ]);
    assert.equal(requests[0].input.authority, 'worker-token');
    assert.equal(requests[0].input.provider, 'deepseek');
    assert.equal(requests[0].input.model, 'deepseek-chat');
    assert.equal(requests[0].input.effort, 'high');
    assert.equal(requests[1].input.requestId, 't1:1');
    assert.match(requests[0].input.policy, /^## Worker/);
  });

  it('runs only on the runtimes it knows, and a stopped turn ends as stopped', async (t) => {
    const { dir, config, task } = setup(t);
    const executor = new WorkerExecutor({ config });
    assert.deepEqual(['pi', 'codex', 'claude', 'dsh', 'durable', 'opencode', 'volc'].map((id) => executor.supports(id)),
      [true, true, true, true, false, false, false]);
    assert.throws(() => executor.start({ task: task({ runtime: 'durable' }), prompt: 'p', authority: 'x' }), /workers do not run on durable/);
    config.piBin = nodeFixture(dir, 'slow-pi', 'setTimeout(() => {}, 60_000);');
    const handle = executor.start({ task: task(), prompt: 'p', authority: 'x' });
    setTimeout(() => handle.stop(), 300);
    assert.equal((await handle.done).error, 'stopped');
  });
});
