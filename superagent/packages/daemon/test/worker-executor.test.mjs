// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { FORK_MAX_BYTES, WorkerExecutor } from '../src/worker-executor.mjs';
import { PI_MCP_EXTENSION } from '../src/pi-runtime.mjs';

// Execute a real Node fixture on every platform. Windows cannot spawn a
// shebang-only file, so its entry point is a small cmd launcher instead.
function nodeFixture(dir, name, source) {
  const script = path.join(dir, `${name}.cjs`);
  fs.writeFileSync(script, `#!/usr/bin/env node\n${source}`, { mode: 0o755 });
  if (process.platform !== 'win32') return script;
  const launcher = path.join(dir, `${name}.cmd`);
  fs.writeFileSync(launcher, `@echo off\r\n"${process.execPath}" "%~dp0${name}.cjs" %*\r\n`);
  return launcher;
}

// A CLI stand-in: records its argv, cwd and the agent env, then prints the
// JSON lines the test gives it and exits with the given code.
function fakeCli(dir, name, lines, code = 0) {
  return nodeFixture(dir, name, `
const fs = require('node:fs');
fs.appendFileSync(${JSON.stringify(path.join(dir, `${name}.calls`))}, JSON.stringify({ argv: process.argv.slice(2), cwd: process.cwd(),
  mcp: process.env.PERSONAL_AGENT_MCP_CONFIG ?? null, authority: process.env.PERSONAL_AGENT_AUTHORITY ?? null,
  shellLimit: process.env.PERSONAL_AGENT_SHELL_LIMIT_S ?? null, shellDefault: process.env.PERSONAL_AGENT_SHELL_DEFAULT_S ?? null,
  shellMax: process.env.PERSONAL_AGENT_SHELL_MAX_S ?? null }) + '\\n');
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
  it('reads the remaining CLI output after process exit before completing a turn', async (t) => {
    const { config, task } = setup(t);
    config.piBin = 'fake-pi';
    const spawn = () => {
      const child = new EventEmitter();
      child.stdout = new PassThrough();
      child.stderr = new PassThrough();
      setImmediate(() => {
        child.emit('exit', 0, null);
        // Node may observe exit before the stdout pipe has drained.
        child.stdout.end(JSON.stringify({ type: 'message_end', message: {
          role: 'assistant', content: [], stopReason: 'error', errorMessage: 'HTTP 429',
        } }) + '\n');
        child.stderr.end();
        child.emit('close', 0, null);
      });
      return child;
    };
    const result = await new WorkerExecutor({ config, spawn }).start({ task: task(), prompt: 'p', authority: 'x' }).done;
    assert.equal(result.error, 'HTTP 429');
    assert.equal(result.transient, true);
  });

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
    // but a command that never ends cannot hold the task, and the policy says so.
    assert.deepEqual([call.shellDefault, call.shellMax], ['300', '3600']);
    const policy = fs.readFileSync(arg('--append-system-prompt'), 'utf8');
    assert.match(policy, /\n## Shell\n\n- Search file contents with .*rg.* rather than `grep -r`/);
    assert.match(policy, /A shell command without a `timeout` of its own is stopped after 5 minutes, and none runs longer than 60/);
  });

  it('starts a task that follows a Pi task from a copy of that task\'s session, unless it is gone or too large', async (t) => {
    const { dir, config, task } = setup(t);
    config.piBin = fakeCli(dir, 'pi', [{ type: 'agent_settled' }]);
    const executor = new WorkerExecutor({ config, settings: () => ({ runtime: 'pi', model: null, effort: null }) });
    const parentDir = path.join(config.dataDir, 'workers', 't3');
    fs.mkdirSync(parentDir, { recursive: true });
    const parentFile = path.join(parentDir, '2026-11-16T09-00-00-000Z_aaaaaaaa-0000-4000-8000-000000000003.jsonl');
    fs.writeFileSync(parentFile, '{"type":"session"}\n');
    const fork = { taskId: 't3', runtime: 'pi', sessionId: 'aaaaaaaa-0000-4000-8000-000000000003' };
    await executor.start({ task: task(), prompt: 'p', authority: 'x', fork }).done;
    await executor.start({ task: task({ native_session: '6d0f2a51-0000-4000-8000-000000000001', turns: 2 }), prompt: 'p', authority: 'x', fork }).done;
    await executor.start({ task: task(), prompt: 'p', authority: 'x', fork: { ...fork, sessionId: 'missing' } }).done;
    fs.writeFileSync(parentFile, Buffer.alloc(FORK_MAX_BYTES + 1));
    await executor.start({ task: task(), prompt: 'p', authority: 'x', fork }).done;
    const forks = calls(dir, 'pi').map((call) => (call.argv.includes('--fork') ? call.argv[call.argv.indexOf('--fork') + 1] : null));
    assert.deepEqual(forks, [parentFile, null, null, null], 'first turn only, and only a session that is there and not too large');
    const [first] = calls(dir, 'pi');
    assert.equal(first.argv[first.argv.indexOf('--session-id') + 1], '6d0f2a51-0000-4000-8000-000000000001', 'under the new task\'s own id');
  });

  it('says whether a task\'s last session can go on into a new round, on every runtime: kept, deleted or too large', (t) => {
    const { dir, config, task } = setup(t);
    const saved = { CLAUDE_CONFIG_DIR: process.env.CLAUDE_CONFIG_DIR, CODEX_HOME: process.env.CODEX_HOME, TRAE_HOME: process.env.TRAE_HOME };
    process.env.CLAUDE_CONFIG_DIR = path.join(dir, 'claude-home');
    process.env.CODEX_HOME = path.join(dir, 'codex-home');
    process.env.TRAE_HOME = path.join(dir, 'trae-home');
    t.after(() => { for (const [key, value] of Object.entries(saved)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
    const executor = new WorkerExecutor({ config });
    const id = 'bbbbbbbb-0000-4000-8000-000000000001';
    const files = {
      pi: path.join(config.dataDir, 'workers', 't1', `2026-11-18T10-00-00-000Z_${id}.jsonl`),
      claude: path.join(process.env.CLAUDE_CONFIG_DIR, 'projects', '-srv-workspace-tasks-t1', `${id}.jsonl`),
      codex: path.join(process.env.CODEX_HOME, 'sessions', '2026', '11', '18', `rollout-2026-11-18T10-00-00-${id}.jsonl`),
      traex: path.join(process.env.TRAE_HOME, 'cli', 'sessions', '2026', '11', '18', `rollout-2026-11-18T10-00-00-${id}.jsonl`),
    };
    for (const [runtime, file] of Object.entries(files)) {
      const current = task({ runtime, native_session: id, turns: 1 });
      assert.equal(executor.sessionState(current), 'deleted', `${runtime}: no file`);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, '{"type":"session"}\n');
      assert.equal(executor.sessionState(current), 'kept', runtime);
      assert.equal(executor.sessionState(current, { maxBytes: 4 }), 'large', runtime);
    }
    assert.equal(executor.sessionState(task({ runtime: 'claude', native_session: null })), 'kept', 'no checkpoint: nothing to judge');
  });

  it('says when the session a Claude Code or Codex turn resumed is gone, so the task starts a new one', async (t) => {
    const { dir, config, task } = setup(t);
    const cli = (name, lines, stderr, code) => {
      const file = path.join(dir, name);
      fs.writeFileSync(file, `#!/usr/bin/env node\nfor (const line of ${JSON.stringify(lines)}) process.stdout.write(JSON.stringify(line) + '\\n');\nprocess.stderr.write(${JSON.stringify(stderr)});\nprocess.exit(${code});\n`, { mode: 0o755 });
      return file;
    };
    config.claudeBin = cli('claude', [{ type: 'result', subtype: 'error_during_execution', is_error: true, num_turns: 0 }], 'No conversation found with session ID: x', 0);
    config.codexBin = cli('codex', [], 'Error: thread/resume: thread/resume failed: no rollout found for thread id th_gone (code -32600)', 1);
    config.traexBin = cli('traex', [], 'Error: thread/resume: thread/resume failed: no rollout found for thread id th_gone (code -32600)', 1);
    const executor = new WorkerExecutor({ config });
    const claude = await executor.start({ task: task({ runtime: 'claude', native_session: 'gone', turns: 2 }), prompt: 'p', authority: 'x' }).done;
    const codex = await executor.start({ task: task({ runtime: 'codex', native_session: 'th_gone', turns: 2 }), prompt: 'p', authority: 'x' }).done;
    assert.equal(claude.sessionLost, true);
    assert.equal(codex.sessionLost, true);
    const traex = await executor.start({ task: task({ runtime: 'traex', native_session: 'th_gone', turns: 2 }), prompt: 'p', authority: 'x' }).done;
    assert.equal(traex.sessionLost, true);
    const fresh = await executor.start({ task: task({ runtime: 'codex', native_session: null, turns: 0 }), prompt: 'p', authority: 'x' }).done;
    assert.equal(fresh.sessionLost, undefined, 'only a turn that resumed a session lost it');
  });

  it('starts a task that follows a Claude Code or Codex task from a fork of that task\'s session, unless it is gone or too large', async (t) => {
    const { dir, config, task } = setup(t);
    const saved = { CLAUDE_CONFIG_DIR: process.env.CLAUDE_CONFIG_DIR, CODEX_HOME: process.env.CODEX_HOME };
    process.env.CLAUDE_CONFIG_DIR = path.join(dir, 'claude-home');
    process.env.CODEX_HOME = path.join(dir, 'codex-home');
    t.after(() => { for (const [key, value] of Object.entries(saved)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; } });
    const parent = 'cccccccc-0000-4000-8000-000000000003';
    const claudeFile = path.join(process.env.CLAUDE_CONFIG_DIR, 'projects', '-srv-workspace-tasks-t3', `${parent}.jsonl`);
    const codexFile = path.join(process.env.CODEX_HOME, 'sessions', '2026', '11', '19', `rollout-2026-11-19T03-24-10-${parent}.jsonl`);
    for (const file of [claudeFile, codexFile]) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, '{}\n'); }
    config.claudeBin = fakeCli(dir, 'claude', [{ type: 'result', result: 'ok', is_error: false }]);
    config.codexBin = fakeCli(dir, 'codex', [{ type: 'thread.started', thread_id: 'th_child' }]);
    const executor = new WorkerExecutor({ config, settings: () => ({ runtime: null, model: null, effort: null }) });
    const run = async (runtime, fields = {}, fork = { taskId: 't3', runtime, sessionId: parent }) => {
      await executor.start({ task: task({ runtime, turns: 0, ...fields }), prompt: 'p', authority: 'x', fork }).done;
    };
    await run('claude');
    await run('claude', { native_session: task().session_id, turns: 1 });
    await run('claude', {}, { taskId: 't3', runtime: 'claude', sessionId: 'missing' });
    await run('codex');
    await run('codex', { native_session: 'th_child', turns: 1 });
    await run('codex', {}, { taskId: 't3', runtime: 'codex', sessionId: 'missing' });
    fs.writeFileSync(claudeFile, Buffer.alloc(FORK_MAX_BYTES + 1));
    fs.writeFileSync(codexFile, Buffer.alloc(FORK_MAX_BYTES + 1));
    await run('claude');
    await run('codex');
    const claude = calls(dir, 'claude').map((call) => call.argv.slice(call.argv.indexOf('--no-chrome') + 1, -1));
    assert.deepEqual(claude, [
      ['--resume', parent, '--fork-session', '--session-id', task().session_id],
      ['--resume', task().session_id],
      ['--session-id', task().session_id],
      ['--session-id', task().session_id],
    ], 'a fork under its own id on the first turn only, of a session that is there and not too large');
    const codex = calls(dir, 'codex').map((call) => call.argv.slice(call.argv.lastIndexOf('-c') + 2, -1));
    assert.deepEqual(codex, [['fork', parent], ['resume', 'th_child'], [], []]);
    assert.ok(calls(dir, 'codex').every((call) => call.argv.includes('-C')), 'in its own directory');
  });

  it('reports a failed turn, and whether it is worth another try', async (t) => {
    const { dir, config, task } = setup(t);
    config.piBin = fakeCli(dir, 'pi', [], 1);
    const result = await new WorkerExecutor({ config }).start({ task: task(), prompt: 'p', authority: 'x' }).done;
    assert.match(result.error, /^exit 1: upstream said HTTP 429/);
    assert.equal(result.transient, true);
  });

  it('does not fail a Pi turn on a model error Pi retried past, only on one it ended with', async (t) => {
    const { dir, config, task } = setup(t);
    const limited = { role: 'assistant', content: [], stopReason: 'error', errorMessage: '429: {"code":"ModelAccountTpmRateLimitExceeded"}' };
    const reported = { role: 'assistant', content: [{ type: 'text', text: '已提交报告' }], stopReason: 'stop', usage: { input: 10, output: 2 } };
    // As Pi's json mode prints an automatic retry (agent-session _prepareRetry).
    config.piBin = fakeCli(dir, 'pi', [
      { type: 'message_end', message: limited },
      { type: 'auto_retry_start', attempt: 1, maxAttempts: 3, delayMs: 2000, errorMessage: limited.errorMessage },
      { type: 'message_end', message: reported },
      { type: 'auto_retry_end', success: true, attempt: 1 },
      { type: 'agent_settled' },
    ]);
    const executor = new WorkerExecutor({ config });
    const recovered = await executor.start({ task: task(), prompt: 'p', authority: 'x' }).done;
    assert.equal(recovered.error, null);
    assert.equal(recovered.transient, false);
    assert.equal(recovered.text, '已提交报告');

    config.piBin = fakeCli(dir, 'pi-exhausted', [
      { type: 'message_end', message: reported },
      { type: 'message_end', message: limited },
      { type: 'auto_retry_end', success: false, attempt: 3, finalError: limited.errorMessage },
    ]);
    const exhausted = await executor.start({ task: task(), prompt: 'p', authority: 'x' }).done;
    assert.match(exhausted.error, /^429: /);
    assert.equal(exhausted.transient, true);
  });

  for (const stopReason of ['aborted', 'length']) {
    it(`does not treat a Pi retry ending with ${stopReason} as recovered`, async (t) => {
      const { dir, config, task } = setup(t);
      // Pi's JSON print mode exits zero even for these incomplete responses.
      config.piBin = fakeCli(dir, 'pi', [
        { type: 'message_end', message: { role: 'assistant', content: [], stopReason: 'error', errorMessage: 'HTTP 429' } },
        { type: 'message_end', message: { role: 'assistant', content: [{ type: 'text', text: 'unfinished' }], stopReason } },
      ]);
      const result = await new WorkerExecutor({ config }).start({ task: task(), prompt: 'p', authority: 'x' }).done;
      assert.match(result.error, new RegExp(stopReason));
      assert.equal(result.transient, false, 'the terminal failure replaces the earlier rate limit');
    });
  }

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
    assert.match(one.argv.join(' '), /## Shell\\n\\n- Search file contents with /);
    assert.doesNotMatch(one.argv.join(' '), /stopped after 5 minutes/, 'Pi\'s timeout rule is Pi\'s');
    assert.equal(one.shellDefault, null);
    assert.match(one.argv.join(' '), /mcp_servers\.personal_agent\.args=\[.*"--authority","tok"\]/);
    assert.deepEqual(two.argv.slice(-3), ['resume', 'th_123', 'second']);
    assert.ok(!one.argv.includes('--shell-tool-timeout'), 'Codex returns a long command\'s output and lets it run');
    assert.ok(two.argv.includes('gpt-6-astra'));
    // New task inputs reach the running worker through this run's own hooks.
    assert.ok(one.argv.includes('--dangerously-bypass-hook-trust'));
    for (const event of ['PostToolUse', 'Stop']) {
      assert.ok(one.argv.some((value) => value.startsWith(`hooks.${event}=[{hooks=[{type="command",command=`) && value.includes('task-input-hook.mjs')), event);
    }
  });

  it('runs TraeX as it runs Codex, with its own binary, config and home: exec --json, resume, and a fork for a task that follows', async (t) => {
    const { dir, config, task } = setup(t);
    const saved = process.env.TRAE_HOME;
    process.env.TRAE_HOME = path.join(dir, 'trae-home');
    t.after(() => { if (saved === undefined) delete process.env.TRAE_HOME; else process.env.TRAE_HOME = saved; });
    const parent = '01a1f268-2f50-78b0-b6a2-41ac7404f260';
    const rollout = path.join(process.env.TRAE_HOME, 'cli', 'sessions', '2026', '11', '19', `rollout-2026-11-19T06-04-36-${parent}.jsonl`);
    fs.mkdirSync(path.dirname(rollout), { recursive: true });
    fs.writeFileSync(rollout, '{}\n');
    config.traexBin = fakeCli(dir, 'traex', [
      { type: 'thread.started', thread_id: 'th_traex' },
      { type: 'item.completed', item: { type: 'agent_message', text: '做完了' } },
      { type: 'turn.completed', usage: { input_tokens: 10, cached_input_tokens: 8, output_tokens: 2 } },
    ]);
    const executor = new WorkerExecutor({ config, settings: () => ({ runtime: 'traex', model: 'DeepSeek-V4.1-Flash', effort: 'low' }) });
    assert.ok(executor.supports('traex'));
    const first = await executor.start({ task: task({ runtime: 'traex', turns: 0 }), prompt: 'first', authority: 'tok' }).done;
    assert.deepEqual([first.nativeSession, first.text, first.usage], ['th_traex', '做完了', { inputTokens: 10, cachedInputTokens: 8, outputTokens: 2 }]);
    await executor.start({ task: task({ runtime: 'traex', native_session: 'th_traex', turns: 1 }), prompt: 'second', authority: 'tok' }).done;
    const fork = { taskId: 't3', runtime: 'traex', sessionId: parent };
    await executor.start({ task: task({ runtime: 'traex', turns: 0 }), prompt: 'third', authority: 'tok', fork }).done;
    await executor.start({ task: task({ runtime: 'traex', turns: 0 }), prompt: 'fourth', authority: 'tok', fork: { ...fork, sessionId: 'gone' } }).done;
    const [one, two, three, four] = calls(dir, 'traex');
    assert.deepEqual(one.argv.slice(0, 4), ['exec', '--json', '--skip-git-repo-check', '--dangerously-bypass-approvals-and-sandbox']);
    assert.equal(one.argv[one.argv.indexOf('-C') + 1], task().workdir);
    assert.match(one.argv.join(' '), /developer_instructions="## Worker/);
    assert.match(one.argv.join(' '), /mcp_servers\.personal_agent\.args=\[.*"--authority","tok"\]/);
    assert.ok(one.argv.includes('--dangerously-bypass-hook-trust') && one.argv.some((arg) => /^hooks\.Stop=/.test(arg)), 'new task inputs reach it through hooks');
    assert.equal(one.argv[one.argv.indexOf('--shell-tool-timeout') + 1], '3600s', 'a command that never ends cannot hold the task');
    assert.deepEqual([one.argv[one.argv.indexOf('-m') + 1], one.argv.find((arg) => arg.startsWith('model_reasoning_effort='))], ['DeepSeek-V4.1-Flash', 'model_reasoning_effort="low"']);
    assert.deepEqual(two.argv.slice(-3), ['resume', 'th_traex', 'second']);
    assert.deepEqual(three.argv.slice(-3), ['fork', parent, 'third']);
    assert.ok(!four.argv.includes('fork'), 'no fork of a thread that is gone');
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
    // New task inputs reach the running worker through hooks in its settings.
    const { hooks } = JSON.parse(arg(one, '--settings'));
    assert.deepEqual(Object.keys(hooks), ['PostToolUse', 'Stop']);
    assert.match(hooks.Stop[0].hooks[0].command, /task-input-hook\.mjs"$/);
    assert.equal(hooks.Stop[0].hooks[0].type, 'command');
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
    assert.deepEqual(['pi', 'codex', 'traex', 'claude', 'dsh', 'durable', 'opencode', 'volc'].map((id) => executor.supports(id)),
      [true, true, true, true, true, false, false, false]);
    assert.throws(() => executor.start({ task: task({ runtime: 'durable' }), prompt: 'p', authority: 'x' }), /workers do not run on durable/);
    config.piBin = nodeFixture(dir, 'slow-pi', 'setTimeout(() => {}, 60_000);');
    const handle = executor.start({ task: task(), prompt: 'p', authority: 'x' });
    setTimeout(() => handle.stop(), 300);
    assert.equal((await handle.done).error, 'stopped');
  });
});
