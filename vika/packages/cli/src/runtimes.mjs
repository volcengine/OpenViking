// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFile as execFileCallback } from '../../daemon/src/platform/index.mjs';
import fs from 'node:fs';
import path from 'node:path';
import { promisify } from 'node:util';
import { assertPiVersion, managedPiBin, piCommand } from '../../daemon/src/pi-runtime.mjs';
import { assertSupportedOpencodeVersion } from '../../daemon/src/opencode-runtime.mjs';
import { runtimeBinKind, runtimeBinProblem } from '../../daemon/src/runtime-bin.mjs';
import { t } from './i18n.mjs';
import { probeAcp, probeAppServer } from './runtime-probe.mjs';

// Setup only checks the agent runtimes; installing and logging in to them is
// the user's business. Pi ships with Vika and reuses the user's ~/.pi/agent.

const execFile = promisify(execFileCallback);

export { findExecutable as which } from '../../daemon/src/platform/index.mjs';
import { findExecutable as which } from '../../daemon/src/platform/index.mjs';

async function run(execFileImpl, command, args, env) {
  try {
    const { stdout } = await execFileImpl(command, args, { env, timeout: 20_000 });
    return { ok: true, stdout: String(stdout) };
  } catch (error) {
    return { ok: false, stdout: String(error?.stdout ?? ''), error };
  }
}

function configured(value) {
  return typeof value === 'string' ? value.trim() : '';
}

// A configured *_BIN is authoritative: it names the file the daemon will run,
// so a broken value is reported as such instead of falling back to a different
// CLI found on PATH. The two accepted forms are owned by runtime-bin.mjs.
function configuredBin(value, env) {
  const name = configured(value);
  if (!name) return null;
  return runtimeBinKind(name) === 'name' ? which(name, env) : path.resolve(name);
}

// What detection probes: the configured binary when the key is set (even when
// it does not resolve, so the note can name it), otherwise the first of
// `fallback` on PATH. A value that cannot be used as written comes back as
// `problem` rather than being resolved against the caller's cwd.
function probeBin(key, env, ...fallback) {
  const value = configured(env[key]);
  if (value) {
    const problem = runtimeBinProblem(value, key);
    return { bin: problem ? null : configuredBin(value, env), problem, configured: value };
  }
  for (const name of fallback) {
    const found = which(name, env);
    if (found) return { bin: found, problem: null, configured: null };
  }
  return { bin: null, problem: null, configured: null };
}

// execFile rejects with a string code when the binary itself could not run
// (ENOENT, EACCES, ENOEXEC) and with a numeric exit code when it ran and
// failed; only the former says anything about the path.
function binaryProblem(result) {
  const code = result?.error?.code;
  if (typeof code !== 'string') return null;
  return code === 'ENOENT' ? 'missing' : 'unusable';
}

function binaryProblemNote(problem, bin) {
  return problem === 'missing'
    ? t(`找不到可执行文件：${bin}`, `Executable not found: ${bin}`)
    : t(`无法执行：${bin}`, `Cannot execute: ${bin}`);
}

function missingNote(bin) {
  return binaryProblemNote('missing', bin);
}

// Nothing configured and nothing on PATH is "not installed"; a configured value
// that does not resolve is named, because the user has to fix that value.
function absentNote(probe) {
  return probe.configured ? missingNote(probe.configured) : t('未安装', 'not installed');
}

// [{ id, label, model, bin, binEnv, available, note }], in the order setup offers them.
export async function detectRuntimes({
  env = process.env,
  execFileImpl = execFile,
  piBin = managedPiBin(),
  probe = probeAppServer,
  acpProbe = probeAcp,
} = {}) {
  const runtimes = [];

  const codex = probeBin('CODEX_BIN', env, 'codex');
  const codexLogin = codex.bin ? await run(execFileImpl, codex.bin, ['login', 'status'], env) : null;
  const codexFailure = binaryProblem(codexLogin);
  runtimes.push({
    id: 'codex', label: 'Codex', model: 'codex:', bin: codex.bin, binEnv: 'CODEX_BIN',
    available: Boolean(codexLogin?.ok),
    note: codex.problem ?? (!codex.bin ? absentNote(codex)
      : codexFailure ? binaryProblemNote(codexFailure, codex.bin)
        : codexLogin.ok ? t('已登录', 'logged in')
          : t('未登录：先运行 codex login', 'not logged in: run codex login first')),
  });

  const claude = probeBin('CLAUDE_BIN', env, 'claude');
  const claudeStatus = claude.bin ? await run(execFileImpl, claude.bin, ['auth', 'status'], env) : null;
  let claudeLoggedIn = false;
  try { claudeLoggedIn = JSON.parse(claudeStatus?.stdout || '{}').loggedIn === true; } catch {}
  const claudeFailure = binaryProblem(claudeStatus);
  runtimes.push({
    id: 'claude', label: 'Claude Code', model: 'claude:', bin: claude.bin, binEnv: 'CLAUDE_BIN',
    available: claudeLoggedIn,
    note: claude.problem ?? (!claude.bin ? absentNote(claude)
      : claudeFailure ? binaryProblemNote(claudeFailure, claude.bin)
        : claudeLoggedIn ? t('已登录', 'logged in')
          : t('未登录：先运行 claude auth login', 'not logged in: run claude auth login first')),
  });

  // An explicit PI_BIN is what the daemon runs; without one, the bundled entry
  // the caller passed (or the release's own) is probed. A bare name goes
  // through PATH, exactly as piCommand resolves it for the daemon.
  const piConfigured = configured(env.PI_BIN);
  const piProblem = runtimeBinProblem(piConfigured, 'PI_BIN');
  const piEntry = piProblem ? null : (piConfigured ? configuredBin(piConfigured, env) : piBin);
  let models = [];
  let piProbeResult = null;
  let piVersionProblem = null;
  if (piEntry) {
    // The bundled entry is a Node script this process runs itself, exactly as
    // the daemon does; an explicit PI_BIN is run as given (piCommand).
    const command = piConfigured
      ? piCommand(piEntry, ['--list-models'])
      : { command: process.execPath, args: [piEntry, '--list-models'] };
    piProbeResult = await run(execFileImpl, command.command, command.args, { ...env, PI_OFFLINE: '1' });
    models = piProbeResult.stdout.split('\n').slice(1).map((line) => line.trim().split(/\s+/))
      .filter((columns) => columns.length >= 2).map(([provider, model]) => `${provider}/${model}`);
    // The daemon's preflight rejects a version this release was not built for;
    // probing it here keeps setup from offering a Pi that cannot start.
    if (piConfigured && !binaryProblem(piProbeResult)) {
      const version = piCommand(piEntry, ['--version']);
      const versionResult = await run(execFileImpl, version.command, version.args, env);
      const versionFailure = binaryProblem(versionResult);
      if (versionFailure) piVersionProblem = binaryProblemNote(versionFailure, piEntry);
      else {
        try { assertPiVersion(versionResult.stdout); } catch (error) { piVersionProblem = error.message; }
      }
    }
  }
  const piFailure = binaryProblem(piProbeResult);
  runtimes.push({
    id: 'pi', label: t('Pi（内置）', 'Pi (built in)'), model: 'pi',
    // Only a configured entry is pinned; the bundled one is not written back.
    bin: piConfigured && !piProblem ? piEntry : null, binEnv: 'PI_BIN',
    available: models.length > 0 && !piVersionProblem,
    note: piProblem ?? piVersionProblem ?? (!piEntry ? absentNote({ configured: piConfigured })
      : piFailure ? binaryProblemNote(piFailure, piEntry)
        : models.length > 0 ? t(`用 ~/.pi/agent 里的 ${models[0]}`, `uses ${models[0]} from ~/.pi/agent`)
          : t('没有可用模型：运行 vika pi 后 /login，或编辑 ~/.pi/agent/models.json', 'no model yet: run vika pi and /login, or edit ~/.pi/agent/models.json')),
  });

  const durable = await detectDurable({ env, execFileImpl });
  if (durable) runtimes.push(durable);

  const traex = await detectTraex({ env, execFileImpl, probe });
  if (traex) runtimes.push(traex);
  const opencode = await detectOpencode({ env, execFileImpl, probe: acpProbe });
  if (opencode) runtimes.push(opencode);
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
  const binKey = env.TRAEX_BIN?.trim() ? 'TRAEX_BIN' : 'TRAECLI_BIN';
  const problem = explicit ? runtimeBinProblem(explicit, binKey) : null;
  if (problem) {
    return {
      id: 'traex', label: 'TraeX', model: 'traex', bin: null, binEnv: 'TRAECLI_BIN',
      installed: false, protocolCompatible: false, loggedIn: false, available: false,
      note: problem,
    };
  }
  const candidates = explicit ? [explicit] : TRAEX_COMMANDS.map((name) => which(name, env)).filter(Boolean);
  const seen = new Set();
  let best = null;
  for (const candidate of candidates) {
    const located = path.isAbsolute(candidate) || candidate.includes(path.sep) ? path.resolve(candidate) : which(candidate, env);
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
        result.note = login.ok ? t('协议兼容，已登录', 'Protocol compatible, logged in')
          : t(`协议兼容，但登录检查未通过：运行 ${bin} login`, `Protocol compatible, but login check failed: run ${bin} login`);
      }
    }
    if (result.available) return result;
    if (!best || result.protocolCompatible) best = result;
  }
  return best;
}

// OpenCode runs over ACP; an explicit OPENCODE_BIN is authoritative. ACP alone
// does not prove the user has a model credential, so an initialize handshake
// plus a supported version is what setup verifies (no session, no model call).
async function detectOpencode({ env, execFileImpl, probe }) {
  const explicit = env.OPENCODE_BIN?.trim();
  const problem = explicit ? runtimeBinProblem(explicit, 'OPENCODE_BIN') : null;
  if (problem) {
    return {
      id: 'opencode', label: 'OpenCode', model: 'opencode', bin: null, binEnv: 'OPENCODE_BIN',
      installed: false, available: false, note: problem,
    };
  }
  const bin = explicit ? configuredBin(explicit, env) : which('opencode', env);
  if (!bin) {
    return {
      id: 'opencode', label: 'OpenCode', model: 'opencode', bin: null, binEnv: 'OPENCODE_BIN',
      installed: false, available: false, note: t('未安装', 'not installed'),
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
  result.note = t(`协议兼容（${version.raw}），使用 opencode 自己的模型和登录配置`,
    `ACP compatible (${version.raw}), uses OpenCode's own model and login`);
  return result;
}
