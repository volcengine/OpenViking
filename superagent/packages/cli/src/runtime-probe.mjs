// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { spawn, spawnDetached } from '../../daemon/src/platform/index.mjs';
import { sanitizeAgentChildEnv, terminateProcessTree } from '../../daemon/src/process-utils.mjs';
import { captureStartupStderr } from '../../daemon/src/runtime-diagnostics.mjs';
import { safeError } from '../../daemon/src/logger.mjs';
import { agyHandshakeProblem } from '../../daemon/src/agy-runtime.mjs';
import { writeControlPatch } from '../../daemon/src/dsh-control.mjs';
import {
  DSH_BRIDGE_DAEMON_METHODS, DSH_BRIDGE_VERSION,
  assertDshNodeVersion, bridgeHelloSupports, dshCommand, sanitizeDshEnv, writeDshPatch,
} from '../../daemon/src/dsh-runtime.mjs';

// What a read-only probe needs the bridge to expose. The daemon method set is
// what `vika setup` is verifying -- the runtime this install will drive -- plus
// `vika/settings/describe`, the one read that `vika dsh model list` and the
// models page both start from. It is checked on the handshake rather than
// exercised here, because this probe must not need a configured route.
// The credential write methods (`vika/credentials/*`) and `vika/settings/mutate`
// are deliberately NOT required: a bridge that can read the catalogue but not
// write a credential is still a working setup, and failing it there would report
// a broken install for a missing write feature.
const DSH_PROBE_METHODS = Object.freeze([
  ...DSH_BRIDGE_DAEMON_METHODS, 'vika/settings/describe',
]);

// Spawn `bin args`, send initialize (id 1), then either stop immediately or
// complete a short protocol-specific handshake. No session or model turn.
//
// `bridge` opts a probe into the DSH handshake: after initialize DSH must answer
// `vika/hello` (the bridge this release drives) and `vika/llm/models` (the model
// routes DSH itself can serve), then `shutdown`. A catalogue that answers an
// error is reported to `onModels` as `null`, never treated as a failed probe:
// the routes are DSH's own configuration, and this probe runs keyless.
function probeInitialize(bin, {
  args, label, request, onAccepted = () => {}, bridge = null, validate = () => '', env, spawnImpl, timeoutMs,
}) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawnImpl(bin, args, {
        env: sanitizeAgentChildEnv(env), shell: false,
        detached: spawnDetached, stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (error) {
      resolve({ compatible: false, reason: safeError(error).slice(0, 2000) });
      return;
    }
    const stderr = captureStartupStderr(child.stderr);
    let settled = false;
    let buffer = '';
    let received = 0;
    const timer = setTimeout(() => finish(false, `${label} ${bridge ? 'handshake' : 'initialize'} timed out`), timeoutMs);
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
    child.once('close', (code, signal) => finish(false, `${label} exited before ${bridge ? 'handshake' : 'initialize'} (code ${code}, signal ${signal})`));
    child.stdin?.on('error', (error) => finish(false, error.message));
    child.stdout?.setEncoding('utf8');
    child.stdout?.on('data', (chunk) => {
      if (settled) return;
      received += chunk.length;
      if (received > 65536) { finish(false, `${label} initialize response exceeded 64 KiB`); return; }
      buffer += chunk;
      let index;
      while (!settled && (index = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        let message;
        try { message = JSON.parse(line); } catch { continue; }
        if (bridge && message?.id === 2) {
          if (message.error || !bridgeHelloSupports(message.result, DSH_PROBE_METHODS)) {
            finish(false, `${label} bridge handshake failed (requires v${DSH_BRIDGE_VERSION})`);
          } else {
            bridge.stage = 'models';
            child.stdin.write('{"jsonrpc":"2.0","id":4,"method":"vika/llm/models","params":{}}\n');
          }
          continue;
        }
        if (bridge && message?.id === 4 && bridge.stage === 'models') {
          bridge.onModels(message.error ? null : message.result);
          bridge.stage = 'shutdown';
          child.stdin.write('{"jsonrpc":"2.0","id":3,"method":"shutdown","params":{}}\n');
          continue;
        }
        if (bridge && message?.id === 3 && bridge.stage === 'shutdown') {
          finish(Boolean(message.result && !message.error), `${label} shutdown failed`);
          continue;
        }
        if (message?.id !== 1) continue;
        const problem = message.error || !message.result || typeof message.result !== 'object' || Array.isArray(message.result)
          ? `${label} rejected initialize`
          : validate(message.result);
        if (problem) {
          finish(false, problem);
        } else {
          onAccepted(child);
          if (bridge) {
            bridge.stage = 'hello';
            child.stdin.write('{"jsonrpc":"2.0","id":2,"method":"vika/hello","params":{}}\n');
          } else finish(true);
        }
      }
    });
    child.stdin.write(`${JSON.stringify({ id: 1, method: 'initialize', ...request })}\n`);
  });
}

// Codex/TraeX app-server. An older TraeX can answer to the same command names
// but speaks ACP, not this protocol.
export function probeAppServer(bin, { env = process.env, spawnImpl = spawn, timeoutMs = 5000 } = {}) {
  return probeInitialize(bin, {
    args: ['app-server', '--listen', 'stdio://'],
    label: 'app-server',
    request: { params: { clientInfo: { name: 'vika-setup', version: '1' }, capabilities: { experimentalApi: true } } },
    onAccepted: (child) => child.stdin.write(`${JSON.stringify({ method: 'initialized', params: {} })}\n`),
    env, spawnImpl, timeoutMs,
  });
}

// The built-in route initialize probes. It is the one route a keyless process
// always has, and it is what DSH falls back on when a wanted route is unknown.
// Model ids may themselves contain slashes, so provider and model stay separate.
export const DSH_PROBE_ROUTE = Object.freeze({ provider: 'deepseek-official', model: 'deepseek-flash' });

// One provider entry of `vika/llm/models`, reduced to what a probe reports.
function catalogueRoute(route) {
  if (!route || typeof route !== 'object' || typeof route.provider !== 'string' || !route.provider) return null;
  return {
    provider: route.provider,
    models: Array.isArray(route.models)
      ? route.models.map(model => (typeof model?.id === 'string' ? model.id : '')).filter(Boolean)
      : [],
    credential: route.credential?.present === true,
    error: route.error && typeof route.error === 'object' ? String(route.error.message ?? route.error.code ?? '') : '',
  };
}

/**
 * Every route a `vika/llm/models` reply reported, reduced to what a caller can
 * check without the raw reply: the provider, its model ids, whether a
 * credential resolved, and how the catalogue lookup failed.
 * @param {object|null} reply - the `vika/llm/models` result.
 * @returns {Array<{provider: string, models: string[], credential: boolean, error: string}>}
 */
export function dshCatalogueRoutes(reply) {
  if (!reply || typeof reply !== 'object' || !Array.isArray(reply.providers)) return [];
  return reply.providers.map(catalogueRoute).filter(Boolean);
}

/**
 * The model route a DSH process reports for itself.
 * The catalogue is the only source: `models.json` is gone, and a route that
 * serves no model cannot run a turn. A route whose credential is present wins
 * over one that only answers because DSH discovered a key elsewhere.
 * @param {object|null} reply - the `vika/llm/models` result.
 * @returns {{provider: string, model: string, id: string}|null}
 */
export function dshCatalogueRoute(reply) {
  const routes = dshCatalogueRoutes(reply).filter(route => route.models.length > 0);
  if (routes.length === 0) return null;
  const route = routes.find(entry => entry.credential) ?? routes[0];
  const model = route.models[0];
  return { provider: route.provider, model, id: `${route.provider}/${model}` };
}

// Why the catalogue named no route. An unreachable endpoint, a missing
// credential and an unconfigured home read the same from the outside, so the
// providers DSH did report are named instead of guessed at.
function catalogueNote(reply) {
  if (!reply || typeof reply !== 'object' || !Array.isArray(reply.providers)) {
    return 'DSH reported no model catalogue';
  }
  const routes = dshCatalogueRoutes(reply);
  if (routes.length === 0) return 'DSH reported no model route';
  return routes
    .map((route) => {
      const count = `${route.models.length} model${route.models.length === 1 ? '' : 's'}`;
      return route.error ? `${route.provider}: ${count} (${route.error})` : `${route.provider}: ${count}`;
    })
    .join('; ');
}

// DSH's probe does not create a session or call a model. Its temporary data
// directory carries the same private patch as the daemon, without actions.sock
// and without any provider section: routes live in DSH's own settings document
// (`<DSH_HOME>/profiles/sdk/cordis.patch.yml`), which a fresh temporary home
// does not have. Nothing here needs a real credential: initialize answers
// without one, and `vika/llm/models` then says which routes this DSH can serve.
// A key the caller does have is passed through, so a built-in route that really
// resolves models is reported as such; no placeholder is invented, because that
// would make the built-in route look servable on an unconfigured machine.
//
// `dataDir` probes one existing home instead of a throwaway one: setup uses it
// on an instance that already exists, because that instance's routes live there
// (the same home `vika dsh model list` starts a control process against). The
// directory belongs to the caller then -- nothing is removed, and the probe
// starts from the same separate `control-patch.yml` the control client writes,
// never from the daemon's `run/patch.yml`, so a live instance's launch patch is
// not overwritten by a read-only check.
export async function probeDsh(bin, { env = process.env, spawnImpl = spawn, timeoutMs = 30_000, dataDir = '' } = {}) {
  const ownHome = !dataDir;
  let directory = dataDir;
  let catalogue = null;
  try {
    assertDshNodeVersion();
    if (ownHome) {
      directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-dsh-probe-'));
      fs.chmodSync(directory, 0o700);
    }
    const patchPath = ownHome
      ? writeDshPatch({ dataDir: directory, policy: 'Vika setup bridge probe', mcp: null })
      : writeControlPatch({ dataDir: directory, policy: 'Vika setup bridge probe' });
    const command = dshCommand({ bin, patchPath });
    const result = await probeInitialize(bin, {
      args: command.args, label: 'DSH',
      request: {
        jsonrpc: '2.0',
        params: { cwd: directory, ...DSH_PROBE_ROUTE, reasoningEffort: 'high', approvalRequests: true },
      },
      bridge: { onModels: (reply) => { catalogue = reply; } },
      env: sanitizeDshEnv({ ...env }, { dataDir: directory }),
      spawnImpl, timeoutMs,
    });
    const route = dshCatalogueRoute(catalogue);
    return {
      ...result,
      route: route?.id ?? '',
      // Falls back to the initialize route: that is the route this probe did
      // verify DSH accepts, so the caller still has something to report.
      fallbackRoute: route ? '' : `${DSH_PROBE_ROUTE.provider}/${DSH_PROBE_ROUTE.model}`,
      note: route ? '' : catalogueNote(catalogue),
      // Every route the catalogue reported, for a caller that has to check one
      // specific route (setup validating a draft) rather than "some route".
      routes: dshCatalogueRoutes(catalogue),
    };
  } catch (error) {
    return { compatible: false, reason: safeError(error).slice(0, 2000), route: '', fallbackRoute: '', note: '', routes: [] };
  } finally {
    // Only a home this call made is removed; an instance's own home is the
    // caller's and must survive the probe.
    if (ownHome && directory) fs.rmSync(directory, { recursive: true, force: true });
  }
}

const ACP_INITIALIZE = Object.freeze({ jsonrpc: '2.0', params: {
  protocolVersion: 1,
  clientCapabilities: { fs: { readTextFile: false, writeTextFile: false }, terminal: false },
  clientInfo: { name: 'vika-setup', version: '1' },
} });

// OpenCode ACP. OpenCode 1 and 2 both answer, so version gating happens
// separately.
export function probeAcp(bin, { env = process.env, spawnImpl = spawn, timeoutMs = 8000 } = {}) {
  return probeInitialize(bin, { args: ['acp'], label: 'ACP', request: ACP_INITIALIZE, env, spawnImpl, timeoutMs });
}

// Antigravity ACP. The handshake needs no login; `agy models` proves that.
export function probeAgyAcp(bin, { env = process.env, spawnImpl = spawn, timeoutMs = 8000 } = {}) {
  return probeInitialize(bin, {
    args: ['acp'], label: 'ACP', request: ACP_INITIALIZE,
    validate: agyHandshakeProblem,
    env, spawnImpl, timeoutMs,
  });
}
