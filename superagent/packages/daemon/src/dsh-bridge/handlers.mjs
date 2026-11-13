// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

export const BRIDGE_ID = 'superagent-dsh-bridge';
export const BRIDGE_VERSION = 2;

// The only settings namespace OpenViking SuperAgent may write. The models page owns the same
// namespace, so a narrower bridge surface keeps both on one document.
const SETTINGS_NS = 'llm-pi-ai';

// A single provider query may hit the network when the route follows an
// endpoint listing (deepseek-official does by default). The list is best-effort
// per route, so one hung provider must not hold the whole answer.
const MODELS_ROUTE_TIMEOUT_MS = 10_000;
// One model's capability metadata is a local lookup in DSH, but the adapter
// behind it is not: a route whose endpoint never answers would otherwise leave
// this handler pending forever and write a late response onto the wire after the
// caller gave up. Same order of magnitude as one route of the list above.
const MODEL_INFO_TIMEOUT_MS = 10_000;

// Mirrors @deepseek-ai/dsh-credentials `REF_PATTERN` (credentialRef). The
// bridge validates before writing because credentials-local's own set() does
// not, and an invalid reference would corrupt the document on its next read.
const CREDENTIAL_REF = /^[A-Za-z_][A-Za-z0-9_]*$/;

// JSON-RPC -32002: the requested service is not mounted yet. The bridge is
// wired before some DSH services finish activating (credentials observed
// ~1s after the first request), so callers retry this code only.
export const NOT_READY_CODE = -32002;

export class DshBridgeError extends Error {
  constructor(code, message, data = undefined) {
    super(message);
    this.code = code;
    if (data !== undefined) this.data = data;
  }
}

function invalid(message) {
  return new DshBridgeError(-32602, message);
}

function notReady(service) {
  return new DshBridgeError(NOT_READY_CODE, `${service} is not ready`, { code: 'NOT_READY', service });
}

// DSH reports settings failures two ways: plain `Error`s whose only signal is
// the message (overridden patch, non-configurable entry, schema refusal), and
// `SettingsConflictError`, which carries a string code plus expected/actual.
// Both fold into one closed code surface so callers route on `data.code` and
// never on message text (design §4.2.1).
const SETTINGS_METHOD_PREFIX = 'superagent/settings/';

const SETTINGS_MESSAGE_CODES = Object.freeze([
  [/overridden by a home patch/i, 'SETTINGS_OVERRIDDEN'],
  [
    /no configurable plugin entry|no longer configurable|is not configurable|is no longer available|is no longer active|no volatile fields/i,
    'SETTINGS_NOT_CONFIGURABLE',
  ],
  [/changed since it was read|changed during reload/i, 'SETTINGS_CONFLICT'],
]);

function settingsErrorCode(error, message) {
  for (const [pattern, code] of SETTINGS_MESSAGE_CODES) {
    if (pattern.test(message)) return code;
  }
  // An already machine-routable code is better reported as-is than folded into
  // SETTINGS_INVALID; the remainder — notably schema refusals — carries none.
  return typeof error?.code === 'string' && error.code ? error.code : 'SETTINGS_INVALID';
}

/**
 * Project a thrown value onto a JSON-RPC error object. Numeric JSON-RPC codes
 * pass through untouched, with their structured detail preserved. DSH's own
 * errors carry a string code (`HarnessError`/`LlmError`, `SettingsConflictError`)
 * that is not a JSON-RPC code: the frame keeps -32603 and moves the code into
 * `data.code`, so the failure class survives the wire and callers stay off
 * message matching.
 * @param error - the thrown value.
 * @param method - the handler that failed; settings failures are translated to
 *   the closed SETTINGS_* surface only under `superagent/settings/*`.
 * @returns a JSON-RPC error object.
 */
export function bridgeErrorFrame(error, method = '') {
  const message = error instanceof Error ? error.message : String(error);
  if (Number.isSafeInteger(error?.code)) {
    return { code: error.code, message, ...(error?.data === undefined ? {} : { data: error.data }) };
  }
  const stringCode = typeof error?.code === 'string' && error.code ? error.code : undefined;
  const mapped = method.startsWith(SETTINGS_METHOD_PREFIX) ? settingsErrorCode(error, message) : stringCode;
  // The classified code is authoritative over one already sitting in `data`:
  // DSH's own contract is to route on `Error.code`, and this is the single place
  // that decides the wire taxonomy.
  const detail = error?.data;
  const data = detail !== null && typeof detail === 'object' && !Array.isArray(detail) ? { ...detail } : {};
  if (mapped !== undefined) data.code = mapped;
  // Re-reading the form and retrying is the caller's only recovery, so carry
  // both revisions that make the retry one round trip shorter.
  if (mapped === 'SETTINGS_CONFLICT') {
    if (Number.isSafeInteger(error?.expected)) data.expected = error.expected;
    if (Number.isSafeInteger(error?.actual)) data.actual = error.actual;
  }
  // A non-object detail cannot be merged with the code, so it is kept beside it
  // rather than silently dropped.
  if (detail !== undefined && (detail === null || typeof detail !== 'object' || Array.isArray(detail))) {
    data.detail = detail;
  }
  return { code: -32603, message, ...(Object.keys(data).length === 0 ? {} : { data }) };
}

function requireService(ctx, name) {
  const service = ctx?.get?.(name);
  if (!service) throw notReady(name);
  return service;
}

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function sessionAgent(agents, sessionId) {
  if (typeof sessionId !== 'string' || !sessionId) throw invalid('sessionId must be a non-empty string');
  const agent = agents.get(sessionId);
  if (!agent) throw invalid(`session is not loaded: ${sessionId}`);
  return agent;
}

// Model selection belongs to a root session. Children spawned by the delegating
// agent share the process, and steering one onto another route would leave the
// parent's turn on a route its own selection no longer names.
function rootSessionAgent(agents, sessionId) {
  const agent = sessionAgent(agents, sessionId);
  if (agent?.session?.header?.origin === 'subagent') {
    throw invalid(`session is a subagent, not a root session: ${sessionId}`);
  }
  return agent;
}

function errorPayload(error) {
  return {
    code: error?.code === undefined ? 'BRIDGE_ERROR' : String(error.code),
    message: error instanceof Error ? error.message : String(error),
  };
}

// Race a provider lookup against a local bound. The underlying call accepts no
// signal, so it keeps running; the handled branch above the race keeps its late
// rejection from surfacing as an unhandled one.
function withTimeout(promise, ms, label) {
  const guarded = Promise.resolve(promise);
  guarded.catch(() => {});
  let timer;
  const expired = new Promise((_resolve, reject) => {
    timer = setTimeout(() => {
      const error = new Error(`${label} timed out after ${ms}ms`);
      error.code = 'BRIDGE_TIMEOUT';
      reject(error);
    }, ms);
    timer.unref?.();
  });
  return Promise.race([guarded, expired]).finally(() => clearTimeout(timer));
}

/**
 * Configured credential reference per provider route. Read from the same
 * settings document the Models page writes, so the bridge reports what the
 * adapter will actually resolve instead of guessing from environment names.
 * @param settings - the `settings` service, when it has activated.
 * @returns route id → `apiKeyEnv` reference.
 */
function credentialRefs(settings) {
  const refs = new Map();
  if (!settings) return refs;
  try {
    const descriptor = settings.describe({ redactSecrets: true }).find(row => row.ns === SETTINGS_NS);
    const providers = descriptor?.value?.providers;
    if (!providers || typeof providers !== 'object') return refs;
    for (const [route, profile] of Object.entries(providers)) {
      const ref = profile?.apiKeyEnv;
      if (typeof ref === 'string' && ref) refs.set(route, ref);
    }
  } catch {
    // Credential enrichment is advisory; a settings failure must not blank the
    // model list.
  }
  return refs;
}

/**
 * Three-state credential state for one reference. A `null` presence alone could
 * not distinguish "the credentials service has not activated yet" from "this
 * route has no credential", and the catalogue is exactly what explains a route
 * whose models are hidden; so the read never fails the list and instead says
 * whether the answer is known.
 * @param credentials - the `credentials` service, when it has activated.
 * @param ref - the route's `apiKeyEnv` reference.
 * @returns `{present, known}`; `known:false` means retry the field later.
 */
async function credentialPresence(credentials, ref) {
  if (!credentials) return { present: null, known: false };
  try {
    const info = await credentials.describe(ref);
    return { present: info?.configured === true, known: true };
  } catch {
    return { present: null, known: false };
  }
}

/**
 * Bridge request handlers. Methods are the whole routing surface: `superagent/hello`
 * advertises every key except itself, so the advertised list cannot drift from
 * what the bridge actually answers.
 * @param options.ctx - DSH plugin context; services are resolved per request so
 *   a handler installed before a service activates still answers once it does.
 * @param options.agents - the agent registry (`ctx.agents`).
 * @param options.createUserMessage - DSH's user-message factory.
 */
export function createBridgeHandlers({
  ctx = {}, agents, createUserMessage,
  modelsTimeoutMs = MODELS_ROUTE_TIMEOUT_MS,
  modelInfoTimeoutMs = MODEL_INFO_TIMEOUT_MS,
  workerHandlers = null,
}) {
  const handlers = {
    'session/steer'(params) {
      const agent = sessionAgent(agents, params.sessionId);
      if (!Array.isArray(params.contentBlocks) || params.contentBlocks.length === 0) {
        throw invalid('session/steer requires a non-empty contentBlocks array');
      }
      if (params.contentBlocks.some(block => block?.type !== 'text' || typeof block.text !== 'string')) {
        throw invalid('session/steer accepts text content blocks only');
      }
      const message = createUserMessage({ content: params.contentBlocks, source: { kind: 'user' } });
      agent.steer(message);
      return { messageId: message.id };
    },
    'session/cancel'(params) {
      const agent = sessionAgent(agents, params.sessionId);
      if (params.keepInbox !== undefined && typeof params.keepInbox !== 'boolean') {
        throw invalid('session/cancel keepInbox must be a boolean');
      }
      // 0.2.2-rc.1 declares `Agent.cancel(cause, options): void`
      // (@deepseek-ai/dsh-agent lib/types/runtime-types.d.ts), so there is no
      // result to forward; `cancelled` means "was work live when called", which
      // is the `AgentStatus` value read before the call.
      const cancelled = agent.status === 'running';
      agent.cancel({ kind: 'user' }, { keepInbox: params.keepInbox ?? false });
      return { cancelled };
    },
    async 'superagent/llm/models'() {
      const llm = requireService(ctx, 'llm');
      const settings = ctx?.get?.('settings') ?? null;
      const credentials = ctx?.get?.('credentials') ?? null;
      const refs = credentialRefs(settings);
      // Routes are queried together: each carries its own bound, so N routes
      // cannot multiply into a wait longer than the caller's request timeout.
      return {
        providers: await Promise.all(llm.listProviders().map(async (route) => {
          const entry = {
            provider: route.id,
            name: route.name ?? route.id,
            models: [],
            error: null,
            credential: null,
          };
          const ref = refs.get(route.id);
          if (ref) entry.credential = { ref, ...(await credentialPresence(credentials, ref)) };
          try {
            const models = await withTimeout(
              llm.listModels(route.id), modelsTimeoutMs, `listModels(${route.id})`,
            );
            entry.models = models.map(model => ({
              id: model.id,
              name: model.name,
              inputModalities: model.inputModalities === undefined ? null : [...model.inputModalities],
            }));
          } catch (error) {
            entry.error = errorPayload(error);
          }
          return entry;
        })),
      };
    },
    async 'superagent/llm/model/info'(params) {
      const provider = text(params.provider);
      const model = text(params.model);
      if (!provider || !model) throw invalid('provider and model must be non-empty strings');
      const llm = requireService(ctx, 'llm');
      // Bounded locally: an adapter that never answers must fail this one
      // request instead of holding the caller until its own timeout, and no
      // response is written after the caller stopped waiting.
      return withTimeout(
        llm.resolveModelInfo(provider, model), modelInfoTimeoutMs, `resolveModelInfo(${provider}/${model})`,
      );
    },
    async 'superagent/session/model/select'(params) {
      const agent = rootSessionAgent(agents, params.sessionId);
      const provider = text(params.provider);
      const model = text(params.model);
      if (!provider || !model) throw invalid('provider and model must be non-empty strings');
      const effort = params.reasoningEffort;
      if (effort !== undefined && (typeof effort !== 'string' || effort.length === 0)) {
        throw invalid('reasoningEffort must be a non-empty string');
      }
      if (typeof agent?.model?.select !== 'function') {
        throw new DshBridgeError(-32603, 'session does not expose model selection');
      }
      const llm = requireService(ctx, 'llm');
      // Validation and recording are separate: resolveCallConfig materializes
      // adapter defaults (an effort, maxTokens), and recording those would pin a
      // default as the caller's choice — the selection DSH stores must be the
      // request (core/agent/src/model-selection.ts, runtime-types.ts `select`).
      const selection = { provider, model, ...(effort === undefined ? {} : { reasoningEffort: effort }) };
      await llm.resolveCallConfig(selection);
      agent.model.select(selection);
      return { provider, model, reasoningEffort: effort ?? null };
    },
    'superagent/settings/describe'(params) {
      const ns = text(params.ns) || SETTINGS_NS;
      if (ns !== SETTINGS_NS) throw invalid(`unsupported settings namespace: ${ns}`);
      const settings = requireService(ctx, 'settings');
      const descriptor = settings.describe({ redactSecrets: true }).find(row => row.ns === ns);
      return { ns, descriptor: descriptor ?? null };
    },
    async 'superagent/settings/mutate'(params) {
      const ns = text(params.ns) || SETTINGS_NS;
      if (ns !== SETTINGS_NS) throw invalid(`unsupported settings namespace: ${ns}`);
      if (!Array.isArray(params.ops) || params.ops.length === 0) {
        throw invalid('ops must be a non-empty array');
      }
      const settings = requireService(ctx, 'settings');
      await settings.mutate(ns, params.ops, params.expectedRevision);
      return { ok: true };
    },
    async 'superagent/credentials/set'(params) {
      const ref = text(params.ref);
      if (!CREDENTIAL_REF.test(ref)) throw invalid('ref must be a POSIX shell identifier');
      if (typeof params.value !== 'string' || params.value.length === 0) {
        throw invalid('value must be a non-empty string; use unset to remove one');
      }
      const credentials = requireService(ctx, 'credentials');
      // Never echo the value: this error surface reaches logs and chat.
      await credentials.set(ref, params.value);
      return { ok: true };
    },
    async 'superagent/credentials/unset'(params) {
      const ref = text(params.ref);
      if (!CREDENTIAL_REF.test(ref)) throw invalid('ref must be a POSIX shell identifier');
      const credentials = requireService(ctx, 'credentials');
      await credentials.unset(ref);
      return { ok: true };
    },
  };

  // Worker methods share the same advertisement/routing table as the stock
  // bridge methods. They are still session adapters, not daemon task APIs.
  if (workerHandlers) Object.assign(handlers, workerHandlers);
  // Installed last so the advertised method list is the handlers themselves.
  handlers['superagent/hello'] = () => ({
    bridge: BRIDGE_ID,
    version: BRIDGE_VERSION,
    methods: Object.keys(handlers).filter(name => name !== 'superagent/hello'),
  });
  return handlers;
}
