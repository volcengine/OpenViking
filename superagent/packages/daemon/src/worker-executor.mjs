// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { spawn as nodeSpawn, spawnDetached } from './platform/index.mjs';
import { terminateProcessTree } from './process-utils.mjs';
import { agentToolEnv, agentToolServer } from './actions/api.mjs';
import { assembleWorkerPolicy } from './conversation-policy.mjs';
import { sanitizePiEnv } from './pi.mjs';
import { sanitizeClaudeEnv } from './claude.mjs';
import { sanitizeCodexEnv } from './codex.mjs';
import { PI_MCP_EXTENSION, piCommand } from './pi-runtime.mjs';
import { claudeIsolationSettings, codexConfigFile, codexIsolationArgs, piIsolationArgs } from './ov-isolation.mjs';
import { safeError } from './logger.mjs';

// One worker turn is one run of the router's own runtime CLI in its
// non-interactive mode, resuming the task's own session: `pi --mode json -p`,
// `codex exec --json [resume]`, `claude --print --resume`. Nothing runs
// between turns, so an idle task holds no process, and a crash or restart
// loses at most the turn in flight. The worker reaches the Core through the
// same MCP adapter as the router, with an authority that allows only its task
// tools and reads (ActionService.workerTools).

export const WORKER_RUNTIMES = Object.freeze(['pi', 'codex', 'claude']);
const MAX_OUTPUT_BYTES = 20_000_000;
const TRANSIENT = /\b(429|5\d\d)\b|rate.?limit|overloaded|ECONNRESET|ETIMEDOUT|ECONNREFUSED|socket hang up|timed? ?out|temporarily/i;

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

// Each runtime: how to build one turn's command, and how to read its result.
const RUNTIMES = {
  pi: {
    async command({ config, task, prompt, authority, model, effort, policyFile }) {
      const env = sanitizePiEnv(process.env, config.profile, agentToolEnv(config, authority), config);
      env.PERSONAL_AGENT_MCP_CONFIG = JSON.stringify({
        ...agentToolServer(config, authority),
        timeout: Math.max(10, Math.ceil(config.sendTimeoutMs / 1_000) + 5),
      });
      const isolation = await piIsolationArgs(env, task.workdir);
      const args = [
        '--mode', 'json', '-p',
        '--append-system-prompt', policyFile,
        ...(effort ? ['--thinking', effort] : []),
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
          if (message.stopReason === 'error' && message.errorMessage) state.error = String(message.errorMessage);
          state.usage.inputTokens += Number(message.usage?.input ?? 0);
          state.usage.cachedInputTokens += Number(message.usage?.cacheRead ?? 0);
          state.usage.outputTokens += Number(message.usage?.output ?? 0);
        },
      };
    },
  },
  codex: {
    async command({ config, task, prompt, authority, model, effort, policy }) {
      const tools = agentToolServer(config, authority);
      const args = [
        'exec', '--json', '--skip-git-repo-check', '--dangerously-bypass-approvals-and-sandbox', '-C', task.workdir,
        '-c', `developer_instructions=${JSON.stringify(policy)}`,
        '-c', 'features.plugins=false', '-c', 'features.tool_suggest=false', '-c', 'features.apps=false',
        ...codexIsolationArgs(codexConfigFile(process.env)),
        '-c', `mcp_servers.personal_agent.command=${JSON.stringify(tools.command)}`,
        '-c', `mcp_servers.personal_agent.args=${JSON.stringify(tools.args)}`,
        '-c', 'mcp_servers.personal_agent.startup_timeout_sec=10',
        '-c', `mcp_servers.personal_agent.tool_timeout_sec=${Math.max(10, Math.ceil(config.sendTimeoutMs / 1_000) + 5)}`,
        ...(model ? ['-m', model] : []),
        ...(effort ? ['-c', `model_reasoning_effort=${JSON.stringify(effort)}`] : []),
        // The thread id is Codex's own; the first turn reports it.
        ...(task.native_session ? ['resume', task.native_session] : []),
        prompt,
      ];
      return { command: config.codexBin, args, env: sanitizeCodexEnv(process.env, config.profile, agentToolEnv(config, authority), config) };
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
          if (event.type === 'turn.failed' || event.type === 'error') state.error = String(event.error?.message ?? event.message ?? 'codex turn failed');
        },
      };
    },
  },
  claude: {
    async command({ config, task, prompt, authority, model, effort, policyFile, scratch }) {
      const mcpConfig = path.join(scratch, 'mcp-config.json');
      fs.writeFileSync(mcpConfig, JSON.stringify({ mcpServers: { personal_agent: agentToolServer(config, authority) } }), { mode: 0o600 });
      const args = [
        '--print', '--dangerously-skip-permissions', '--permission-prompts', 'none', '--verbose', '--output-format', 'stream-json',
        '--mcp-config', mcpConfig,
        '--settings', JSON.stringify(claudeIsolationSettings({ env: process.env, workspace: task.workdir })),
        '--disallowed-tools', 'EnterPlanMode,ExitPlanMode',
        '--append-system-prompt-file', policyFile,
        '--no-chrome',
        ...(effort ? ['--effort', effort] : []),
        ...(model ? ['--model', model] : []),
        ...(task.native_session ? ['--resume', task.native_session] : ['--session-id', task.session_id]),
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
  constructor({ config, logger = null, settings = () => ({}), spawn = nodeSpawn }) {
    Object.assign(this, { config, logger, settings, spawn });
  }

  supports(runtime) {
    return WORKER_RUNTIMES.includes(runtime);
  }

  // The model and effort of the router's runtime, when the task runs on it.
  modelFor(task) {
    const { runtime, model, effort } = this.settings();
    if (runtime !== task.runtime) return { model: null, effort: null };
    return { model: model && model !== runtime ? model : null, effort: effort || null };
  }

  start({ task, prompt, authority }) {
    const definition = RUNTIMES[task.runtime];
    if (!definition) throw new Error(`workers do not run on ${task.runtime}; they need ${WORKER_RUNTIMES.join(', ')}`);
    let child = null;
    let stopped = false;
    const done = (async () => {
      const scratch = path.join(this.config.dataDir, 'workers', task.id);
      fs.mkdirSync(scratch, { recursive: true, mode: 0o700 });
      fs.mkdirSync(task.workdir, { recursive: true });
      const policy = assembleWorkerPolicy(this.config);
      const policyFile = path.join(scratch, 'worker-policy.md');
      fs.writeFileSync(policyFile, policy, { mode: 0o600 });
      const { model, effort } = this.modelFor(task);
      const { command, args, env } = await definition.command({
        config: this.config, task, prompt, authority, model, effort, policy, policyFile, scratch,
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
        child.once('exit', (code, signal) => {
          const failed = stopped ? 'stopped' : reader.state.error ?? (code === 0 ? null : `exit ${code ?? signal}: ${tail(stderr, 600)}`);
          resolve({ ...reader.state, error: failed, transient: Boolean(failed && !stopped && TRANSIENT.test(failed)) });
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
