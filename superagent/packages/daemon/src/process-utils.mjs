// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawnDetached as defaultDetached } from './platform/index.mjs';

import { spawn, normalizeEnvironment } from './platform/index.mjs';
import path from 'node:path';
import { agentCliCommands } from './agent-clis.mjs';
import { applyOpenVikingChildEnv } from './openviking.mjs';

export function sanitizeNonArkChildEnv(environment) {
  const safeEnv = { ...environment };
  for (const key of Object.keys(safeEnv)) {
    if (/^(?:ARK|VOLCENGINE)_/i.test(key)
      || /^(?:OV_SUPERAGENT)_VOLC_/i.test(key)
      || key === 'SESSION_ID') delete safeEnv[key];
  }
  return safeEnv;
}

// Agent runtimes reach the Core's tools through the local action socket; the
// authority token fences a retired runtime. lark-cli keeps the bot profile for
// non-message Lark operations.
export function sanitizeAgentChildEnv(environment, {
  profile = '',
  stripClaudeCode = false,
  actions = null,
  cliBins = agentCliCommands().map(({ bin }) => bin),
} = {}) {
  const safeEnv = {
    ...sanitizeNonArkChildEnv(normalizeEnvironment(environment)),
    FORCE_COLOR: '0',
    NO_COLOR: '1',
  };
  for (const key of Object.keys(safeEnv)) {
    if (/^(?:LARK|FEISHU|LARKSUITE|OV_SUPERAGENT)_/i.test(key)
      || (stripClaudeCode && key === 'CLAUDECODE')) delete safeEnv[key];
  }
  if (profile) safeEnv.LARKSUITE_CLI_PROFILE = profile;
  if (actions) {
    safeEnv.PERSONAL_AGENT_SOCKET = actions.socketPath;
    safeEnv.PERSONAL_AGENT_AUTHORITY = actions.authority ?? '';
  }
  // Runtimes launch in the user's workspace, outside the release/checkout.
  // Prefer configured binaries to global installs without exposing credentials.
  const directories = [...new Set(cliBins.filter(bin => path.isAbsolute(bin)).map(bin => path.dirname(bin)))];
  // A PATH entry cannot contain its delimiter; the prompt still provides the
  // exact shell-quoted command for these paths and for renamed overrides.
  const prefixes = directories.filter(directory => !directory.includes(path.delimiter));
  if (prefixes.length) {
    const inherited = (safeEnv.PATH || '/usr/bin:/bin').split(path.delimiter);
    safeEnv.PATH = [...prefixes, ...inherited.filter(directory => !prefixes.includes(directory))].join(path.delimiter);
  }
  return applyOpenVikingChildEnv(safeEnv, environment);
}

function appendTail(current, chunk, limit) {
  const next = current + chunk;
  return next.length <= limit ? next : next.slice(next.length - limit);
}

export { terminateProcessTree } from './platform/index.mjs';
import { terminateProcessTree } from './platform/index.mjs';

// Exit codes a process gets because the OS is ending the session, not because
// it failed. Windows terminates a session's processes instead of asking them to
// stop: a process killed that way exits with 0x40010004 (DBG_TERMINATE_PROCESS),
// one created while the session is already logging off cannot initialize
// user32.dll and exits with 0xC0000142 (STATUS_DLL_INIT_FAILED), and a console
// closed under a process gives it 0xC000013A (STATUS_CONTROL_C_EXIT). None of
// them says anything about what the process was doing.
const SESSION_END_EXIT_CODES = new Map([
  [0x40010004, 'DBG_TERMINATE_PROCESS'],
  [0xc0000142, 'STATUS_DLL_INIT_FAILED'],
  [0xc000013a, 'STATUS_CONTROL_C_EXIT'],
]);

function exitCodeName(code) {
  return Number.isInteger(code) ? SESSION_END_EXIT_CODES.get(code >>> 0) ?? null : null;
}

// Whether an exit code means the session is ending rather than the process
// failing. A non-Windows host never produces these codes as a session end.
export function sessionEndedExit(code) {
  return exitCodeName(code) !== null;
}

// How a process ended, in words: `with code 1`,
// `with code 3221225794 (STATUS_DLL_INIT_FAILED)`, `by signal SIGKILL`; null
// when neither half of the exit was reported.
export function exitDescription(code, signal) {
  if (signal) return `by signal ${signal}`;
  if (!Number.isInteger(code)) return null;
  const name = exitCodeName(code);
  return name ? `with code ${code} (${name})` : `with code ${code}`;
}

// ` with code 1: session expired` — the exit that happened, and what the
// process said about it, without one standing in for the other; `: detail`
// when the exit itself is unknown.
export function describeFailure(code, signal, detail) {
  const how = exitDescription(code, signal);
  if (how) return detail ? ` ${how}: ${detail}` : ` ${how}`;
  return detail ? `: ${detail}` : '';
}

export class ProcessRegistry {
  constructor(logger, { detached = defaultDetached } = {}) {
    this.logger = logger;
    this.detached = detached;
    this.children = new Set();
  }

  register(child) {
    this.children.add(child);
    const unregister = () => this.children.delete(child);
    child.once('exit', unregister);
    child.once('error', unregister);
    return child;
  }

  async run(command, args, options = {}) {
    const {
      cwd,
      env = sanitizeNonArkChildEnv(process.env),
      input,
      timeoutMs = 60_000,
      stdoutLimit = 200_000,
      stderrLimit = 200_000,
      onStdoutLine,
    } = options;

    return await new Promise((resolve, reject) => {
      const child = this.register(spawn(command, args, {
        cwd,
        env,
        shell: false,
        detached: this.detached,
        stdio: ['pipe', 'pipe', 'pipe'],
      }));
      let stdout = '';
      let stderr = '';
      let lineBuffer = '';
      let timedOut = false;
      let settled = false;

      const timeout = setTimeout(() => {
        timedOut = true;
        terminateProcessTree(child, 'SIGTERM');
        setTimeout(() => terminateProcessTree(child, 'SIGKILL'), 2_000).unref();
      }, timeoutMs);
      timeout.unref();

      child.stdout?.setEncoding('utf8');
      child.stderr?.setEncoding('utf8');
      child.stdin?.on('error', () => {});
      child.stdout?.on('data', (chunk) => {
        stdout = appendTail(stdout, chunk, stdoutLimit);
        if (!onStdoutLine) return;
        lineBuffer += chunk;
        while (true) {
          const index = lineBuffer.indexOf('\n');
          if (index < 0) break;
          const line = lineBuffer.slice(0, index);
          lineBuffer = lineBuffer.slice(index + 1);
          if (line) {
            try { onStdoutLine(line); } catch {}
          }
        }
        if (lineBuffer.length > stdoutLimit) {
          lineBuffer = lineBuffer.slice(-stdoutLimit);
        }
      });
      child.stderr?.on('data', (chunk) => {
        stderr = appendTail(stderr, chunk, stderrLimit);
      });

      child.once('error', (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        reject(error);
      });
      child.once('exit', (code, signal) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        if (lineBuffer && onStdoutLine) {
          try { onStdoutLine(lineBuffer); } catch {}
        }
        resolve({ code, signal, stdout, stderr, timedOut });
      });

      if (input !== undefined) child.stdin?.end(input);
      else child.stdin?.end();
    });
  }

  async stopAll(graceMs = 5_000) {
    const children = [...this.children].filter((child) => child.exitCode === null);
    for (const child of children) terminateProcessTree(child, 'SIGTERM');
    if (children.length === 0) return;
    await Promise.race([
      Promise.all(children.map((child) => new Promise((resolve) => child.once('exit', resolve)))),
      new Promise((resolve) => setTimeout(resolve, graceMs)),
    ]);
    for (const child of children) terminateProcessTree(child, 'SIGKILL');
  }
}
