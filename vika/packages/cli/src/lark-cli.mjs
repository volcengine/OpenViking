// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn } from 'node:child_process';

// The lark-cli calls setup needs: write the bot profile, authorize the owner
// as a user (the second QR code), read who authorized, send a greeting, list
// which app each profile uses, and read an app's settings as its bot.
// Profiles go into the user's own lark-cli config, where the daemon reads them.

const SCRUBBED = ['LARKSUITE_CLI_PROFILE', 'LARKSUITE_CLI_APP_ID', 'LARKSUITE_CLI_APP_SECRET',
  'LARKSUITE_CLI_USER_ACCESS_TOKEN', 'LARKSUITE_CLI_TENANT_ACCESS_TOKEN'];

export function larkCliEnv(env = process.env) {
  const next = { ...env, LARKSUITE_CLI_NO_UPDATE_NOTIFIER: '1', LARKSUITE_CLI_NO_SKILLS_NOTIFIER: '1' };
  for (const key of SCRUBBED) delete next[key];
  return next;
}

export function runLarkCli(bin, args, { input = '', env = larkCliEnv(), timeoutMs = 120_000, spawnImpl = spawn } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawnImpl(bin, args, { env, stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    const timer = timeoutMs ? setTimeout(() => child.kill('SIGTERM'), timeoutMs) : null;
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.once('error', (error) => {
      if (timer) clearTimeout(timer);
      reject(error.code === 'ENOENT' ? new Error(`lark-cli not found: ${bin}`) : error);
    });
    child.once('close', (code) => {
      if (timer) clearTimeout(timer);
      resolve({ code, stdout, stderr });
    });
    child.stdin.end(input);
  });
}

function parseJson(text) {
  try { return JSON.parse(text); } catch { return null; }
}

function failure(result) {
  return (result.stderr || result.stdout).trim().slice(0, 600);
}

function findField(value, test) {
  if (!value || typeof value !== 'object') return null;
  for (const [key, child] of Object.entries(value)) {
    if (test(key) && typeof child === 'string' && child) return child;
    const nested = findField(child, test);
    if (nested) return nested;
  }
  return null;
}

export class LarkCli {
  constructor(bin, options = {}) {
    this.bin = bin;
    this.options = options;
  }

  run(args, options = {}) {
    return runLarkCli(this.bin, args, { ...this.options, ...options });
  }

  async version() {
    const result = await this.run(['--version'], { timeoutMs: 15_000 });
    if (result.code !== 0) throw new Error(`lark-cli --version failed: ${failure(result)}`);
    return result.stdout.trim().split(/\s+/).pop();
  }

  // Creates the profile, or updates it when it already exists.
  async configureProfile({ profile, appId, appSecret, brand }) {
    const result = await this.run(['config', 'init', '--app-id', appId, '--app-secret-stdin',
      '--brand', brand, '--name', profile], { input: `${appSecret}\n` });
    if (result.code !== 0) throw new Error(`lark-cli config init failed: ${failure(result)}`);
  }

  async beginUserLogin({ profile, scopes }) {
    const result = await this.run(['--profile', profile, 'auth', 'login', '--scope', scopes.join(' '), '--no-wait', '--json']);
    const started = parseJson(result.stdout);
    const url = findField(started, (key) => key === 'verification_uri_complete' || key === 'verification_url');
    const deviceCode = findField(started, (key) => key === 'device_code');
    if (result.code !== 0 || !url || !deviceCode) throw new Error(`lark-cli auth login failed: ${failure(result)}`);
    return { url, deviceCode };
  }

  // Blocks until the QR code is confirmed or expires.
  async finishUserLogin({ profile, deviceCode }) {
    const result = await this.run(['--profile', profile, 'auth', 'login', '--device-code', deviceCode, '--json'],
      { timeoutMs: 15 * 60_000 });
    if (result.code !== 0) throw new Error(`lark-cli authorization did not finish: ${failure(result)}`);
  }

  // Every profile with its app: [{ name, appId }]; none when it cannot be read.
  async profiles() {
    const result = await this.run(['profile', 'list'], { timeoutMs: 15_000 });
    const listed = result.code === 0 ? parseJson(result.stdout) : null;
    return Array.isArray(listed)
      ? listed.filter((entry) => typeof entry?.name === 'string' && typeof entry?.appId === 'string')
        .map(({ name, appId }) => ({ name, appId }))
      : [];
  }

  // A GET as the profile's bot, for readApp: { code, msg, data }.
  async get({ profile, pathname, query = {} }) {
    const result = await this.run(['--profile', profile, 'api', 'GET', pathname, '--params', JSON.stringify(query), '--as', 'bot'],
      { timeoutMs: 30_000 });
    const body = parseJson(result.stdout);
    if (body?.ok === true) return { code: 0, data: body.data };
    return { code: body?.error?.code ?? -1, msg: body?.error?.message ?? failure(result) };
  }

  async userOpenId({ profile }) {
    const result = await this.run(['--profile', profile, 'auth', 'status', '--json'], { timeoutMs: 30_000 });
    return parseJson(result.stdout)?.identities?.user?.openId ?? null;
  }

  // The scopes the owner's login carries; null when there is no login or it
  // cannot be read.
  async userScopes({ profile }) {
    const result = await this.run(['--profile', profile, 'auth', 'status', '--json'], { timeoutMs: 30_000 });
    const scope = parseJson(result.stdout)?.identities?.user?.scope;
    return typeof scope === 'string' && scope.trim() ? scope.trim().split(/\s+/) : null;
  }

  async sendText({ profile, userId, text }) {
    const result = await this.run(['--profile', profile, 'im', '+messages-send', '--as', 'bot',
      '--user-id', userId, '--text', text], { timeoutMs: 30_000 });
    if (result.code !== 0) throw new Error(`sending the greeting failed: ${failure(result)}`);
  }
}
