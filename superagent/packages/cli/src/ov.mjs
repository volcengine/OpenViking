// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { t } from './i18n.mjs';
import { OVConnection, OVUnavailableError, readOVConfig } from '../../daemon/src/ov-connection.mjs';

// An explicit instance or environment config is authoritative. Never fall back
// to another identity when that config is missing, invalid, or unavailable.

export const LOCAL_OV_URL = 'http://127.0.0.1:1933';
// Volcengine's hosted OpenViking Service, the address `ov config` uses for it.
export const HOSTED_OV_URL = 'https://api.vikingdb.cn-beijing.volces.com/openviking';

export const savedOVConfigPath = (homeDir = os.homedir()) => path.join(homeDir, '.ov-superagent', 'ov-connection.json');

export async function detectOpenViking({ env = process.env, homeDir = os.homedir(), fetchImpl = globalThis.fetch, configPath, config: suppliedConfig } = {}) {
  const defaultPath = path.join(homeDir, '.openviking', 'ovcli.conf');
  const savedPath = savedOVConfigPath(homeDir);
  const selected = suppliedConfig ? null : configPath ?? ((env.OPENVIKING_CLI_CONFIG_FILE || '').trim()
    || (fs.existsSync(savedPath) ? savedPath : null) || (fs.existsSync(defaultPath) ? defaultPath : null));
  let config;
  let connection;
  let health;
  try {
    config = suppliedConfig ?? (selected ? readOVConfig(selected) : { url: LOCAL_OV_URL });
    connection = new OVConnection(config, { fetch: fetchImpl, timeoutMs: 3_000 });
    // Keep health separate so missing credentials don't look like a dead server.
    health = await connection.json('/health');
    const user = await connection.authenticate();
    if (!selected && !suppliedConfig && health.auth_mode !== 'dev') {
      return { configPath: null, url: connection.url, usable: false, user: null, authMode: health.auth_mode ?? null,
        reason: t('OpenViking 服务已启动，请配置身份信息和凭据', 'OpenViking is running; configure identity and credentials') };
    }
    // Under an API key the configured user is not who the server sees. A
    // server that knows the caller says its account and role on /health.
    return { configPath: selected, url: connection.url, usable: true,
      user: user ?? health.user_id ?? (health.auth_mode === 'api_key' ? null : config.user) ?? null,
      account: health.account_id ?? null, role: health.role ?? null,
      version: health.version ?? null, authMode: health.auth_mode ?? null, reason: null };
  } catch (error) {
    let reason;
    if (!(error instanceof OVUnavailableError)) {
      reason = t('OpenViking 配置文件缺失或格式无效', 'the OpenViking configuration file is missing or invalid');
    } else if (health && error.httpStatus === 400 && health.auth_mode === 'trusted' && (!config.account || !config.user)) {
      reason = t('OpenViking 服务已启动；Trusted 模式需要填写 Account 和 User，请手动配置或指定配置文件',
        'OpenViking is running; Trusted mode requires Account and User. Configure them manually or choose a configuration file');
    } else if (health && [401, 403].includes(error.httpStatus)) {
      reason = t(`OpenViking 服务已启动，但认证或权限检查失败（HTTP ${error.httpStatus}），请检查凭据和身份配置`,
        `OpenViking is running, but authentication or authorization failed (HTTP ${error.httpStatus}); check credentials and identity`);
    } else if (!health && [401, 403].includes(error.httpStatus)) {
      // The hosted service checks the key before it answers /health.
      reason = t(`OpenViking 拒绝了 API Key（HTTP ${error.httpStatus}），请检查 API Key 是否填写正确`,
        `OpenViking rejected the API key (HTTP ${error.httpStatus}); check that it is entered correctly`);
    } else if (health) {
      reason = error.httpStatus
        ? t(`OpenViking 健康检查通过，但身份检查失败（HTTP ${error.httpStatus}），请检查服务和配置`,
          `OpenViking health check passed, but the identity check failed (HTTP ${error.httpStatus}); check the service and configuration`)
        : t('OpenViking 健康检查通过，但身份检查没有完成（连不上、超时或服务还在初始化），请重试',
          'OpenViking health check passed, but the identity check did not finish (unreachable, timed out, or still starting); retry');
    } else {
      reason = error.httpStatus
        ? t(`OpenViking 健康检查失败（HTTP ${error.httpStatus}），请检查服务和配置`,
          `OpenViking health check failed (HTTP ${error.httpStatus}); check the service and configuration`)
        : t('OpenViking 服务不可用或 3 秒内未响应', 'OpenViking is unavailable or did not respond within 3 seconds');
    }
    return { configPath: selected, url: connection?.url ?? '', usable: false, user: null, authMode: health?.auth_mode ?? null,
      reason };
  }
}

// Fields that name a peer. A peer is someone the agent talks to, which the
// daemon names per message; an instance is a user of its own, never a peer.
export const PEER_FIELDS = Object.freeze(['actor_peer_id', 'peer_id', 'agent_id']);

// Whether requests name their user (dev, trusted) instead of an API key
// deciding it. A server that does not say goes by whether there is a key.
export function requestNamesUser(authMode, config = {}) {
  return authMode ? authMode === 'dev' || authMode === 'trusted' : !config.api_key;
}

// The user an instance is in OV: the connection's user, then the instance id.
// Each instance keeps a memory of its own, and in an account several people
// share, one owner's instances stay apart from another's. A name already for
// this instance (its own config chosen again) is kept as it is.
export function instanceUserName(base, instanceId) {
  const owner = String(base ?? '').trim();
  if (!owner || owner === instanceId || owner.endsWith(`.${instanceId}`)) return owner || instanceId;
  return `${owner}.${instanceId}`;
}

// The instance's own connection: the chosen one, with this instance's identity
// and no peer. Where requests name the user it is derived here. Under an API
// key the key decides; with an administrator's key the owner may instead have
// chosen a user of the instance's own (`identity.mode` 'register'), which is
// created now and replaces the administrator's key, which is not kept.
export async function instanceOvConfig({
  detected, instanceId, fetchImpl = globalThis.fetch, registrations = new OVRegistrations(),
}) {
  const source = detected.config ?? (detected.configPath ? readOVConfig(detected.configPath) : { url: detected.url });
  const config = { ...source };
  for (const field of PEER_FIELDS) delete config[field];
  if (detected.identity?.mode === 'register') {
    const user = instanceUserName(detected.user, instanceId);
    return registrations.created({ url: config.url, account: detected.account, user })
      ?? registrations.register({ admin: config, account: detected.account, user, fetchImpl });
  }
  if (requestNamesUser(detected.authMode, config)) config.user = instanceUserName(config.user, instanceId);
  return config;
}

const TERMINAL_TASK = new Set(['completed', 'failed', 'cancelled']);

// The users an administrator key created during one setup run. OV answers a
// second registration of a name with 409, while the config holding the key is
// a pending file setup removes when the owner goes back or cancels. So in one
// run a user is created once and its key reused. When setup ends, the users
// the saved instance does not use are deleted again. OV deletes in the
// background; until it is done the name is still taken, so the wait is
// bounded and an unfinished deletion is reported.
export class OVRegistrations {
  constructor({ deleteWaitMs = 15_000, pollMs = 500 } = {}) {
    this.users = [];
    this.deleteWaitMs = deleteWaitMs;
    this.pollMs = pollMs;
  }

  // The instance config of a user created in this run, a copy.
  created({ url, account, user }) {
    const entry = this.users.find((other) => sameServer(other.url, url) && other.account === account && other.user === user);
    return entry ? structuredClone(entry.config) : null;
  }

  async register({ admin, account, user, fetchImpl }) {
    let result;
    try {
      ({ result } = await new OVConnection(admin, { fetch: fetchImpl })
        .json(`/api/v1/admin/accounts/${encodeURIComponent(account)}/users`, { user_id: user, role: 'user' }));
    } catch (error) {
      throw new Error(error.httpStatus === 409
        ? t(`OpenViking 里已有用户 ${user}。刚取消过 setup 的话，它可能还在删除中，稍等再试；如果它就是这个实例之前的身份，请改用它自己的 API Key`,
          `OpenViking already has a user ${user}. After a cancelled setup it may still be being deleted, so retry in a moment; if it is this instance’s earlier identity, use that user’s own API key`)
        : t(`无法在 OpenViking 里创建用户 ${user}（${error.httpStatus ? `HTTP ${error.httpStatus}` : '连不上或超时'}）`,
          `Could not create the OpenViking user ${user} (${error.httpStatus ? `HTTP ${error.httpStatus}` : 'unreachable or timed out'})`));
    }
    if (typeof result?.user_key !== 'string' || !result.user_key) {
      throw new Error(t(`OpenViking 创建了用户 ${user}，但没有返回它的 API Key`, `OpenViking created the user ${user} but returned no API key for it`));
    }
    const { account: _account, user: _user, api_key: _key, ...rest } = admin;
    const config = { ...rest, api_key: result.user_key, user };
    // The administrator key stays in memory only, to delete an unused user.
    this.users.push({ url: admin.url, account, user, admin, config: structuredClone(config), kept: false });
    return config;
  }

  // The saved instance's config file: a user created for it stays. With no
  // user created in this run there is nothing to keep, or to read.
  keep(file) {
    if (!this.users.length) return;
    const { api_key: key } = readOVConfig(file);
    for (const entry of this.users) if (entry.config.api_key === key) entry.kept = true;
  }

  // Deletes the users nothing kept, with the administrator key that created
  // each, and waits a while for OV to finish. Names what could not be
  // deleted (failed) and what OV is still deleting (pending).
  async discard({ fetchImpl = globalThis.fetch } = {}) {
    const failed = [];
    const pending = [];
    for (const { admin, account, user, kept } of this.users.splice(0)) {
      if (kept) continue;
      const connection = new OVConnection(admin, { fetch: fetchImpl });
      let task;
      try {
        ({ result: { task_id: task } = {} } = await connection.send(
          `/api/v1/admin/accounts/${encodeURIComponent(account)}/users/${encodeURIComponent(user)}`,
          { method: 'DELETE', headers: connection.headers }, connection.timeoutMs));
      } catch (error) {
        if (error.httpStatus !== 404) failed.push(user);
        continue;
      }
      const outcome = task ? await this.settled(connection, task) : 'completed';
      if (outcome === 'completed') continue;
      (outcome === 'pending' ? pending : failed).push(user);
    }
    return { failed, pending };
  }

  async settled(connection, task) {
    const deadline = Date.now() + this.deleteWaitMs;
    for (;;) {
      try {
        const { result } = await connection.json(`/api/v1/tasks/${encodeURIComponent(task)}`);
        if (TERMINAL_TASK.has(result?.status)) return result.status;
      } catch {
        // A status that could not be read is asked again until the deadline.
      }
      if (Date.now() >= deadline) return 'pending';
      await new Promise((resolve) => { setTimeout(resolve, this.pollMs); });
    }
  }
}

const sameServer = (a, b) => String(a ?? '').replace(/\/+$/, '') === String(b ?? '').replace(/\/+$/, '');

// Writes an instance config (see instanceOvConfig), private to the owner.
export function writeInstanceOvConfig({ config, target, exclusive = false }) {
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  if (exclusive) {
    fs.writeFileSync(target, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600, flag: 'wx' });
    return target;
  }
  const temporary = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, target);
  return target;
}
