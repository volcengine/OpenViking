// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { SlackError, validToken } from './api.mjs';

export const SLACK_CLI_VERSION = '4.10.0';
export const bundledSlackCli = () => fileURLToPath(new URL('../../../../bin/slack', import.meta.url));
const READ_METHODS = new Set(['users.conversations', 'conversations.history', 'conversations.replies',
  'conversations.members', 'users.info']);

// Only the connector sees the bot credential. Each invocation has an isolated
// config directory and no project, saved login, custom API host or user token.
export class SlackCli {
  constructor(token, { bin = bundledSlackCli(), registry = null } = {}) {
    if (!validToken(token, 'bot')) throw new Error('Invalid Slack bot token');
    Object.assign(this, { token, bin, registry });
  }

  async call(method, params = {}, signal = AbortSignal.timeout(30_000)) {
    if (!READ_METHODS.has(method)) throw new Error('Unsupported Slack CLI query');
    signal.throwIfAborted();
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-slack-cli-'));
    const env = {};
    for (const key of ['PATH', 'HOME', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
      'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY', 'https_proxy', 'http_proxy', 'no_proxy']) {
      if (process.env[key] !== undefined) env[key] = process.env[key];
    }
    Object.assign(env, { SLACK_BOT_TOKEN: this.token, SLACK_DISABLE_TELEMETRY: 'true', NO_COLOR: '1' });
    try {
      // Some read methods (notably conversations.members/users.info) ignore
      // JSON bodies. Let the official CLI encode/authenticate a form request.
      const data = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined)).toString();
      const { error, stdout } = await new Promise((resolve) => {
        const child = execFile(this.bin, ['api', method, '--data', data,
          '--config-dir', directory, '--skip-update', '--no-color'], {
          cwd: directory, env, signal, timeout: 30_000, killSignal: 'SIGKILL', maxBuffer: 4 * 1024 * 1024,
        }, (error, stdout) => resolve({ error, stdout }));
        this.registry?.register(child);
        child.stdin?.end();
      });
      if (signal.aborted) throw new Error('Slack CLI query cancelled or timed out');
      if (error?.code === 'ENOENT') throw new Error('Slack CLI is missing; run npm run setup or install a Vika release containing Slack CLI');
      let result;
      try { result = JSON.parse(stdout); } catch { /* Never expose CLI stderr, stdout or errors containing credentials. */ }
      if (result?.ok === false) throw new SlackError(method, result.error);
      if (error || result?.ok !== true) throw new Error('Slack CLI query failed or returned invalid JSON');
      return result;
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  }
}
