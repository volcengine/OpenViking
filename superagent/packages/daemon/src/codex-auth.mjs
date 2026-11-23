// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { createHash } from 'node:crypto';
import fs from 'node:fs';

const digest = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');

// Compare identity, not rotating access/refresh tokens. Never return credentials,
// account ids or email addresses to the caller. null means an unreadable or
// incomplete write: keep the last known identity and try again next time.
export function codexAuthIdentity(file) {
  let auth;
  try {
    if (fs.statSync(file).size > 1024 * 1024) return null;
    auth = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    return error.code === 'ENOENT' ? 'signed-out' : null;
  }
  if (!auth || typeof auth !== 'object' || Array.isArray(auth)) return null;
  if (auth.auth_mode !== 'chatgpt' && typeof auth.OPENAI_API_KEY === 'string' && auth.OPENAI_API_KEY) {
    return digest(['apikey', auth.OPENAI_API_KEY]);
  }
  const tokens = auth.tokens;
  if (!tokens) return auth.auth_mode === 'chatgpt' ? null : 'signed-out';
  try {
    const payload = JSON.parse(Buffer.from(tokens.id_token.split('.')[1], 'base64url').toString('utf8'));
    const claims = payload['https://api.openai.com/auth'] ?? {};
    const user = claims.chatgpt_user_id || payload.sub || payload.email;
    const account = tokens.account_id || claims.chatgpt_account_id;
    if (!user || !account) return null;
    return digest(['chatgpt', user, account, payload.auth_time ?? null, payload.sid ?? null]);
  } catch { return null; }
}

export function codexAccountSummary(result) {
  const account = result?.account;
  if (!account) return { type: 'signed-out', email: null, planType: null };
  const email = typeof account.email === 'string' ? account.email : '';
  const match = /^([^@\s])[^@\s]*@([^@\s]+)$/.exec(email);
  return {
    type: account.type,
    email: match ? `${match[1]}***@${match[2]}` : null,
    planType: typeof account.planType === 'string' ? account.planType : null,
  };
}

// Polling also handles atomic rename and a home directory created after startup.
// Require two identical observations so logout/login and partial writes coalesce.
export class CodexAuthMonitor {
  constructor(file, onChange, { intervalMs = 1000, readIdentity = codexAuthIdentity } = {}) {
    this.file = file;
    this.onChange = onChange;
    this.intervalMs = intervalMs;
    this.readIdentity = readIdentity;
    this.timer = null;
    this.identity = null;
    this.candidate = null;
  }

  start() {
    if (this.timer) return;
    this.identity = this.readIdentity(this.file);
    this.candidate = null;
    this.timer = setInterval(() => this.poll(), this.intervalMs);
    this.timer.unref?.();
  }

  poll() {
    const next = this.readIdentity(this.file);
    if (next === null || next === this.identity) { this.candidate = null; return; }
    if (next !== this.candidate) { this.candidate = next; return; }
    this.identity = next;
    this.candidate = null;
    this.onChange();
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
  }
}
