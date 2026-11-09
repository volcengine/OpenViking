// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFile as execFileCallback } from '../../daemon/src/platform/index.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { safeError } from '../../daemon/src/logger.mjs';
import { prepareAgyGeminiDir } from '../../daemon/src/ov-isolation.mjs';
import { managedPiBin } from '../../daemon/src/pi-runtime.mjs';
import { assertSupportedOpencodeVersion } from '../../daemon/src/opencode-runtime.mjs';
import { DSH_DEFAULT_MODEL, assertSupportedDshVersion } from '../../daemon/src/dsh-runtime.mjs';
import { t } from './i18n.mjs';
import { probeAcp, probeAgyAcp, probeAppServer, probeDsh } from './runtime-probe.mjs';

// Setup only checks the agent runtimes; installing and logging in to them is
// the user's business. Pi ships with Vika and reuses the user's ~/.pi/agent.

const execFile = promisify(execFileCallback);

export { findExecutable as which } from '../../daemon/src/platform/index.mjs';
import { findExecutable as which } from '../../daemon/src/platform/index.mjs';

// A configured `*_BIN` is either an absolute path or a bare command name that
// the shell would find on PATH. `path.resolve('dsh')` is `$CWD/dsh`: resolving
// a bare name skipped the PATH search and pinned a path that usually does not
// exist, so a working install was reported as broken (and pinned into the
// manifest). A value carrying a separator stays a path, as before.
function locateBinary(candidate, env) {
  return path.isAbsolute(candidate) || candidate.includes(path.sep)
    ? path.resolve(candidate)
    : which(candidate, env);
}

async function run(execFileImpl, command, args, env) {
  try {
    const { stdout } = await execFileImpl(command, args, { env, timeout: 20_000 });
    return { ok: true, stdout: String(stdout) };
  } catch (error) {
    return { ok: false, stdout: String(error?.stdout ?? ''), stderr: String(error?.stderr ?? '') };
  }
}

// [{ id, label, model, bin, binEnv, available, note }], in the order setup offers them.
// [{ id, label, model, bin, binEnv, available, note }], in the order setup offers them.
export async function detectRuntimes({
  env = process.env,
  execFileImpl = execFile,
  piBin = managedPiBin(),
  probe = probeAppServer,
  acpProbe = probeAcp,
  agyProbe = probeAgyAcp,
  dshProbe = probeDsh,
} = {}) {
  const runtimes = [];

  const codex = which('codex', env);
  const codexLogin = codex ? await run(execFileImpl, codex, ['login', 'status'], env) : null;
  runtimes.push({
    id: 'codex', label: 'Codex', model: 'codex:', bin: codex, binEnv: 'CODEX_BIN',
    available: Boolean(codexLogin?.ok),
    note: !codex ? t('未安装', 'not installed') : codexLogin.ok ? t('已登录', 'logged in') : t('未登录：先运行 codex login', 'not logged in: run codex login first'),
  });

  const claude = which('claude', env);
  const claudeStatus = claude ? await run(execFileImpl, claude, ['auth', 'status'], env) : null;
  let claudeLoggedIn = false;
  try { claudeLoggedIn = JSON.parse(claudeStatus?.stdout || '{}').loggedIn === true; } catch {}
  runtimes.push({
    id: 'claude', label: 'Claude Code', model: 'claude:', bin: claude, binEnv: 'CLAUDE_BIN',
    available: claudeLoggedIn,
    note: !claude ? t('未安装', 'not installed') : claudeLoggedIn ? t('已登录', 'logged in') : t('未登录：先运行 claude auth login', 'not logged in: run claude auth login first'),
  });

  const piModels = await run(execFileImpl, process.execPath, [piBin, '--list-models'], { ...env, PI_OFFLINE: '1' });
  const models = piModels.stdout.split('\n').slice(1).map((line) => line.trim().split(/\s+/))
    .filter((columns) => columns.length >= 2).map(([provider, model]) => `${provider}/${model}`);
  runtimes.push({
    id: 'pi', label: t('Pi（内置）', 'Pi (built in)'), model: 'pi', bin: null, binEnv: null,
    available: models.length > 0,
    note: models.length > 0 ? t(`用 ~/.pi/agent 里的 ${models[0]}`, `uses ${models[0]} from ~/.pi/agent`)
      : t('没有可用模型：运行 vika pi 后 /login，或编辑 ~/.pi/agent/models.json', 'no model yet: run vika pi and /login, or edit ~/.pi/agent/models.json'),
  });

  const durable = await detectDurable({ env, execFileImpl });
  if (durable) runtimes.push(durable);

  const traex = await detectTraex({ env, execFileImpl, probe });
  if (traex) runtimes.push(traex);
  const opencode = await detectOpencode({ env, execFileImpl, probe: acpProbe });
  if (opencode) runtimes.push(opencode);
  runtimes.push(await detectAgy({ env, execFileImpl, probe: agyProbe }));
  const dsh = await detectDsh({ env, execFileImpl, probe: dshProbe });
  runtimes.push(dsh);
  return runtimes;
}

// The durable runtime ships with Vika (like Pi) and talks to provider APIs
// directly, so availability is "the bundled host loads and one provider has
// credentials", not a login on PATH. It reads the same models as the host:
// pi-ai's built-in providers plus Pi's ~/.pi/agent, Pi's default model first.
const DURABLE_PROBE = `
import { availableChatModels, loadDurableModels } from ${JSON.stringify(new URL('../../daemon/src/durable-models.mjs', import.meta.url).href)};
const { models } = await loadDurableModels();
const chat = await availableChatModels(models);
process.stdout.write(JSON.stringify(chat.map((model) => \`\${model.provider}/\${model.id}\`)));
`;

// The daemon may get provider keys from ~/.vika/secrets.env (see
// deployment-config.cjs); the setup probe must see the same credentials or it
// would offer a runtime that only looks unusable here.
const DURABLE_PROVIDER_KEYS = [
  'ANTHROPIC_API_KEY', 'ARK_API_KEY', 'DEEPSEEK_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_GENERATIVE_AI_API_KEY',
  'GROQ_API_KEY', 'MISTRAL_API_KEY', 'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'XAI_API_KEY',
];

function providerSecrets(env) {
  const file = String(env.VIKA_SECRETS_FILE || '').trim()
    || (env.HOME ? path.join(env.HOME, '.vika', 'secrets.env') : '');
  if (!file) return {};
  let raw = '';
  try { raw = fs.readFileSync(file, 'utf8'); } catch { return {}; }
  const found = {};
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    if (DURABLE_PROVIDER_KEYS.includes(key)) found[key] = trimmed.slice(eq + 1).trim();
  }
  return found;
}

async function detectDurable({ env, execFileImpl }) {
  const secrets = providerSecrets(env);
  const probe = await run(execFileImpl, process.execPath, ['--input-type=module', '-e', DURABLE_PROBE], {
    ...env,
    ...Object.fromEntries(Object.entries(secrets).filter(([key]) => !env[key])),
    PI_OFFLINE: '1',
  });
  let models = [];
  if (probe.ok) {
    try { models = JSON.parse(probe.stdout || '[]'); } catch { models = []; }
  }
  return {
    id: 'durable', label: t('Durable（内置）', 'Durable (built in)'), model: 'durable', bin: null, binEnv: null,
    available: models.length > 0,
    note: models.length > 0 ? t(`自托管 durable harness，用 ${models[0]}（和 Pi 共用 ~/.pi/agent 的模型）`, `self-hosted durable harness, uses ${models[0]} (models shared with Pi in ~/.pi/agent)`)
      : t('有可用模型才可选：运行 vika pi 后 /login 或编辑 ~/.pi/agent/models.json，或配置 OPENAI_API_KEY 等 provider 密钥',
        'needs a model: run vika pi and /login, edit ~/.pi/agent/models.json, or set a provider key such as OPENAI_API_KEY'),
  };
}

// TraeX installs under any of these names; the first one that passes is used.
const TRAEX_COMMANDS = ['traex', 'traecli', 'trae-cli'];

async function detectTraex({ env, execFileImpl, probe }) {
  // A configured binary is authoritative. Do not silently replace it with an
  // unrelated login/configuration from PATH when it fails verification.
  const explicit = env.TRAEX_BIN?.trim() || env.TRAECLI_BIN?.trim();
  const candidates = explicit ? [explicit] : TRAEX_COMMANDS.map((name) => which(name, env)).filter(Boolean);
  const seen = new Set();
  let best = null;
  for (const candidate of candidates) {
    const located = locateBinary(candidate, env);
    let bin = located || candidate;
    let installed = false;
    try {
      if (located) {
        fs.accessSync(located, fs.constants.X_OK);
        installed = fs.statSync(located).isFile();
        // Keep the resolved candidate path as the pinned binary; only follow a
        // symlink so aliases that point at the same real file deduplicate.
        // /var -> /private/var on macOS must not rewrite a plain file path.
        if (installed && fs.lstatSync(located).isSymbolicLink()) {
          bin = fs.realpathSync(located);
        }
      }
    } catch {}
    if (seen.has(bin)) continue;
    seen.add(bin);
    const result = {
      id: 'traex', label: 'TraeX', model: 'traex', bin, binEnv: 'TRAECLI_BIN',
      installed, protocolCompatible: false, loggedIn: false, available: false,
      note: t(`找不到可执行文件：${candidate}`, `Executable not found: ${candidate}`),
    };
    if (installed) {
      const protocol = await probe(bin, { env });
      result.protocolCompatible = protocol.compatible;
      if (!protocol.compatible) {
        result.note = t(`不支持 Vika 所需的 app-server 握手：${bin}；请升级到支持 app-server 的 TraeX`,
          `Required app-server handshake failed: ${bin}; update to a TraeX with app-server support`);
        if (protocol.reason) result.note += ` (${protocol.reason})`;
      } else {
        const login = await run(execFileImpl, bin, ['login', 'status'], env);
        result.loggedIn = login.ok;
        result.available = login.ok;
        result.note = login.ok ? t('protocol 兼容，已登录', 'Protocol compatible, logged in')
          : t(`protocol 兼容，但登录检查未通过：运行 ${bin} login`, `Protocol compatible, but login check failed: run ${bin} login`);
      }
    }
    if (result.available) return result;
    if (!best || result.protocolCompatible) best = result;
  }
  return best;
}

// DSH owns model execution; Vika supplies the bridge and personal_agent MCP.
// Never reuse the daemon's real DSH_HOME here: the probe runs in a private
// temporary home, and model routes are DSH's own settings document there.
//
// `dataDir` probes one instance's own home instead. Setup uses it when the
// operator edits an instance that already exists: that instance's routes live
// in its home, and a throwaway home can never see them, so a configured
// instance would otherwise read as "no route".
export async function detectDsh({ env = process.env, execFileImpl = execFile, probe = probeDsh, dataDir = '' } = {}) {
  const explicit = env.DSH_BIN?.trim();
  const bin = explicit ? locateBinary(explicit, env) : which('dsh', env);
  const result = {
    id: 'dsh', label: 'DeepSeek Harness', model: 'dsh', bin, binEnv: 'DSH_BIN',
    installed: Boolean(bin), available: false, note: '', route: '', fallbackRoute: '',
  };
  if (!bin) {
    result.note = explicit
      ? t(`找不到可执行文件：${explicit}（既不是绝对路径，也不在 PATH 里）`,
        `Cannot find the executable ${explicit}: it is neither an absolute path nor on PATH`)
      : t('未安装：需要 dsh 0.2.x 和 Node 24.9+', 'Not installed: requires dsh 0.2.x and Node 24.9+');
    return result;
  }
  const version = await run(execFileImpl, bin, ['--version'], env);
  if (!version.ok) {
    result.note = t(`无法执行：${bin}`, `Cannot execute: ${bin}`);
    return result;
  }
  try { assertSupportedDshVersion(version.stdout); } catch (error) { result.note = error.message; return result; }
  const secrets = providerSecrets(env);
  const credential = env.DEEPSEEK_API_KEY || secrets.DEEPSEEK_API_KEY;
  const probeEnv = { ...env, ...secrets, ...(credential ? { DEEPSEEK_API_KEY: credential } : {}) };
  // Availability is the protocol: the binary answers, the bridge handshake
  // succeeds, and DSH can enumerate its own routes. Whether those routes carry
  // credentials is DSH's configuration (`vika dsh model list`), not something a
  // keyless probe can decide — so it is reported, never used to hide the runtime.
  const handshake = await probe(bin, { env: probeEnv, ...(dataDir ? { dataDir } : {}) });
  result.route = handshake.route ?? '';
  result.fallbackRoute = handshake.fallbackRoute ?? '';
  if (!handshake.compatible) {
    result.note = t(`SDK bridge 握手失败：${handshake.reason}`, `SDK bridge handshake failed: ${handshake.reason}`);
    return result;
  }
  result.available = true;
  const via = handshake.route || handshake.fallbackRoute || DSH_DEFAULT_MODEL;
  if (handshake.route) {
    result.note = t(`SDK bridge 兼容（${version.stdout.trim()}），model route ${via}`,
      `SDK bridge compatible (${version.stdout.trim()}), model route ${via}`);
    return result;
  }
  // No route served a model. This probe runs in a throwaway DSH home, so it can
  // never see the routes the model menu wrote into a real instance: say
  // what was actually verified (the protocol) instead of claiming the instance
  // has no model.
  const detail = handshake.note ? `（${handshake.note}）` : '';
  result.note = t(`SDK bridge 兼容（${version.stdout.trim()}）；model 目录由 DSH 自己管理（vika dsh model list），本机内置 route ${via} 需要 DEEPSEEK_API_KEY${detail}`,
    `SDK bridge compatible (${version.stdout.trim()}); model routes belong to DSH itself (vika dsh model list), and the built-in route ${via} needs DEEPSEEK_API_KEY${detail}`);
  return result;
}

// OpenCode runs over ACP; an explicit OPENCODE_BIN is authoritative. ACP alone
// does not prove the user has a model credential, so an initialize handshake
// plus a supported version is what setup verifies (no session, no model call).
async function detectOpencode({ env, execFileImpl, probe }) {
  const explicit = env.OPENCODE_BIN?.trim();
  const bin = explicit ? locateBinary(explicit, env) : which('opencode', env);
  if (!bin) {
    return {
      id: 'opencode', label: 'OpenCode', model: 'opencode', bin: null, binEnv: 'OPENCODE_BIN',
      installed: false, available: false,
      note: explicit
        ? t(`找不到可执行文件：${explicit}（既不是绝对路径，也不在 PATH 里）`,
          `Cannot find the executable ${explicit}: it is neither an absolute path nor on PATH`)
        : t('未安装', 'not installed'),
    };
  }
  const result = {
    id: 'opencode', label: 'OpenCode', model: 'opencode', bin, binEnv: 'OPENCODE_BIN',
    installed: true, available: false,
    note: '',
  };
  const output = await run(execFileImpl, bin, ['--version'], env);
  if (!output.ok) {
    result.note = t(`无法执行：${bin}`, `Cannot execute: ${bin}`);
    return result;
  }
  let version;
  try {
    version = assertSupportedOpencodeVersion(output.stdout);
  } catch (error) {
    result.note = error.message;
    return result;
  }
  const handshake = await probe(bin, { env });
  if (!handshake.compatible) {
    result.note = t(`ACP 握手失败：${handshake.reason}`, `ACP handshake failed: ${handshake.reason}`);
    return result;
  }
  // Models and credentials stay OpenCode's own (`opencode auth login`).
  result.available = true;
  result.note = t(`protocol 兼容（${version.raw}），使用 opencode 自己的模型和登录配置`,
    `ACP compatible (${version.raw}), uses OpenCode's own model and login`);
  return result;
}

// `agy models` starts every MCP server it finds, OpenViking's included, and
// unlike `agy acp` takes no --gemini-dir, so the same filtered config home
// reaches it through HOME. The token stays reachable through its links.
async function agyLoginCheck(execFileImpl, bin, env) {
  let isolated;
  try {
    isolated = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-agy-detect-'));
    prepareAgyGeminiDir(path.join(isolated, '.gemini'), env);
    return await run(execFileImpl, bin, ['models'], { ...env, HOME: isolated, USERPROFILE: isolated });
  } catch (error) {
    return { ok: false, stdout: '', stderr: safeError(error) };
  } finally {
    if (isolated) fs.rmSync(isolated, { recursive: true, force: true });
  }
}

// Antigravity runs over ACP; an explicit AGY_BIN is authoritative. Handshake
// before login: an agy that is not Antigravity cannot answer the login check
// meaningfully, and `agy models` exits non-zero when nobody is signed in.
async function detectAgy({ env, execFileImpl, probe }) {
  const explicit = env.AGY_BIN?.trim();
  const bin = explicit ? locateBinary(explicit, env) : which('agy', env);
  const result = {
    id: 'agy', label: 'Antigravity', model: 'agy', bin: bin ?? null, binEnv: 'AGY_BIN',
    installed: Boolean(bin), available: false, note: '',
  };
  if (!bin) {
    result.note = explicit
      ? t(`找不到可执行文件：${explicit}（既不是绝对路径，也不在 PATH 里）`,
        `Cannot find the executable ${explicit}: it is neither an absolute path nor on PATH`)
      : t('未安装', 'not installed');
    return result;
  }
  const version = await run(execFileImpl, bin, ['--version'], env);
  if (!version.ok) {
    result.note = t(`无法执行：${bin}`, `Cannot execute: ${bin}`);
    return result;
  }
  const handshake = await probe(bin, { env });
  if (!handshake.compatible) {
    result.note = t(`ACP 握手失败：${handshake.reason}`, `ACP handshake failed: ${handshake.reason}`);
    return result;
  }
  const login = await agyLoginCheck(execFileImpl, bin, env);
  if (!login.ok) {
    if (/sign in/i.test(login.stderr)) {
      result.note = t('未登录：先运行 agy 登录', 'not logged in: run agy and sign in first');
      return result;
    }
    const detail = login.stderr.trim().split('\n').at(-1) || `${bin} models`;
    result.note = t(`无法列出模型：${detail}`, `Could not list models: ${detail}`);
    return result;
  }
  result.available = true;
  result.note = t(`protocol 兼容（${version.stdout.trim()}），已登录`,
    `ACP compatible (${version.stdout.trim()}), logged in`);
  return result;
}
