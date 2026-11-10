#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// A fake `dsh` executable for tests. It speaks the same LF-framed JSON-RPC
// stdio contract as the real process and answers the bridge v2 methods a
// control client uses (`vika/hello`, `vika/settings/*`, `vika/credentials/*`,
// `vika/llm/models`, `vika/llm/model/info`), persisting state into the same
// two files the real settings and credentials services write:
//
//   <DSH_HOME>/profiles/sdk/cordis.patch.yml   the llm-pi-ai providers
//   <DSH_HOME>/.credentials.yaml               the stored references
//
// The patch document is JSON: JSON is a YAML subset, which is the same
// convention `buildDshPatch` already uses for vika's own patch files. The
// credentials document is written in the real service's shape so a test reads
// the same `version`/`refs` layout the product produces.
//
// Environment:
//   DSH_HOME                 required (or derivable from `--patch <dataDir>/dsh/run/…`)
//   FAKE_DSH_VERSION         `--version` output (default 0.2.2-rc.1)
//   FAKE_DSH_LOG             append JSONL of spawn/request/response events here
//   FAKE_DSH_COLD_REFUSALS   answer this many service calls with -32002 first
//   FAKE_DSH_DEBUG           print diagnostics to stderr
//
// This is a fixture, not a DSH substitute: it does not run a model turn and it
// implements exactly the methods vika's control client depends on.

import fs from 'node:fs';
import path from 'node:path';

const VERSION = process.env.FAKE_DSH_VERSION || '0.2.2-rc.1';
const CREDENTIAL_REF = /^[A-Za-z_][A-Za-z0-9_]*$/;
const NOT_READY_CODE = -32002;
const METHODS = Object.freeze([
  'session/steer', 'session/cancel',
  'vika/llm/models', 'vika/llm/model/info', 'vika/session/model/select',
  'vika/settings/describe', 'vika/settings/mutate',
  'vika/credentials/set', 'vika/credentials/unset',
]);
const SETTINGS_NS = 'llm-pi-ai';
// Methods that resolve a DSH service, so they can be refused before the boot
// completes exactly like the real bridge's NOT_READY answer.
const SERVICE_METHODS = new Set([
  'vika/llm/models', 'vika/llm/model/info', 'vika/session/model/select',
  'vika/settings/describe', 'vika/settings/mutate',
  'vika/credentials/set', 'vika/credentials/unset',
]);
const PROVIDER_ENTRY_ID = 'llm-pi-ai';
const PROVIDER_ENTRY_NAME = '@deepseek-ai/dsh-llm-pi-ai';
const PATCH_RELATIVE = path.join('profiles', 'sdk', 'cordis.patch.yml');
const CREDENTIALS_FILE = '.credentials.yaml';

function debug(...args) {
  if (process.env.FAKE_DSH_DEBUG) process.stderr.write(`[fake-dsh] ${args.join(' ')}\n`);
}

function parseArgs(argv) {
  const options = { profile: null, patch: null };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--profile') options.profile = argv[index + 1] ?? null;
    else if (arg === '--patch') options.patch = argv[index + 1] ?? null;
  }
  return options;
}

// The control patch lives at <dataDir>/dsh/run/control-patch.yml, so the home
// directory is <dataDir>/dsh/home — the same layout sanitizeDshEnv creates.
function deriveDshHome(patchPath, env = process.env) {
  if (typeof env.DSH_HOME === 'string' && env.DSH_HOME.trim()) return path.resolve(env.DSH_HOME);
  if (patchPath) {
    const runDirectory = path.dirname(path.resolve(patchPath));
    const dshDirectory = path.dirname(runDirectory);
    return path.join(dshDirectory, 'home');
  }
  // Neither input exists, so there is no per-test directory to write into. A
  // shared path here would let two suites running in parallel (node --test runs
  // files concurrently) write each other's settings document and credentials,
  // and the failure would look like a flaky test rather than a misconfigured
  // fixture. Refuse instead.
  throw new Error('fake dsh: neither DSH_HOME nor --patch names a data directory');
}

function readJsonFile(filePath, fallback) {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')); } catch { return fallback; }
}

function writeFilePrivate(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
  const temporary = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, content, { encoding: 'utf8', mode: 0o600 });
  fs.chmodSync(temporary, 0o600);
  fs.renameSync(temporary, filePath);
}

function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function scalar(pathSegment) {
  return /^(?:0|[1-9][0-9]*)$/.test(pathSegment) ? Number(pathSegment) : pathSegment;
}

/**
 * Apply one settings path op to a detached section, mirroring the settings
 * service: `set` creates missing containers, `unset` removes the member (and
 * splices array indices).
 */
function applyPathOp(section, op) {
  const pathSegments = op?.path;
  if (!Array.isArray(pathSegments) || pathSegments.length === 0
    || pathSegments.some(segment => typeof segment !== 'string' || segment.length === 0)) {
    const error = new Error('ops[].path must be a non-empty array of non-empty strings');
    error.code = -32602;
    throw error;
  }
  let node = section;
  for (let index = 0; index < pathSegments.length - 1; index += 1) {
    const key = scalar(pathSegments[index]);
    const next = scalar(pathSegments[index + 1]);
    if (Array.isArray(node)) {
      if (typeof key !== 'number' || key < 0 || key > node.length) throw new Error(`Config array index "${key}" is out of range`);
      if (node[key] === undefined || typeof node[key] !== 'object' || node[key] === null) {
        node[key] = typeof next === 'number' ? [] : {};
      }
    } else if (!node[key] || typeof node[key] !== 'object') {
      node[key] = typeof next === 'number' ? [] : {};
    }
    node = node[key];
  }
  const last = scalar(pathSegments[pathSegments.length - 1]);
  if (op.op === 'unset') {
    if (Array.isArray(node)) {
      if (typeof last !== 'number' || last >= node.length) throw new Error(`Config array index "${last}" is out of range`);
      node.splice(last, 1);
    } else {
      delete node[last];
    }
    return;
  }
  if (op.op !== 'set') throw new Error(`unsupported settings op "${op.op}"`);
  if (Array.isArray(node)) {
    if (typeof last !== 'number' || last > node.length) throw new Error(`Config array index "${last}" is out of range`);
    if (last === node.length) node.push(clone(op.value));
    else node[last] = clone(op.value);
    return;
  }
  node[last] = clone(op.value);
}

// The bridge's wire taxonomy (bridge v2.1): a numeric JSON-RPC code stays on
// the frame, and a typed failure class rides in `data.code`. A fake that
// answered bare -32603 would let a caller pass by matching message text.
//
// This mirrors `bridgeErrorFrame` in src/dsh-bridge/handlers.mjs. It cannot
// import it: this file is copied into a throwaway bin directory and executed as
// a stand-alone `dsh`, so a relative specifier would not resolve. The mirror is
// pinned by dsh-bridge.test.mjs, which feeds the same failures through both and
// fails if they disagree.
const SETTINGS_MESSAGE_CODES = Object.freeze([
  [/overridden by a home patch/i, 'SETTINGS_OVERRIDDEN'],
  [
    /no configurable plugin entry|no longer configurable|is not configurable|is no longer available|is no longer active|no volatile fields/i,
    'SETTINGS_NOT_CONFIGURABLE',
  ],
  [/changed since it was read|changed during reload/i, 'SETTINGS_CONFLICT'],
]);

export function settingsErrorCode(error, message) {
  for (const [pattern, code] of SETTINGS_MESSAGE_CODES) {
    if (pattern.test(message)) return code;
  }
  return typeof error?.code === 'string' && error.code ? error.code : 'SETTINGS_INVALID';
}

export function bridgeErrorFrame(error, method) {
  const message = error instanceof Error ? error.message : String(error);
  if (Number.isSafeInteger(error?.code)) {
    return { code: error.code, message, ...(error?.data === undefined ? {} : { data: error.data }) };
  }
  const stringCode = typeof error?.code === 'string' && error.code ? error.code : undefined;
  const mapped = method.startsWith('vika/settings/') ? settingsErrorCode(error, message) : stringCode;
  const detail = error?.data;
  const data = detail !== null && typeof detail === 'object' && !Array.isArray(detail) ? { ...detail } : {};
  if (mapped !== undefined) data.code = mapped;
  if (mapped === 'SETTINGS_CONFLICT') {
    if (Number.isSafeInteger(error?.expected)) data.expected = error.expected;
    if (Number.isSafeInteger(error?.actual)) data.actual = error.actual;
  }
  if (detail !== undefined && (detail === null || typeof detail !== 'object' || Array.isArray(detail))) {
    data.detail = detail;
  }
  return { code: -32603, message, ...(Object.keys(data).length === 0 ? {} : { data }) };
}

function parseCredentials(text) {
  const refs = new Map();
  for (const line of String(text ?? '').split('\n')) {
    const match = /^ {2}([A-Za-z_][A-Za-z0-9_]*): (.*)$/.exec(line.replace(/\r$/, ''));
    if (!match) continue;
    let value = match[2];
    if (value.startsWith('"')) {
      try { value = JSON.parse(value); } catch { continue; }
    }
    refs.set(match[1], String(value));
  }
  return refs;
}

function renderCredentials(refs) {
  const lines = ['version: 1'];
  if (refs.size === 0) return `${lines.join('\n')}\nrefs: {}\n`;
  lines.push('refs:');
  for (const [ref, value] of [...refs].sort(([left], [right]) => left.localeCompare(right))) {
    const rendered = /^[A-Za-z0-9._/@+-]+$/.test(value) ? value : JSON.stringify(value);
    lines.push(`  ${ref}: ${rendered}`);
  }
  return `${lines.join('\n')}\n`;
}

function main() {
  const argv = process.argv.slice(2);
  if (argv.includes('--version') || argv.includes('-v')) {
    process.stdout.write(`${VERSION}\n`);
    return;
  }
  const options = parseArgs(argv);
  const dshHome = deriveDshHome(options.patch);
  const patchPath = path.join(dshHome, PATCH_RELATIVE);
  const credentialsPath = path.join(dshHome, CREDENTIALS_FILE);
  const logPath = process.env.FAKE_DSH_LOG || '';
  let coldRefusals = Math.max(0, Number.parseInt(process.env.FAKE_DSH_COLD_REFUSALS || '0', 10) || 0);

  // A hand-edited document the settings service cannot parse is the state
  // `vika dsh model repair` exists to fix, so the fake reproduces it: every
  // settings call fails until something rewrites the file.
  const patchText = fs.existsSync(patchPath) ? fs.readFileSync(patchPath, 'utf8') : null;
  const stored = readJsonFile(patchPath, null);
  const brokenDocument = patchText !== null && stored === null;
  const entry = Array.isArray(stored) ? stored.find(row => row?.id === PROVIDER_ENTRY_ID) : null;
  let providers = clone(entry?.config?.providers) ?? {};
  let refs = parseCredentials(fs.existsSync(credentialsPath) ? fs.readFileSync(credentialsPath, 'utf8') : '');
  // A persisted document means a previous run wrote settings; starting at
  // revision 1 makes an edit to a non-empty document a change, not a no-op.
  let revision = Object.keys(providers).length > 0 ? 1 : 0;

  function log(event) {
    if (!logPath) return;
    try {
      fs.mkdirSync(path.dirname(logPath), { recursive: true });
      fs.appendFileSync(logPath, `${JSON.stringify({ ts: Date.now(), ...event })}\n`);
    } catch (error) {
      debug('log failed', error.message);
    }
  }

  function writeProviders() {
    const rows = Array.isArray(stored) ? clone(stored) : [];
    const index = rows.findIndex(row => row?.id === PROVIDER_ENTRY_ID);
    const next = { id: PROVIDER_ENTRY_ID, name: PROVIDER_ENTRY_NAME, config: { providers: clone(providers) } };
    if (index < 0) rows.push(next);
    else rows[index] = next;
    writeFilePrivate(patchPath, `${JSON.stringify(rows, null, 2)}\n`);
  }

  function writeCredentials() {
    writeFilePrivate(credentialsPath, renderCredentials(refs));
  }

  function credentialPresent(ref) {
    if (refs.has(ref)) return true;
    return typeof process.env[ref] === 'string' && process.env[ref].length > 0;
  }

  const builtInModels = (provider) => {
    if (provider === 'deepseek-account') return [];
    if (typeof process.env.DEEPSEEK_API_KEY === 'string' && process.env.DEEPSEEK_API_KEY.length > 0) {
      return [
        { id: 'deepseek-flash', name: 'DeepSeek-V4.1-Flash', inputModalities: ['text', 'image'] },
        { id: 'deepseek-v4-pro', name: 'DeepSeek-V4-Pro', inputModalities: ['text'] },
      ];
    }
    return [];
  };

  function presentModels(profile) {
    if (!Array.isArray(profile?.models)) return [];
    const ref = typeof profile.apiKeyEnv === 'string' ? profile.apiKeyEnv : '';
    if (ref && !credentialPresent(ref)) return [];
    return profile.models.map(model => ({
      id: model.id,
      name: model.name ?? model.id,
      inputModalities: Array.isArray(model.input) ? [...model.input] : null,
    }));
  }

  function descriptor() {
    return {
      autoGenerate: false,
      ns: SETTINGS_NS,
      schema: { uid: 1, refs: {} },
      revision,
      applies: 'live',
      value: { providers: clone(providers) },
      base: { providers: {} },
      user: { providers: clone(providers) },
      secrets: [],
    };
  }

  function modelInfo(provider, model) {
    const profile = providers?.[provider];
    const listed = profile ? (profile.models ?? []).find(entryModel => entryModel.id === model) : undefined;
    if (profile && listed) {
      const efforts = listed.reasoningEfforts && typeof listed.reasoningEfforts === 'object'
        ? Object.keys(listed.reasoningEfforts) : [];
      return {
        provider,
        id: listed.id,
        name: listed.name ?? listed.id,
        inputModalities: Array.isArray(listed.input) ? [...listed.input] : ['text'],
        context: { contextWindow: listed.contextWindow ?? profile.defaultContextWindow ?? 1_000_000 },
        defaultMaxTokens: listed.maxTokens ?? profile.defaultMaxTokens ?? 128_000,
        ...(efforts.length === 0 ? {} : {
          reasoning: {
            efforts: efforts.map(id => ({ id, name: id.charAt(0).toUpperCase() + id.slice(1) })),
            defaultEffort: efforts.includes('high') ? 'high' : efforts[0],
          },
        }),
      };
    }
    if (provider === 'deepseek-official' && builtInModels(provider).some(entryModel => entryModel.id === model)) {
      return {
        provider, id: model, name: model, inputModalities: ['text'],
        context: { contextWindow: 1_048_576 }, defaultMaxTokens: 256_000,
        reasoning: {
          efforts: ['off', 'low', 'high', 'max'].map(id => ({ id, name: id })),
          defaultEffort: 'high',
        },
      };
    }
    const error = new Error(`no adapter registered for provider "${provider}"`);
    error.code = 'NO_ADAPTER';
    return { error };
  }

  function assertDocumentReadable() {
    if (!brokenDocument) return;
    throw new Error('llm-pi-ai is not readable: the profile patch is malformed');
  }

  const handlers = {
    initialize: () => ({ serverInfo: { name: 'fake-dsh', version: VERSION } }),
    'vika/hello': () => ({ bridge: 'vika-dsh-bridge', version: 2, methods: [...METHODS] }),
    'vika/settings/describe': (params) => {
      const ns = typeof params.ns === 'string' && params.ns.trim() ? params.ns.trim() : SETTINGS_NS;
      if (ns !== SETTINGS_NS) throw invalid(`unsupported settings namespace: ${ns}`);
      assertDocumentReadable();
      return { ns, descriptor: descriptor() };
    },
    'vika/settings/mutate': (params) => {
      const ns = typeof params.ns === 'string' && params.ns.trim() ? params.ns.trim() : SETTINGS_NS;
      if (ns !== SETTINGS_NS) throw invalid(`unsupported settings namespace: ${ns}`);
      if (!Array.isArray(params.ops) || params.ops.length === 0) throw invalid('ops must be a non-empty array');
      assertDocumentReadable();
      if (params.expectedRevision !== undefined && params.expectedRevision !== revision) {
        throw Object.assign(
          new Error(`settings namespace "${ns}" changed since it was read (expected revision ${params.expectedRevision}, now ${revision})`),
          { expected: params.expectedRevision, actual: revision },
        );
      }
      // Schema refusals the settings service performs: the protocol is a closed
      // set and a route needs one, so a caller cannot persist an unservable
      // profile.
      for (const op of params.ops) {
        const value = op?.value;
        if (op?.op === 'set' && Array.isArray(op.path) && op.path[0] === 'providers'
          && value && typeof value === 'object' && value.api !== undefined
          && !['openai-completions', 'openai-responses', 'anthropic-messages'].includes(value.api)) {
          throw new Error(`$.providers.${op.path[1]}.api expected "openai-completions" | "openai-responses" | "anthropic-messages" but got "${value.api}"`);
        }
      }
      let next = { providers: clone(providers) };
      for (const op of params.ops) applyPathOp(next, op);
      providers = next.providers ?? {};
      revision += 1;
      writeProviders();
      return { ok: true };
    },
    'vika/credentials/set': (params) => {
      const ref = typeof params.ref === 'string' ? params.ref.trim() : '';
      if (!CREDENTIAL_REF.test(ref)) throw invalid('ref must be a POSIX shell identifier');
      if (typeof params.value !== 'string' || params.value.length === 0) {
        throw invalid('value must be a non-empty string; use unset to remove one');
      }
      refs.set(ref, params.value);
      writeCredentials();
      return { ok: true };
    },
    'vika/credentials/unset': (params) => {
      const ref = typeof params.ref === 'string' ? params.ref.trim() : '';
      if (!CREDENTIAL_REF.test(ref)) throw invalid('ref must be a POSIX shell identifier');
      refs.delete(ref);
      writeCredentials();
      return { ok: true };
    },
    'vika/llm/models': () => ({
      providers: [
        { provider: 'deepseek-official', name: 'DeepSeek', models: builtInModels('deepseek-official'), error: null, credential: null },
        { provider: 'deepseek-account', name: 'DeepSeek Account', models: [], error: null, credential: null },
        ...Object.entries(providers).map(([route, profile]) => ({
          provider: route,
          name: profile?.displayName ?? route,
          models: presentModels(profile),
          error: null,
          // The bridge's three-state block: `known` says whether the
          // credentials service answered at all.
          credential: typeof profile?.apiKeyEnv === 'string'
            ? { ref: profile.apiKeyEnv, present: credentialPresent(profile.apiKeyEnv), known: true }
            : null,
        })),
      ],
    }),
    'vika/llm/model/info': (params) => {
      const provider = typeof params.provider === 'string' ? params.provider.trim() : '';
      const model = typeof params.model === 'string' ? params.model.trim() : '';
      if (!provider || !model) throw invalid('provider and model must be non-empty strings');
      const result = modelInfo(provider, model);
      if (result.error) throw result.error;
      return result;
    },
    'vika/session/model/select': (params) => {
      if (typeof params.sessionId !== 'string' || !params.sessionId) throw invalid('sessionId must be a non-empty string');
      const provider = typeof params.provider === 'string' ? params.provider.trim() : '';
      const model = typeof params.model === 'string' ? params.model.trim() : '';
      if (!provider || !model) throw invalid('provider and model must be non-empty strings');
      return { provider, model, reasoningEffort: params.reasoningEffort ?? null };
    },
  };

  function invalid(message) {
    const error = new Error(message);
    error.code = -32602;
    return error;
  }

  function redact(method, params) {
    if (method === 'vika/credentials/set') {
      return { ref: params.ref, valueLength: typeof params.value === 'string' ? params.value.length : 0 };
    }
    return params;
  }

  function respond(frame) {
    const id = frame.id;
    const method = frame.method;
    const params = frame.params && typeof frame.params === 'object' && !Array.isArray(frame.params) ? frame.params : {};
    log({ event: 'request', method, params: redact(method, params) });
    if (coldRefusals > 0 && SERVICE_METHODS.has(method)) {
      coldRefusals -= 1;
      const error = { code: NOT_READY_CODE, message: 'credentials is not ready', data: { code: 'NOT_READY', service: 'credentials' } };
      log({ event: 'response', method, ok: false, code: NOT_READY_CODE });
      write({ jsonrpc: '2.0', id, error });
      return;
    }
    if (method === 'shutdown') {
      log({ event: 'response', method, ok: true });
      write({ jsonrpc: '2.0', id, result: { ok: true } });
      process.exit(0);
    }
    const handler = handlers[method];
    if (!handler) {
      log({ event: 'response', method, ok: false, code: -32601 });
      write({ jsonrpc: '2.0', id, error: { code: -32601, message: `method not found: ${method}` } });
      return;
    }
    try {
      const result = handler(params);
      log({ event: 'response', method, ok: true });
      write({ jsonrpc: '2.0', id, result: result === undefined ? null : result });
    } catch (error) {
      const frame = bridgeErrorFrame(error, method);
      log({ event: 'response', method, ok: false, code: frame.code, errorCode: frame.data?.code });
      write({ jsonrpc: '2.0', id, error: frame });
    }
  }

  function write(frame) {
    process.stdout.write(`${JSON.stringify(frame)}\n`);
  }

  let buffer = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => {
    buffer += chunk;
    let index = buffer.indexOf('\n');
    while (index >= 0) {
      const line = buffer.slice(0, index).replace(/\r$/, '');
      buffer = buffer.slice(index + 1);
      index = buffer.indexOf('\n');
      if (!line.trim()) continue;
      let frame;
      try { frame = JSON.parse(line); } catch {
        debug('ignoring malformed frame');
        continue;
      }
      if (!frame || typeof frame !== 'object' || typeof frame.method !== 'string') continue;
      if (frame.id === undefined) continue; // notification
      try { respond(frame); } catch (error) { debug('respond failed', error.message); }
    }
  });
  process.stdin.on('end', () => process.exit(0));
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
    process.on(signal, () => process.exit(0));
  }

  log({
    event: 'spawn', argv, cwd: process.cwd(), dshHome, patchPath, credentialsPath,
    controlPatch: options.patch, version: VERSION,
    controlPatchExists: options.patch ? fs.existsSync(options.patch) : false,
  });
}

main();
