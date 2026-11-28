// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// /restart: the owner restarts the instance they are talking to, without a
// terminal. Owner and direct chat only, like /tasks and /workers — anyone else
// could otherwise stop the agent in the middle of someone's work.
//
// The restart itself is handed to a process of its own, started after this
// command has answered: a restart run inside the daemon kills the session tree
// it runs in (packages/cli/src/restart-watchdog.mjs), so the reply has to be on
// its way out before the instance goes quiet.

import { fileURLToPath } from 'node:url';

import { spawn as platformSpawn } from './platform/index.mjs';
import { sanitizeNonArkChildEnv } from './process-utils.mjs';

export const RESTART_USAGE = '用法：/restart（重启当前实例，仅限 owner 私聊，无需终端）';

const CLI = fileURLToPath(new URL('../../cli/bin/ov-superagent.mjs', import.meta.url));
// The command's reply is sent when executeRestartCommand returns; the restart
// waits this long, so the owner reads why the instance went quiet.
const DEFAULT_DELAY_MS = 3_000;

// Runs `ov-superagent restart <instance>` detached, so it outlives this daemon:
// a command spawned as its plain child is killed with the session tree when the
// daemon shuts down, and its own `start` never runs.
export function scheduleInstanceRestart({
  instanceId,
  delayMs = DEFAULT_DELAY_MS,
  spawnImpl = platformSpawn,
  env = process.env,
  node = process.execPath,
  cli = CLI,
  timer = setTimeout,
} = {}) {
  if (!instanceId) throw new Error('/restart needs the instance id');
  timer(() => {
    try {
      // The CLI restarts a PM2 app; it has no use for a model credential.
      spawnImpl(node, [cli, 'restart', instanceId], { detached: true, stdio: 'ignore', env: sanitizeNonArkChildEnv(env) })?.unref?.();
    } catch { /* the watchdog the CLI leaves behind is the second chance */ }
  }, delayMs);
}

export function executeRestartCommand(command, { event, connectors, schedule = null } = {}) {
  if (event?.chatType !== 'p2p' || event.senderType !== 'user' || !connectors?.isOwner?.(event)) {
    return { ok: false, text: '仅限已绑定的 owner 在与机器人的私聊中使用 /restart。' };
  }
  if (String(command.arg ?? '').trim()) return { ok: false, text: RESTART_USAGE };
  if (!schedule) return { ok: false, text: '当前环境不能重启这个实例。' };
  try {
    schedule();
  } catch (error) {
    return { ok: false, text: `没能安排重启：${error?.message ?? error}` };
  }
  return { ok: true, text: '正在重启当前实例：这条回复送达后开始，期间十几秒不响应，之后照常工作。' };
}
