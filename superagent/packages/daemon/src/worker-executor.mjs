// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn as nodeSpawn, spawnDetached } from './platform/index.mjs';
import { terminateProcessTree } from './process-utils.mjs';
import { agentToolEnv, agentToolServer } from './actions/api.mjs';
import { assembleWorkerPolicy } from './conversation-policy.mjs';
import { sanitizePiEnv } from './pi.mjs';
import { sanitizeClaudeEnv } from './claude.mjs';
import { sanitizeCodexEnv } from './codex.mjs';
import { sanitizeTraexEnv } from './traex.mjs';
import { PI_MCP_EXTENSION, PI_SHELL_MAX_S, piCommand, piShellTimeoutEnv } from './pi-runtime.mjs';
import { claudeIsolationSettings, codexConfigFile, codexIsolationArgs, piIsolationArgs, traexConfigFile } from './ov-isolation.mjs';
import { safeError } from './logger.mjs';
import { TASK_INPUT_EVENTS, taskInputHookCommand } from './task-input-hook.mjs';

// The hooks that bring new task inputs into a running Claude Code or Codex
// worker (task-input-hook.mjs), in the format both CLIs share.
function taskInputHooks() {
  const command = taskInputHookCommand();
  return Object.fromEntries(TASK_INPUT_EVENTS.map((event) => [event, [{ hooks: [{ type: 'command', command }] }]]));
}

// The same hooks as Codex -c overrides, for this run only. Codex runs a hook
// that is not in its persisted trust store only with --dangerously-bypass-hook-trust.
function codexTaskInputHookArgs() {
  const command = taskInputHookCommand();
  return ['--dangerously-bypass-hook-trust',
    ...TASK_INPUT_EVENTS.flatMap((event) => ['-c', `hooks.${event}=[{hooks=[{type="command",command=${JSON.stringify(command)}}]}]`])];
}

// One worker turn is one run of the router's own runtime CLI in its
// non-interactive mode, resuming the task's own session: `pi --mode json -p`,
// `codex exec --json [resume]`, `claude --print --resume`. Nothing runs
// between turns, so an idle task holds no process, and a crash or restart
// loses at most the turn in flight. The worker reaches the Core through the
// same MCP adapter as the router, with an authority that allows only its task
// tools and reads (ActionService.workerTools).

export const WORKER_RUNTIMES = Object.freeze(['pi', 'codex', 'traex', 'claude']);
const MAX_OUTPUT_BYTES = 20_000_000;
const TRANSIENT = /\b(429|5\d\d)\b|rate.?limit|overloaded|ECONNRESET|ETIMEDOUT|ECONNREFUSED|socket hang up|timed? ?out|temporarily/i;
// What Claude Code and Codex say when the session a turn resumes is gone
// (`claude --resume`, `codex exec resume`): the task starts a new session
// from its record instead of failing.
const SESSION_LOST = /No conversation found with session ID|no rollout found for thread id/i;

function tail(text, chars = 1_500) {
  const value = String(text ?? '').trim();
  return value.length > chars ? value.slice(-chars) : value;
}

// Read a CLI's JSON-lines stdout as it arrives.
function jsonLines(stream, onLine) {
  let buffer = '';
  let bytes = 0;
  stream.setEncoding('utf8');
  stream.on('data', (chunk) => {
    bytes += Buffer.byteLength(chunk);
    if (bytes > MAX_OUTPUT_BYTES) return;
    buffer += chunk;
    let index;
    while ((index = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (!line.startsWith('{')) continue;
      try { onLine(JSON.parse(line)); } catch { /* not ours */ }
    }
  });
}

// A task that follows another starts from a copy of that task's own session
// (its whole conversation, and so the provider's prefix cache) unless the
// session is this large or gone; then the brief's summary of it is the start.
// Pi copies the file (--fork), Claude Code forks a session it keeps
// (--resume --fork-session), Codex and TraeX a thread they keep (exec fork).
// Claude Code records the new directory as an update after the copied
// history; for Pi, whose providers may fold that update into the first system
// message, the worker's system prompt names one directory for every task
// (pi-mcp-extension.mjs). TraeX keeps the copy cached. Codex keys its prompt
// cache by thread, so a fork there starts with the copied history uncached;
// resuming a task's own thread (a later turn or round) does not.
export const FORK_MAX_BYTES = 4 * 1024 * 1024;

// A Pi worker session's file in its task's session directory, if it is there.
function piSessionFile(config, taskId, sessionId) {
  let names;
  try { names = fs.readdirSync(path.join(config.dataDir, 'workers', taskId)); } catch { return null; }
  const name = names.find((entry) => entry.endsWith(`_${sessionId}.jsonl`));
  return name ? path.join(config.dataDir, 'workers', taskId, name) : null;
}

// Claude Code keeps a session as projects/<its directory, encoded>/<id>.jsonl.
function claudeSessionFile(env, sessionId) {
  const root = path.join(env.CLAUDE_CONFIG_DIR || path.join(env.HOME || os.homedir(), '.claude'), 'projects');
  let dirs;
  try { dirs = fs.readdirSync(root); } catch { return null; }
  for (const dir of dirs) {
    const file = path.join(root, dir, `${sessionId}.jsonl`);
    if (fs.existsSync(file)) return file;
  }
  return null;
}

// Codex keeps a thread as sessions/YYYY/MM/DD/rollout-<time>-<thread id>.jsonl
// in its home; TraeX the same way, under its home's cli/.
function codexSessionFile(env, threadId) {
  return rolloutFile(path.join(env.CODEX_HOME || path.join(env.HOME || os.homedir(), '.codex'), 'sessions'), threadId);
}

function traexSessionFile(env, threadId) {
  return rolloutFile(path.join(env.TRAE_HOME || path.join(env.HOME || os.homedir(), '.trae'), 'cli', 'sessions'), threadId);
}

function rolloutFile(root, threadId) {
  const suffix = `-${threadId}.jsonl`;
  const walk = (dir, depth) => {
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return null; }
    for (const entry of entries) {
      const file = path.join(dir, entry.name);
      if (depth === 0 && entry.isFile() && entry.name.endsWith(suffix)) return file;
      if (depth > 0 && entry.isDirectory()) {
        const found = walk(file, depth - 1);
        if (found) return found;
      }
    }
    return null;
  };
  return walk(root, 3);
}

// Where a runtime keeps one of a task's sessions, if it is still there.
function sessionFile(config, env, runtime, taskId, sessionId) {
  if (runtime === 'claude') return claudeSessionFile(env, sessionId);
  if (runtime === 'codex') return codexSessionFile(env, sessionId);
  if (runtime === 'traex') return traexSessionFile(env, sessionId);
  return piSessionFile(config, taskId, sessionId);
}

// The session file of the task a new one follows, when it can be forked.
export function forkableSession(config, fork, env = process.env) {
  if (!fork?.taskId || !fork.sessionId) return null;
  const file = sessionFile(config, env, fork.runtime, fork.taskId, fork.sessionId);
  if (!file) return null;
  try { return fs.statSync(file).size <= FORK_MAX_BYTES ? file : null; } catch { return null; }
}

// A turn of Codex or TraeX: `exec --json`, then `exec resume <thread>`; the
// first turn of a task that follows one on the same runtime forks its thread.
// New task inputs reach the run through -c hook overrides (task-input-hook.mjs).
function appServerExec({ runtime, bin, configFile, sanitizeEnv, shellTimeoutS = 0 }) {
  return {
    async command({ config, task, prompt, authority, model, effort, policy, fork }) {
      const forked = !task.native_session && fork?.runtime === runtime && forkableSession(config, fork) ? fork.sessionId : null;
      const tools = agentToolServer(config, authority);
      const args = [
        'exec', '--json', '--skip-git-repo-check', '--dangerously-bypass-approvals-and-sandbox', '-C', task.workdir,
        ...(shellTimeoutS ? ['--shell-tool-timeout', `${shellTimeoutS}s`] : []),
        ...codexTaskInputHookArgs(),
        '-c', `developer_instructions=${JSON.stringify(policy)}`,
        '-c', 'features.plugins=false', '-c', 'features.tool_suggest=false', '-c', 'features.apps=false',
        ...codexIsolationArgs(configFile(process.env)),
        '-c', `mcp_servers.personal_agent.command=${JSON.stringify(tools.command)}`,
        '-c', `mcp_servers.personal_agent.args=${JSON.stringify(tools.args)}`,
        '-c', 'mcp_servers.personal_agent.startup_timeout_sec=10',
        '-c', `mcp_servers.personal_agent.tool_timeout_sec=${Math.max(10, Math.ceil(config.sendTimeoutMs / 1_000) + 5)}`,
        ...(model ? ['-m', model] : []),
        ...(effort ? ['-c', `model_reasoning_effort=${JSON.stringify(effort)}`] : []),
        // The thread id is the CLI's own; the first turn reports it.
        ...(task.native_session ? ['resume', task.native_session] : forked ? ['fork', forked] : []),
        prompt,
      ];
      return { command: config[bin], args, env: sanitizeEnv(process.env, config.profile, agentToolEnv(config, authority), config) };
    },
    reader() {
      const state = { text: '', usage: { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 }, error: null, nativeSession: null };
      return {
        state,
        line(event) {
          if (event.type === 'thread.started' && event.thread_id) state.nativeSession = String(event.thread_id);
          if (event.type === 'item.completed' && event.item?.type === 'agent_message' && event.item.text) state.text = String(event.item.text).trim();
          if (event.type === 'turn.completed' && event.usage) {
            state.usage.inputTokens += Number(event.usage.input_tokens ?? 0);
            state.usage.cachedInputTokens += Number(event.usage.cached_input_tokens ?? 0);
            state.usage.outputTokens += Number(event.usage.output_tokens ?? 0);
          }
          if (event.type === 'turn.failed' || event.type === 'error') state.error = String(event.error?.message ?? event.message ?? `${runtime} turn failed`);
        },
      };
    },
  };
}

// Each runtime: how to build one turn's command, and how to read its result.
const RUNTIMES = {
  pi: {
    async command({ config, task, prompt, authority, model, effort, policyFile, fork }) {
      const env = sanitizePiEnv(process.env, config.profile, agentToolEnv(config, authority), config);
      env.PERSONAL_AGENT_MCP_CONFIG = JSON.stringify({
        ...agentToolServer(config, authority),
        timeout: Math.max(10, Math.ceil(config.sendTimeoutMs / 1_000) + 5),
      });
      // New task inputs reach the running worker (pi-mcp-extension.mjs).
      env.PERSONAL_AGENT_TASK_INPUTS = '1';
      // A command that never ends must not hold the task (pi-runtime.mjs);
      // a worker is not capped like its router.
      Object.assign(env, piShellTimeoutEnv());
      const isolation = await piIsolationArgs(env, task.workdir);
      const args = [
        '--mode', 'json', '-p',
        '--append-system-prompt', policyFile,
        ...(effort ? ['--thinking', effort] : []),
        // The first turn of a task that follows a Pi task continues a copy of
        // that task's session under this task's own id.
        ...(fork?.runtime === 'pi' && !task.native_session && forkableSession(config, fork) ? ['--fork', forkableSession(config, fork)] : []),
        '--session-id', task.session_id,
        '--session-dir', path.join(config.dataDir, 'workers', task.id),
        '--approve',
        ...isolation,
        '--extension', PI_MCP_EXTENSION,
        ...(model ? ['--model', model] : []),
        prompt,
      ];
      const command = piCommand(config.piBin, args);
      return { ...command, env };
    },
    reader() {
      const state = { text: '', usage: { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 }, error: null };
      return {
        state,
        line(event) {
          const message = event.type === 'message_end' ? event.message : null;
          if (message?.role !== 'assistant') return;
          const text = (message.content ?? []).filter((part) => part.type === 'text').map((part) => part.text).join('').trim();
          if (text) state.text = text;
          // Pi retries a failed model call itself (auto_retry_start), so an
          // error is the turn's only if no assistant message got past it.
          // JSON print mode also exits zero on aborted or truncated responses;
          // neither is a successful recovery from the earlier model error.
          state.error = ['error', 'aborted', 'length'].includes(message.stopReason)
            ? String(message.errorMessage || `pi turn ${message.stopReason}`) : null;
          state.usage.inputTokens += Number(message.usage?.input ?? 0);
          state.usage.cachedInputTokens += Number(message.usage?.cacheRead ?? 0);
          state.usage.outputTokens += Number(message.usage?.output ?? 0);
        },
      };
    },
  },
  codex: appServerExec({ runtime: 'codex', bin: 'codexBin', configFile: codexConfigFile, sanitizeEnv: sanitizeCodexEnv }),
  // TraeX's exec is Codex's: the same flags, events, hooks and thread files,
  // its own binary, config and home. Unlike Codex it waits for a command to
  // end, so one that never ends would hold the task: each is stopped after the
  // longest a Pi worker's may run (its policy says so).
  traex: appServerExec({ runtime: 'traex', bin: 'traexBin', configFile: traexConfigFile, sanitizeEnv: sanitizeTraexEnv, shellTimeoutS: PI_SHELL_MAX_S }),
  claude: {
    async command({ config, task, prompt, authority, model, effort, policyFile, scratch, fork }) {
      // The first turn of a task that follows a Claude Code task forks that
      // task's session under this task's own id.
      const forked = !task.native_session && fork?.runtime === 'claude' && forkableSession(config, fork) ? fork.sessionId : null;
      const mcpConfig = path.join(scratch, 'mcp-config.json');
      fs.writeFileSync(mcpConfig, JSON.stringify({ mcpServers: { personal_agent: agentToolServer(config, authority) } }), { mode: 0o600 });
      const args = [
        '--print', '--dangerously-skip-permissions', '--permission-prompts', 'none', '--verbose', '--output-format', 'stream-json',
        '--mcp-config', mcpConfig,
        '--settings', JSON.stringify({ ...claudeIsolationSettings({ env: process.env, workspace: task.workdir }), hooks: taskInputHooks() }),
        '--disallowed-tools', 'EnterPlanMode,ExitPlanMode',
        '--append-system-prompt-file', policyFile,
        '--no-chrome',
        ...(effort ? ['--effort', effort] : []),
        ...(model ? ['--model', model] : []),
        ...(task.native_session ? ['--resume', task.native_session]
          : forked ? ['--resume', forked, '--fork-session', '--session-id', task.session_id] : ['--session-id', task.session_id]),
        prompt,
      ];
      return { command: config.claudeBin, args, env: sanitizeClaudeEnv(process.env, config.profile, agentToolEnv(config, authority), config) };
    },
    reader() {
      const state = { text: '', usage: { inputTokens: 0, cachedInputTokens: 0, outputTokens: 0 }, error: null };
      return {
        state,
        line(event) {
          if (event.type !== 'result') return;
          if (typeof event.result === 'string') state.text = event.result.trim();
          if (event.is_error) state.error = String(event.result || event.subtype || 'claude turn failed');
          state.usage.inputTokens += Number(event.usage?.input_tokens ?? 0);
          state.usage.cachedInputTokens += Number(event.usage?.cache_read_input_tokens ?? 0);
          state.usage.outputTokens += Number(event.usage?.output_tokens ?? 0);
        },
      };
    },
  },
};

export class WorkerExecutor {
  // settings(): the router's { runtime, model, effort }, which a task without
  // a model of its own follows on the router's runtime (tasks from before
  // workers had settings of their own).
  constructor({ config, logger = null, settings = () => ({}), spawn = nodeSpawn }) {
    Object.assign(this, { config, logger, settings, spawn });
  }

  supports(runtime) {
    return WORKER_RUNTIMES.includes(runtime);
  }

  // The file a runtime keeps one of a task's sessions in, if it is still
  // there: what a worker that takes the task over on another runtime reads.
  transcriptFile(runtime, taskId, sessionId) {
    if (!sessionId || !WORKER_RUNTIMES.includes(runtime)) return null;
    const file = sessionFile(this.config, process.env, runtime, taskId, sessionId);
    // The file itself, not a link to it: the path is handed to another worker.
    try { return file ? fs.realpathSync(file) : null; } catch { return null; }
  }

  // Whether a task's last session can go on into a new round: 'kept',
  // 'deleted' (no longer on disk) or 'large'. Pi's sessions are files the
  // daemon keeps (and the sweep deletes); Claude Code and Codex keep their own,
  // and clean them up themselves (Claude Code after 30 days by default).
  sessionState(task, { maxBytes = FORK_MAX_BYTES } = {}) {
    if (!task.native_session || !WORKER_RUNTIMES.includes(task.runtime)) return 'kept';
    const file = sessionFile(this.config, process.env, task.runtime, task.id, task.native_session);
    if (!file) return 'deleted';
    try { return fs.statSync(file).size > maxBytes ? 'large' : 'kept'; } catch { return 'deleted'; }
  }

  // The model and effort a task took when it started, reopened or moved to
  // another runtime (worker-settings.mjs; '' is the CLI's own default). A
  // task from before that follows the router's, when it runs on the router's
  // runtime.
  modelFor(task) {
    if (task.model != null || task.effort != null) return { model: task.model || null, effort: task.effort || null };
    const { runtime, model, effort } = this.settings();
    if (runtime !== task.runtime) return { model: null, effort: null };
    return { model: model && model !== runtime ? model : null, effort: effort || null };
  }

  start({ task, prompt, authority, fork = null }) {
    const definition = RUNTIMES[task.runtime];
    if (!definition) throw new Error(`workers do not run on ${task.runtime}; they need ${WORKER_RUNTIMES.join(', ')}`);
    let child = null;
    let stopped = false;
    const done = (async () => {
      const scratch = path.join(this.config.dataDir, 'workers', task.id);
      fs.mkdirSync(scratch, { recursive: true, mode: 0o700 });
      fs.mkdirSync(task.workdir, { recursive: true });
      const policy = assembleWorkerPolicy(this.config, { runtime: task.runtime });
      const policyFile = path.join(scratch, 'worker-policy.md');
      fs.writeFileSync(policyFile, policy, { mode: 0o600 });
      const { model, effort } = this.modelFor(task);
      const { command, args, env } = await definition.command({
        config: this.config, task, prompt, authority, model, effort, policy, policyFile, scratch, fork,
      });
      if (stopped) return { text: '', error: 'stopped' };
      const reader = definition.reader();
      let stderr = '';
      return await new Promise((resolve) => {
        child = this.spawn(command, args, {
          cwd: task.workdir, env, shell: false, detached: spawnDetached, stdio: ['ignore', 'pipe', 'pipe'],
        });
        jsonLines(child.stdout, (event) => reader.line(event));
        child.stderr.setEncoding('utf8');
        child.stderr.on('data', (chunk) => { stderr = tail(stderr + chunk, 4_000); });
        child.once('error', (error) => resolve({ ...reader.state, error: safeError(error), transient: false }));
        // exit can precede the last stdout/stderr data. Wait for both pipes
        // to close so the final result or model error is included.
        child.once('close', (code, signal) => {
          const failed = stopped ? 'stopped' : reader.state.error ?? (code === 0 ? null : `exit ${code ?? signal}: ${tail(stderr, 600)}`);
          const sessionLost = Boolean(failed && !stopped && task.native_session && SESSION_LOST.test(`${failed}\n${stderr}`));
          resolve({ ...reader.state, error: failed, transient: Boolean(failed && !stopped && TRANSIENT.test(failed)), ...(sessionLost ? { sessionLost } : {}) });
        });
      });
    })();
    return {
      done,
      stop: () => {
        stopped = true;
        if (child && child.exitCode === null) terminateProcessTree(child, 'SIGTERM');
      },
    };
  }
}
