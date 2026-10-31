// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn } from 'node:child_process';
import { sanitizeAgentChildEnv, terminateProcessTree } from '../../daemon/src/process-utils.mjs';
import { captureStartupStderr } from '../../daemon/src/runtime-diagnostics.mjs';
import { safeError } from '../../daemon/src/logger.mjs';

// Initialize only: no thread, model request, user message or persisted session.
// An older TraeX can answer to the same command names but speaks ACP, not this protocol.
export function probeAppServer(bin, { env = process.env, spawnImpl = spawn, timeoutMs = 5000 } = {}) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawnImpl(bin, ['app-server', '--listen', 'stdio://'], {
        env: sanitizeAgentChildEnv(env), shell: false,
        detached: process.platform !== 'win32', stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (error) {
      resolve({ compatible: false, reason: safeError(error).slice(0, 2000) });
      return;
    }
    const stderr = captureStartupStderr(child.stderr);
    let settled = false;
    let buffer = '';
    let received = 0;
    const timer = setTimeout(() => finish(false, 'app-server initialize timed out'), timeoutMs);
    function finish(compatible, reason = '') {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const detail = stderr.text();
      stderr.stop();
      // This is our disposable probe process group, never an installed service.
      terminateProcessTree(child, 'SIGKILL');
      child.stdin?.end();
      resolve({ compatible, reason: compatible ? '' : [safeError(reason), detail].filter(Boolean).join(': ').slice(0, 2400) });
    }
    child.once('error', (error) => finish(false, error.message));
    child.once('close', (code, signal) => finish(false, `app-server exited before initialize (code ${code}, signal ${signal})`));
    child.stdin?.on('error', (error) => finish(false, error.message));
    child.stdout?.setEncoding('utf8');
    child.stdout?.on('data', (chunk) => {
      if (settled) return;
      received += chunk.length;
      if (received > 65536) { finish(false, 'app-server initialize response exceeded 64 KiB'); return; }
      buffer += chunk;
      let index;
      while (!settled && (index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        let message;
        try { message = JSON.parse(line); } catch { continue; }
        if (message?.id !== 1) continue;
        if (message.error || !message.result || typeof message.result !== 'object' || Array.isArray(message.result)) {
          finish(false, 'app-server rejected initialize');
        } else {
          child.stdin.write(`${JSON.stringify({ method: 'initialized', params: {} })}\n`);
          finish(true);
        }
      }
    });
    child.stdin.write(`${JSON.stringify({ id: 1, method: 'initialize', params: {
      clientInfo: { name: 'vika-setup', version: '1' }, capabilities: { experimentalApi: true },
    } })}\n`);
  });
}

// ACP handshake only: initialize, then stop. No session/new, no model call.
// OpenCode 1 and 2 both answer, so version gating happens separately.
export function probeAcp(bin, { env = process.env, spawnImpl = spawn, timeoutMs = 8000 } = {}) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawnImpl(bin, ['acp'], {
        env: sanitizeAgentChildEnv(env), shell: false,
        detached: process.platform !== 'win32', stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (error) {
      resolve({ compatible: false, reason: safeError(error).slice(0, 2000) });
      return;
    }
    const stderr = captureStartupStderr(child.stderr);
    let settled = false;
    let buffer = '';
    const timer = setTimeout(() => finish(false, 'ACP initialize timed out'), timeoutMs);
    function finish(compatible, reason = '') {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const detail = stderr.text();
      stderr.stop();
      terminateProcessTree(child, 'SIGKILL');
      child.stdin?.end();
      resolve({ compatible, reason: compatible ? '' : [safeError(reason), detail].filter(Boolean).join(': ').slice(0, 2400) });
    }
    child.once('error', (error) => finish(false, error.message));
    child.once('close', (code, signal) => finish(false, `ACP exited before initialize (code ${code}, signal ${signal})`));
    child.stdin?.on('error', (error) => finish(false, error.message));
    child.stdout?.setEncoding('utf8');
    child.stdout?.on('data', (chunk) => {
      if (settled) return;
      buffer += chunk;
      let index;
      while (!settled && (index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        let message;
        try { message = JSON.parse(line); } catch { continue; }
        if (message?.id !== 1) continue;
        if (message.error || !message.result || typeof message.result !== 'object' || Array.isArray(message.result)) {
          finish(false, 'ACP rejected initialize');
        } else {
          finish(true);
        }
      }
    });
    child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {
      protocolVersion: 1,
      clientCapabilities: { fs: { readTextFile: false, writeTextFile: false }, terminal: false },
      clientInfo: { name: 'vika-setup', version: '1' },
    } })}\n`);
  });
}
