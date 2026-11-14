// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { isLocalSoul } from '../../daemon/src/conversation-policy.mjs';
import { OVConnection, readOVConfig } from '../../daemon/src/ov-connection.mjs';
import { availableInstanceOvConfig } from './manifest.mjs';
import {
  HOSTED_OV_URL, LOCAL_OV_URL, PEER_FIELDS, detectOpenViking, instanceOvConfig, instanceUserName, requestNamesUser,
  savedOVConfigPath, writeInstanceOvConfig,
} from './ov.mjs';
import { GoBack, PromptAborted } from './prompt.mjs';
import { t } from './i18n.mjs';

// Only display the address, never URL credentials or a query string.
function address(value) {
  try {
    const url = new URL(value);
    return `${url.protocol}//${url.host}${url.pathname.replace(/\/$/, '')}`;
  } catch { return t('地址未配置', 'no address configured'); }
}

function sourceConfig(detected) {
  return detected.config ?? (detected.configPath ? readOVConfig(detected.configPath) : { url: detected.url });
}

const canonical = (value) => (Array.isArray(value) ? value.map(canonical)
  : value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]))
    : value);

// A new instance is named after its connection is chosen, so until then its
// OV user can only be shown with the name still to come.
const UNNAMED = () => t('<实例名称>', '<instance name>');

// Older OV releases let memory extraction update soul.md. An absent or
// unfamiliar version cannot prove that behavior, so it never blocks setup or
// produces a speculative warning.
export function hasLegacySoulExtraction(version) {
  if (typeof version !== 'string') return false;
  const match = version.trim().match(/^v?(\d+)\.(\d+)(?:\.(\d+))?/);
  if (!match) return false;
  const parts = match.slice(1).map((part) => Number(part ?? 0));
  return parts[0] === 0 && (parts[1] < 5 || (parts[1] === 5 && parts[2] < 5));
}

function warnAboutLegacySoulExtraction(prompter, version) {
  if (!hasLegacySoulExtraction(version)) return;
  prompter.warn(t(`当前 OpenViking 版本为 ${version}，低于 0.5.5；记忆抽取链路可能更新 soul，覆盖角色设定。建议升级到 0.5.5 或更高版本，或为实例指定自定义 soulUri。`,
    `OpenViking ${version} is older than 0.5.5; its memory extraction pipeline may update the soul and overwrite the role. Upgrade to OpenViking 0.5.5 or later, or set a custom soulUri for this instance.`));
}

// Everything an instance copy takes from a config: all of it but the peer the
// copy drops. Null for a file that cannot be read.
function connectionKey(configPath) {
  let config;
  try { config = { ...readOVConfig(configPath) }; } catch { return null; }
  for (const field of PEER_FIELDS) delete config[field];
  config.url = String(config.url ?? '').replace(/\/+$/, '');
  return JSON.stringify(canonical(config));
}

export async function discoverOpenViking({
  env = process.env, homeDir = env.HOME || os.homedir(), fetchImpl = globalThis.fetch,
  preferredConfigPath, detect = detectOpenViking,
} = {}) {
  const sources = [];
  const seen = new Set();
  const add = (configPath, label) => {
    if (!configPath || seen.has(configPath)) return;
    seen.add(configPath);
    sources.push({ configPath, label });
  };
  // Labels say what choosing does, not where a file lives.
  add(preferredConfigPath, t('保持现在的连接', 'Keep the current connection'));
  add((env.OPENVIKING_CLI_CONFIG_FILE || '').trim(),
    t('用环境变量 OPENVIKING_CLI_CONFIG_FILE 指的配置', 'Use the config OPENVIKING_CLI_CONFIG_FILE points to'));
  for (const [file, label] of [
    [savedOVConfigPath(homeDir), t('用上次在 setup 里填的连接', 'Use the connection entered in setup last time')],
    [path.join(homeDir, '.openviking', 'ovcli.conf'), t('用 OpenViking 命令行的配置', 'Use the OpenViking CLI’s config')],
  ]) if (fs.existsSync(file)) add(file, label);
  // Choosing either of two identical connections gives the instance the same
  // config (its own identity is kept), so only the first is offered.
  const keys = new Set();
  const unique = sources.filter(({ configPath }) => {
    const key = connectionKey(configPath);
    if (key === null) return true;
    if (keys.has(key)) return false;
    keys.add(key);
    return true;
  });
  // Only the instance being edited may contribute a private config.
  const candidates = await Promise.all(unique.map(async (source) => ({
    ...await detect({ env, homeDir, fetchImpl, ...source }), ...source,
  })));
  // A configured local connection already checks this service with its identity.
  // Probing it again without credentials produces misleading Trusted-mode errors.
  if (!candidates.some(({ url }) => url === LOCAL_OV_URL)) {
    const source = { label: t('用本机默认地址（不带凭据）', 'Use the local default address (no credentials)'), config: { url: LOCAL_OV_URL } };
    const result = await detect({ env, homeDir, fetchImpl, ...source });
    if (result.usable && result.authMode !== 'dev') {
      result.usable = false;
      result.reason = t('OpenViking 服务已启动，请配置身份信息和凭据', 'OpenViking is running; configure identity and credentials');
    }
    candidates.push({ ...result, ...source });
  }
  return candidates;
}

// Where it goes, who the instance would be there, and whether it works or why
// not. Where requests name the user, choosing it makes the instance a user of
// its own (instanceOvConfig), not the connection's user; the instance's own
// config, kept as it is, stays what it says. Under an API key the key's user
// is the instance, and anyone else logging in with it is named.
function describe(candidate, { preferredConfigPath = null, instanceId = null, taken = [] } = {}) {
  const where = address(candidate.url || candidate.config?.url);
  if (!candidate.usable) return `${where} · ${candidate.reason ?? t('不可用', 'unavailable')}`;
  const config = sourceConfig(candidate);
  const kept = Boolean(preferredConfigPath) && candidate.configPath === preferredConfigPath;
  let user = candidate.user;
  let holder = null;
  if (!kept && requestNamesUser(candidate.authMode, config)) {
    user = instanceUserName(candidate.user ?? config.user, instanceId || UNNAMED());
  } else if (!kept) {
    holder = holderOf(taken, { url: candidate.url || config.url, apiKey: config.api_key, user: candidate.user });
  }
  return `${where}${user ? ` · user=${user}` : ''}${holder ? ` · ${t(`${holder.label} 也在用`, `also used by ${holder.label}`)}` : ''}`
    + ` · ${t('可用', 'ready')}`;
}

async function manualConfig(prompter, previous = {}) {
  const config = { ...previous };
  delete config.api_key;
  config.url = await prompter.text(t('OpenViking URL（支持远端地址和自定义端口）', 'OpenViking URL (remote hosts and custom ports supported)'), {
    initial: previous.url ? address(previous.url) : LOCAL_OV_URL,
    validate: (url) => {
      try { new OVConnection({ url }); return null; } catch {
        return t('请输入有效的 http:// 或 https:// 地址；凭据请在后续步骤填写', 'Enter a valid http:// or https:// URL; enter credentials in the following steps');
      }
    },
  });
  // Never carry a credential or an identity to a different address without
  // the owner's input: another server's user would name a private area this
  // one refuses.
  const sameServer = config.url === previous.url;
  const credential = await prompter.select(t('API Key', 'API key'), [
    ...(previous.api_key && sameServer ? [{ label: t('保留已配置的密钥', 'Keep the configured key'), value: 'keep' }] : []),
    { label: t('输入 API Key', 'Enter an API key'), value: 'enter' },
    { label: t('服务不需要 API Key', 'The service does not require an API key'), value: 'none' },
  ]);
  if (credential === 'keep') config.api_key = previous.api_key;
  if (credential === 'enter') config.api_key = await prompter.secret(t('API Key（隐藏输入）', 'API key (hidden input)'));
  for (const [key, label] of [['account', 'Account'], ['user', 'User'], ['agent', 'Agent']]) {
    const value = await prompter.text(t(`${label}（可选；回车沿用默认值，输入 - 清空）`, `${label} (optional; Enter keeps the default, - clears it)`), {
      initial: sameServer ? previous[key] || '' : '',
    });
    if (value && value !== '-') config[key] = value;
    else delete config[key];
  }
  return config;
}

// The hosted service has one address and takes identity from the key alone.
async function hostedConfig(prompter, previous = {}) {
  const config = previous.url === HOSTED_OV_URL ? { ...previous } : { url: HOSTED_OV_URL };
  for (const key of ['api_key', 'account', 'user', 'agent']) delete config[key];
  const keep = previous.url === HOSTED_OV_URL && previous.api_key
    && await prompter.select(t('API Key', 'API key'), [
      { label: t('保留已配置的密钥', 'Keep the configured key'), value: true },
      { label: t('输入 API Key', 'Enter an API key'), value: false },
    ]);
  config.api_key = keep ? previous.api_key : await prompter.secret(
    t('API Key（OpenViking 控制台 → 用户管理 → API Key；隐藏输入）',
      'API key (OpenViking console → User management → API Key; hidden input)'));
  return config;
}

// An API key alone decides who a request runs as; the server ignores Account
// and User headers. Keep the user the key logs in as, which names the
// instance's private area (mail archive paths), instead of a typed or stale one.
function keyIdentity(config, result) {
  if (result.authMode !== 'api_key' || !result.user) return config;
  const { account: _account, ...rest } = config;
  return { ...rest, user: result.user };
}

// Who the other instances, and the owner's own OpenViking CLI (which their
// Claude Code or Codex plugins use too), are in OV. Under an API key the key is
// the identity, so one of theirs is not handed to another instance unasked.
export function takenIdentities({ agents = [], exclude = null, homeDir = os.homedir() } = {}) {
  const taken = [];
  for (const agent of agents) {
    if (agent.id === exclude || !agent.ovConfig) continue;
    try {
      const config = readOVConfig(agent.ovConfig);
      taken.push({ label: t(`实例 ${agent.id}`, `instance ${agent.id}`), url: config.url, user: config.user ?? null, apiKey: config.api_key ?? null });
    } catch {}
  }
  try {
    const config = readOVConfig(path.join(homeDir, '.openviking', 'ovcli.conf'));
    // Its user field does not say who its key logs in as; only the key does.
    if (config.api_key) taken.push({ label: t('OpenViking 命令行', 'the OpenViking CLI'), url: config.url, user: null, apiKey: config.api_key });
  } catch {}
  return taken;
}

// Who of `taken` already logs in as this identity on that server, if anyone.
function holderOf(taken, { url, apiKey, user }) {
  return taken.find((other) => address(other.url) === address(url)
    && (Boolean(apiKey && other.apiKey === apiKey) || Boolean(user && other.user === user))) ?? null;
}

// Who the instance is in OV. Where requests name the user, each instance gets
// its own (instanceOvConfig). Under an API key the key decides, so a key that
// another instance or the owner's CLI logs in with would share that memory,
// which only the owner can choose; an administrator's key can instead create a
// user of the instance's own, the administrator's key itself not kept. Null
// goes back to the connection menu.
async function chooseIdentity(prompter, { result, config, taken, instanceId }) {
  if (requestNamesUser(result.authMode, config)) return { mode: 'instance' };
  const holder = holderOf(taken, { url: result.url || config.url, apiKey: config.api_key, user: result.user });
  const canRegister = result.role === 'admin' && Boolean(result.account) && Boolean(result.user);
  if (!holder && !canRegister) return { mode: 'key' };
  const user = result.user ?? t('这把 Key 的用户', 'the key’s user');
  const created = instanceUserName(result.user, instanceId || UNNAMED());
  const choice = await prompter.select(t('这个实例在 OpenViking 里的身份', 'This instance’s identity in OpenViking'), [
    ...(canRegister ? [{
      label: t('为这个实例新建用户', 'Create a user for this instance'),
      hint: t(`用这把管理员 Key 创建 ${created}；管理员 Key 不保存`, `${created}, created with this administrator key, which is not kept`),
      value: 'register',
    }] : []),
    holder
      ? { label: t(`共用 ${user}（${holder.label} 也在用）`, `Share ${user} with ${holder.label}`),
        hint: t('两边的记忆不隔离', 'their memories are not kept apart'), value: 'shared' }
      : { label: t(`以 ${user} 运行`, `Run as ${user}`), hint: t('管理员身份', 'an administrator'), value: 'key' },
    { label: t('换一个连接或 Key', 'Choose another connection or key'), value: 'back' },
  ]);
  return choice === 'back' ? null : { mode: choice };
}

// Always offer both manual entry and importing a file, even if discovery found
// a healthy connection. Failures return to the menu; nothing is persisted here.
// `canGoBack` lets ← leave the menu; `leaveTo` names where its last option goes
// back to, instead of quitting setup. `previous` is what the menu returned
// before, offered again when ← comes back to it. `taken` (takenIdentities) and
// `instanceId` decide who the instance is in OV (chooseIdentity).
export async function configureOpenViking({
  prompter, env = process.env, homeDir = env.HOME || os.homedir(), fetchImpl = globalThis.fetch,
  preferredConfigPath, detect = detectOpenViking, title = 'OpenViking', canGoBack = false, leaveTo = null, previous = null,
  taken = [], instanceId = null,
} = {}) {
  if (title) prompter.section(title);
  const options = { env, homeDir, fetchImpl, preferredConfigPath, detect };
  let candidates = await discoverOpenViking(options);
  let draft;
  try {
    draft = previous ? structuredClone(previous.config)
      : sourceConfig((preferredConfigPath && candidates.find((entry) => entry.configPath === preferredConfigPath))
        || candidates.find((entry) => entry.usable) || candidates[0]);
  } catch { draft = {}; }
  for (;;) {
    const before = { hosted: candidates.length, manual: candidates.length + 1, file: candidates.length + 2 }[previous?.via]
      ?? candidates.findIndex(({ label }) => label === previous?.via);
    const ready = candidates.findIndex(({ usable }) => usable);
    const choice = await prompter.select(t('选择 OpenViking 连接', 'Choose an OpenViking connection'), [
      ...candidates.map((candidate) => ({ label: candidate.label,
        hint: describe(candidate, { preferredConfigPath, instanceId, taken }), value: candidate })),
      { label: t('火山引擎 OpenViking 托管服务', 'OpenViking Service on Volcengine'),
        hint: t('只需填写 API Key', 'API key only'), value: 'hosted' },
      { label: t('手动填写／修改地址和凭据', 'Enter or edit the address and credentials'), value: 'manual' },
      { label: t('指定 OV 配置文件', 'Choose an OV configuration file'), value: 'file' },
      { label: t('重新检测', 'Detect again'), value: 'retry' },
      { label: leaveTo ?? t('退出', 'Exit'), value: 'exit' },
    ], { back: canGoBack, initial: [before, ready].find((index) => index >= 0) ?? candidates.length });
    if (choice === 'exit') throw leaveTo ? new GoBack() : new PromptAborted();
    if (choice === 'retry') { candidates = await discoverOpenViking(options); continue; }
    try {
      let configPath = null;
      let config;
      let via = choice;
      if (choice === 'hosted') {
        draft = await hostedConfig(prompter, draft);
        config = draft;
      } else if (choice === 'manual') {
        draft = await manualConfig(prompter, draft);
        config = draft;
      } else if (choice === 'file') {
        const input = await prompter.text(t('OV 配置文件路径', 'OV configuration file path'), {
          initial: previous?.via === 'file' ? previous.configPath : undefined,
          validate: (value) => value ? null : t('请输入配置文件路径', 'Enter a configuration file path'),
        });
        configPath = path.resolve(input === '~' ? homeDir : input.replace(/^~(?=\/)/, homeDir));
        config = readOVConfig(configPath);
      } else {
        configPath = choice.configPath ?? null;
        config = sourceConfig(choice);
        via = choice.label;
      }
      const result = await detect({ env, homeDir, fetchImpl, config });
      if (!result?.usable) {
        prompter.warn(t(`OpenViking 验证失败：${result?.reason ?? '检查失败'}。请修改配置或重试。`,
          `OpenViking check failed: ${result?.reason ?? 'check failed'}. Edit the configuration or retry.`));
        continue;
      }
      prompter.success(t(`OpenViking 连接已验证：${address(result.url || config.url)}`, `OpenViking connection verified: ${address(result.url || config.url)}`));
      warnAboutLegacySoulExtraction(prompter, result.version);
      const identity = await chooseIdentity(prompter, { result, config, taken, instanceId });
      if (!identity) continue;
      return { ...result, configPath, config: keyIdentity(structuredClone(config), result), via, identity };
    } catch (error) {
      if (error instanceof PromptAborted) throw error;
      if (!(error instanceof GoBack)) prompter.warn(t('无法读取 OV 配置，请检查文件路径和 JSON 格式后重试。',
        'Cannot read the OV configuration; check the file path and JSON format, then retry.'));
    }
  }
}

// The connection offered to the next new instance. A file new instances find
// anyway is not copied: the copy would go stale and still be found first. An
// administrator's key that created the instance's user is not kept anywhere.
export function saveOVConnection(detected, { env = process.env, homeDir = env.HOME || os.homedir() } = {}) {
  const target = savedOVConfigPath(homeDir);
  const found = [(env.OPENVIKING_CLI_CONFIG_FILE || '').trim(), target, path.join(homeDir, '.openviking', 'ovcli.conf')];
  if (detected.identity?.mode === 'register') return null;
  if (detected.configPath && found.includes(detected.configPath)) return null;
  const config = { ...sourceConfig(detected) };
  for (const field of PEER_FIELDS) delete config[field];
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  const temporary = `${target}.${randomUUID()}.tmp`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600, flag: 'wx' });
    fs.renameSync(temporary, target);
  } finally { fs.rmSync(temporary, { force: true }); }
  return target;
}

// The instance's soul in OpenViking, read and written with the instance's own
// identity (its ovConfig). A missing soul reads as empty; a release that
// refuses `replace` on a missing file (404) gets `create`.
export async function readOVSoul({ configPath, uri, fetchImpl = globalThis.fetch }) {
  if (isLocalSoul(uri)) {
    try {
      return fs.readFileSync(uri, 'utf8');
    } catch (error) {
      if (error?.code === 'ENOENT') return '';
      throw error;
    }
  }
  const connection = new OVConnection(readOVConfig(configPath), { fetch: fetchImpl });
  try {
    const { result } = await connection.json(`/api/v1/content/read?uri=${encodeURIComponent(uri)}&offset=0&limit=-1&raw=true`);
    return String(result ?? '');
  } catch (error) {
    if (error?.httpStatus === 404) return '';
    throw error;
  }
}

export async function writeOVSoul({ configPath, uri, content, fetchImpl = globalThis.fetch }) {
  if (isLocalSoul(uri)) {
    fs.mkdirSync(path.dirname(uri), { recursive: true, mode: 0o700 });
    fs.writeFileSync(uri, content, { mode: 0o600 });
    return uri;
  }
  const connection = new OVConnection(readOVConfig(configPath), { fetch: fetchImpl });
  const write = (mode) => connection.json('/api/v1/content/write', { uri, content, mode, wait: true });
  try {
    await write('replace');
  } catch (error) {
    if (error?.httpStatus !== 404) throw error;
    await write('create');
  }
  return uri;
}

// A changed connection gets a new private file. Existing files are left intact
// until the caller saves the manifest; cancelling only removes pending files.
export async function prepareInstanceOpenViking({
  agent, detected, pending, env, homeDir, fetchImpl, detect = detectOpenViking, reserved = new Set(), registrations,
}) {
  if (agent.ovConfig && detected.configPath === agent.ovConfig && detected.identity?.mode !== 'register') return agent.ovConfig;
  const target = availableInstanceOvConfig(env, agent.id, reserved);
  const config = await instanceOvConfig({ detected, instanceId: agent.id, fetchImpl, registrations });
  writeInstanceOvConfig({ config, target, exclusive: true });
  pending.add(target);
  const checked = await detect({ env, homeDir, fetchImpl, configPath: target });
  if (!checked?.usable) throw new Error(t(`OpenViking（${agent.id}）不可用：${checked?.reason ?? '检查失败'}`,
    `OpenViking (${agent.id}) is unavailable: ${checked?.reason ?? 'check failed'}`));
  return target;
}
