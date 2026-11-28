// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The backstop `ov-superagent restart` leaves behind when the app it restarts
// is the one that command itself runs inside — which is the case whenever the
// agent in a chat runs it, because that shell is a child of the daemon. PM2
// removes the app, the daemon takes the session tree down with it, and the
// `start` after the delete never runs: no app entry is left, and nothing
// registered brings it back.
//
// This process is started detached from that tree (control.mjs,
// scheduleRestartWatchdog). It waits for the restart command to disappear and
// then runs `start` for the same app, which registers it again. `start` on an
// app that is already online only verifies it, so an ordinary restart — the
// command lived and started the app itself — ends here without touching
// anything.
//
// Started by `restart`, never by hand:
//   node restart-watchdog.mjs --instance primary --wait-pid 1234

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { homeDir } from '../../daemon/src/platform/index.mjs';
import { startCommand } from './control.mjs';

// How long to wait for the restart command to go away. A delete waits up to the
// app's kill_timeout (30s) for a graceful exit, so a command that hangs for
// longer is stuck: registering the app anyway is better than waiting forever.
const DEFAULT_DEADLINE_MS = 120_000;
const DEFAULT_POLL_MS = 500;
// The command disappears when the daemon's shutdown kills it, which is before
// PM2 has finished removing the entry. Give that removal time to land, or the
// `start` below runs against an entry that is still on its way out.
const DEFAULT_SETTLE_MS = 5_000;
const ATTEMPTS = 2;
const RETRY_MS = 5_000;

export function pidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    // EPERM: the process exists and belongs to someone else.
    return error?.code === 'EPERM';
  }
}

// One line per run, next to the daemon's own logs: a self-restart that did not
// come back is otherwise invisible, because the command that asked for it is
// gone and PM2 has no entry to keep a log for.
export function watchdogNote(message, env = process.env) {
  const line = `${new Date().toISOString()} restart-watchdog ${message}\n`;
  if (env.OV_SUPERAGENT_WATCHDOG_LOG === '-') {
    process.stderr.write(line);
    return;
  }
  const home = homeDir(env);
  if (!home) return;
  const file = path.join(home, '.ov-superagent', 'logs', 'restart-watchdog.log');
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.appendFileSync(file, line, { mode: 0o600 });
  } catch { /* a log that cannot be written must not keep the app down */ }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Exported for its test; the entry point at the end only parses arguments.
export async function watchdogMain({
  instance,
  waitPid,
  deadlineMs = DEFAULT_DEADLINE_MS,
  pollMs = DEFAULT_POLL_MS,
  settleMs = DEFAULT_SETTLE_MS,
  env = process.env,
  start = startCommand,
  alive = pidAlive,
  wait = sleep,
  note = (message) => watchdogNote(message),
  now = () => Date.now(),
} = {}) {
  if (!instance) {
    note('no --instance given; nothing to do');
    return 2;
  }
  const pid = Number(waitPid);
  const deadline = now() + deadlineMs;
  while (alive(pid) && now() < deadline) await wait(pollMs);
  if (alive(pid)) note(`${instance}: restart command ${pid} still runs after ${deadlineMs}ms; registering anyway`);
  else note(`${instance}: restart command ${Number.isInteger(pid) ? pid : '(none)'} is gone; making sure the app is up`);
  if (settleMs > 0) await wait(settleMs);
  let errors = '';
  const stream = { write: (text) => { errors += text; } };
  let code = 1;
  for (let attempt = 1; attempt <= ATTEMPTS; attempt += 1) {
    errors = '';
    try {
      code = await start(instance, { env, stdout: stream, stderr: stream });
    } catch (error) {
      code = 1;
      errors = String(error?.message ?? error);
    }
    if (code === 0) {
      note(`${instance}: registered`);
      return 0;
    }
    note(`${instance}: start attempt ${attempt} failed: ${errors.trim() || `exit ${code}`}`);
    if (attempt < ATTEMPTS) await wait(RETRY_MS);
  }
  return code === 0 ? 0 : 1;
}

function argument(name) {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 && index + 1 < process.argv.length ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === pathToFileURL(fileURLToPath(import.meta.url)).href) {
  process.exitCode = await watchdogMain({
    instance: argument('instance'),
    waitPid: Number(argument('wait-pid')),
    deadlineMs: Number(argument('deadline-ms')) || DEFAULT_DEADLINE_MS,
  });
}
