// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The pure half of `vika dsh model`: what a route id, a model id and a
// credential reference may look like, which settings operations add or remove a
// model, and how a Pi `models.json` translates into native routes.
//
// Everything here is a function of its arguments. Nothing in this module spawns
// a process, writes a file or prompts: the one read it performs is `readPiAuth`,
// on a path its caller supplies. Both the command surface (dsh-model.mjs) and
// the writer (dsh-model-store.mjs) build on this file, so neither of them owns
// the rules the other has to obey.

import fs from 'node:fs';
import path from 'node:path';
import { t } from './i18n.mjs';
// `vika dsh model` only ever writes a DSH configuration, so it validates with
// the rule the DSH path of `/model` applies — the wider one, which admits the
// `+` a real dsh serves. Every other runtime keeps the narrower shared rule.
import { DSH_MODEL_ID_PATTERN } from '../../daemon/src/runtime-catalog.mjs';

// The wire protocols DSH's llm-pi-ai adapter serves.
export const PROTOCOLS = Object.freeze(['openai-completions', 'openai-responses', 'anthropic-messages']);
// The modalities a model profile may declare; anything else is dropped.
export const MODALITIES = Object.freeze(['text', 'image']);

// pi-ai's thinking levels in escalation order, and the five it offers without
// an explicit map. `xhigh`/`max` exist in the level list but a model must name
// their wire spelling, so a migration only carries the ones the source did.
const THINKING_LEVELS = Object.freeze(['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']);
const IMPLICIT_LEVELS = Object.freeze(['off', 'minimal', 'low', 'medium', 'high']);

// Same shape the bridge enforces for a credential reference.
const REF_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
// A route key is a settings dict key: one label, never a `provider/model` id.
const ROUTE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
// The model half of `<route>/<model>`. The grammar lives in runtime-catalog so
// the model wizard and `/model` cannot disagree about which ids are legal.

// Wire-compatibility switches a provider profile may state, by protocol. These
// are the fields the installed llm-pi-ai schema offers; a switch outside the
// protocol's list would refuse the whole write, so a migration drops it and
// says so instead of failing silently or refusing everything.
const COMPAT_FIELDS = Object.freeze({
  'openai-completions': Object.freeze([
    'supportsStore', 'supportsDeveloperRole', 'supportsReasoningEffort', 'supportsUsageInStreaming',
    'supportsFinishReason', 'maxTokensField', 'requiresToolResultName', 'requiresAssistantAfterToolResult',
    'requiresThinkingAsText', 'requiresReasoningContentOnAssistantMessages', 'thinkingFormat',
    'chatTemplateKwargs', 'chatTemplateArgs', 'supportsThinkingTokenBudget', 'thinkingTokenBudgetField',
    'vllmPriority', 'supportsStrictMode', 'cacheControlFormat', 'supportsLongCacheRetention',
  ]),
  'openai-responses': Object.freeze([
    'supportsDeveloperRole', 'supportsMaxOutputTokens', 'supportsStrictMode', 'supportsLongCacheRetention',
  ]),
  'anthropic-messages': Object.freeze([
    'supportsEagerToolInputStreaming', 'supportsLongCacheRetention', 'supportsCacheControlOnTools',
    'supportsTemperature', 'forceAdaptiveThinking', 'allowEmptySignature', 'supportsStrictTools',
  ]),
});
const COMPAT_ENUMS = Object.freeze({
  maxTokensField: Object.freeze(['max_completion_tokens', 'max_tokens']),
  thinkingFormat: Object.freeze([
    'openai', 'deepseek', 'openrouter', 'together', 'baseten', 'zai', 'qwen',
    'chat-template', 'qwen-chat-template', 'string-thinking', 'ant-ling',
  ]),
  thinkingTokenBudgetField: Object.freeze(['thinking_token_budget', 'thinking_budget']),
  cacheControlFormat: Object.freeze(['anthropic']),
});
const COMPAT_DICTS = Object.freeze(['chatTemplateKwargs', 'chatTemplateArgs']);
const CHAT_TEMPLATE_VARS = Object.freeze(['thinking.enabled', 'thinking.effort', 'thinking.budget']);
// Environment names the daemon withholds from a DSH child, so a reference to
// one of them can only resolve from DSH's credential service; a migration keeps
// the reference (its value may be stored later) and says so. The prefixes mirror
// `sanitizeAgentChildEnv`/`sanitizeNonArkChildEnv` in the daemon: ARK_* and
// VOLCENGINE_* are stripped for every child, LARK/FEISHU/LARKSUITE/VIKA with
// the connector settings, DSH_* by the DSH child environment itself.
//
// `SESSION_ID` is stripped by exact name, not by prefix (`sanitizeNonArkChildEnv`
// in process-utils.mjs), so it needs its own alternative: a prefix rule alone
// silently claimed the child would resolve it.
const WITHHELD_ENV = /^(?:(?:ARK|VOLCENGINE|LARK|FEISHU|LARKSUITE|VIKA|DSH)_|SESSION_ID$)/i;

export class UsageError extends Error {}

export function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

export function plainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

/**
 * The conventional credential reference of a route, as the DSH models page
 * derives it: `deepseek` → `DEEPSEEK_API_KEY`.
 *
 * This is only the *starting point*. The mapping is many-to-one — `foo-bar`,
 * `foo_bar` and `foo.bar` all collapse to `FOO_BAR_API_KEY` — so a caller must
 * never treat it as the assignment: `KeyRefAllocator.claim` is what decides.
 * @param {string} route - provider route id.
 * @returns {string} a POSIX shell identifier usable as a credential reference.
 */
export function deriveKeyRef(route) {
  const slug = String(route).toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  const derived = slug ? `${slug}_API_KEY` : 'ROUTE_API_KEY';
  return /^[A-Za-z_]/.test(derived) ? derived : `ROUTE_${derived}`;
}

/**
 * Allocate credential references without ever handing the same name to two
 * routes, and without ever writing a name somebody may already be using.
 *
 * The readable slug stays on its first owner, which keeps the common case
 * (`deepseek` → `DEEPSEEK_API_KEY`) exactly as documented. Every later claimant
 * gets the smallest free `_2`, `_3`, … suffix, and the note names the route
 * that already holds the name so the operator can see the disambiguation.
 *
 * Two entry points, because the two callers have different contracts:
 * `claim` is for a route that only *names* a reference (the add wizard), so a route
 * keeping its own reference is idempotent; `claimFresh` is for a reference a
 * value is *written* under (the migration), where the invariant is stricter —
 * see `claimFresh`.
 *
 * A reference is "taken" when a configured route resolves it or when the
 * credential store already holds a value under it. The second half is what
 * makes "this run created the reference" a fact instead of a guess: a name the
 * store already had is never adopted, so a failure path can delete exactly the
 * names it wrote and nothing else.
 */
class KeyRefAllocator {
  /**
   * @param {object|undefined} providers - the routes already configured, so a
   *   live reference is never handed to a different route.
   * @param {string[]} notes - the plan's note sink.
   * @param {object} [options] - `stored`, the references the credential store
   *   already holds a value under.
   */
  constructor(providers, notes, { stored = [] } = {}) {
    this.notes = notes;
    // reference → every route that resolves it. A shared reference has more
    // than one, which is exactly the state that must never be written.
    this.holders = new Map();
    for (const [route, profile] of Object.entries(plainObject(providers) ? providers : {})) {
      const ref = text(profile?.apiKeyEnv);
      if (ref) this.#hold(ref, route);
    }
    this.stored = new Set(stored);
  }

  /**
   * Record a reference a route names explicitly (`apiKey: "$NAME"`), so a
   * derived name can never be handed to a second route that would then
   * resolve the first route's secret.
   * @param {string} ref - the reference the document names.
   * @param {string} route - the route that names it.
   */
  reserve(ref, route) {
    if (!this.holders.has(ref)) this.#hold(ref, route);
  }

  /**
   * The routes that resolve a reference, so a message can name the owner.
   * @param {string} ref - the reference.
   * @returns {string[]} routes, in configuration order.
   */
  holdersOf(ref) {
    return this.holders.get(ref) ?? [];
  }

  /** Whether writing this reference would change what a configured route resolves. */
  isLive(ref) {
    return this.holdersOf(ref).length > 0;
  }

  /** Whether this reference already exists at all: a route resolves it, or a value is stored. */
  isTaken(ref) {
    return this.isLive(ref) || this.stored.has(ref);
  }

  /**
   * The reference a route may merely *name*, allocating a fresh one when its
   * conventional name belongs to somebody else. The route's own reference is
   * not a collision with itself, so repeating the write is idempotent.
   * @param {string} route - the route being described.
   * @returns {string} a reference no other route claims.
   */
  claim(route) {
    const preferred = deriveKeyRef(route);
    const holders = this.holdersOf(preferred);
    if (holders.length === 0 || (holders.length === 1 && holders[0] === route)) {
      this.#hold(preferred, route);
      return preferred;
    }
    const chosen = this.#freeName(preferred);
    this.notes.push(t(
      `${route}：credential reference ${preferred} 已被 ${holders.join('、')} 使用，改用 ${chosen}`,
      `${route}: the reference ${preferred} is already used by ${holders.join(', ')}; using ${chosen} instead`));
    this.#hold(chosen, route);
    return chosen;
  }

  /**
   * The reference an imported literal key is stored under.
   *
   * Invariant I1: the migration never writes a reference a configured route is
   * resolving, and never overwrites a value the store already holds. The name
   * it returns is therefore new, which is also why the migration needs no
   * staging indirection: there is nothing behind this name to damage.
   *
   * The visible consequence is deliberate rotation — importing the same
   * document again allocates the next free suffix (`_2` → `_3`) and leaves the
   * previous reference, value included, exactly where it was.
   * @param {string} route - the route being migrated.
   * @param {string|null} previous - the reference the route resolves today.
   * @returns {string} a reference nobody resolves and no value exists under.
   */
  claimFresh(route, previous = null) {
    const preferred = deriveKeyRef(route);
    if (!this.isTaken(preferred)) {
      this.#hold(preferred, route);
      if (previous && previous !== preferred) this.#rotationNote(route, previous, preferred);
      return preferred;
    }
    const chosen = this.#freeName(preferred);
    const others = this.holdersOf(preferred).filter((holder) => holder !== route);
    if (others.length) {
      this.notes.push(t(
        `${route}：credential reference ${preferred} 已被 ${others.join('、')} 使用，改用 ${chosen}`,
        `${route}: the reference ${preferred} is already used by ${others.join(', ')}; using ${chosen} instead`));
    }
    if (previous) this.#rotationNote(route, previous, chosen);
    this.#hold(chosen, route);
    return chosen;
  }

  /**
   * Take a reference this run already wrote a value under, so a re-planned
   * import reuses it instead of writing the same value a second time.
   * @param {string} ref - the reference this run wrote.
   * @param {string} route - the route the value belongs to.
   * @returns {boolean} whether the name is still this run's to point anywhere.
   */
  canAdopt(ref, route) {
    return this.holdersOf(ref).every((holder) => holder === route);
  }

  /**
   * Record that this run's value now lives under `ref` for `route`.
   * @param {string} ref - the reference this run wrote.
   * @param {string} route - the route the value belongs to.
   * @returns {string} `ref`.
   */
  adopt(ref, route) {
    this.#hold(ref, route);
    return ref;
  }

  #rotationNote(route, previous, chosen) {
    this.notes.push(t(
      `${route}：导入不改写已有 reference；credential 写到新 reference ${chosen}，原 reference ${previous} 保留`,
      `${route}: the import never overwrites an existing reference; the value goes to the new reference ${chosen}, and ${previous} is kept`));
  }

  // The smallest untaken `<base>_<n>`, so a document's references stay
  // readable instead of carrying a hash nobody can retype.
  #freeName(base) {
    for (let index = 2; ; index += 1) {
      const candidate = `${base}_${index}`;
      if (!this.isTaken(candidate)) return candidate;
    }
  }

  #hold(ref, route) {
    if (!this.holders.has(ref)) this.holders.set(ref, []);
    const routes = this.holders.get(ref);
    if (!routes.includes(route)) routes.push(route);
  }
}

/**
 * Split a `provider/model` id at its FIRST slash: the model id may contain
 * slashes (`openrouter/deepseek/deepseek-chat`), the route never may.
 * @param {string} value - the id as typed.
 * @returns {{route: string, model: string}} the two halves.
 */
export function parseModelId(value) {
  const raw = text(value);
  const slash = raw.indexOf('/');
  if (slash <= 0 || slash === raw.length - 1) {
    throw new UsageError(t(`要写成 <route>/<model>，例如 deepseek/deepseek-chat；收到 "${raw}"`,
      `Expected <route>/<model>, e.g. deepseek/deepseek-chat; got "${raw}"`));
  }
  const route = raw.slice(0, slash);
  routeProblem(route);
  return { route, model: modelProblem(raw.slice(slash + 1), raw) };
}

// The one bound the grammar states, read out of the pattern itself so the CLI
// cannot drift from the rule `/model` applies on the DSH path.
const MODEL_ID_MAX_LENGTH = Number(/\{1,(\d+)\}/.exec(DSH_MODEL_ID_PATTERN.source)?.[1]) || 256;

// Whether `/model` can select this model id on the DSH path. Exported because
// the migration uses the same rule to say which imported ids stay unselectable,
// instead of rewriting them.
// @param {string} model - the model half.
// @param {string} [fullId] - the complete `<route>/<model>` id to measure.
// @returns {boolean} true when the DSH `/model` rule accepts it.
export function selectableModelId(model, fullId = model) {
  return DSH_MODEL_ID_PATTERN.test(fullId);
}

// A model id reaches DSH as a settings value, so it is validated before any
// process starts rather than by the service that would refuse the whole write.
//
// The bound belongs to the COMPLETE id, not to the model half: `/model` tests
// the whole argument against the grammar (commands.mjs), so measuring only
// `<model>` here accepted a 256-character half that, with a route in front of
// it, produced an id no one could select. The charset still belongs to the half,
// so a bad character is reported against the part that carries it.
function modelProblem(model, fullId = model) {
  if (DSH_MODEL_ID_PATTERN.test(fullId)) return model;
  if (DSH_MODEL_ID_PATTERN.test(model)) {
    throw new UsageError(t(
      `完整 model id 太长：${fullId} 有 ${fullId.length} 个字符，<route>/<model> 合计最多 ${MODEL_ID_MAX_LENGTH} 个。/model 用的是同一条规则，这样的 ID 写进去也选不了。`,
      `The complete model id is too long: ${fullId} is ${fullId.length} characters, while <route>/<model> may be at most ${MODEL_ID_MAX_LENGTH}. /model applies the same rule, so an id this long could be written but never selected.`));
  }
  throw new UsageError(t(
    `model ${model} 不合法：只能用字母、数字和 . _ : @ + / [ ] -，且完整 <route>/<model> 最长 ${MODEL_ID_MAX_LENGTH} 个字符。`
    + `DSH 能服务更多字符（空格、#、非 ASCII），但 Vika 的 /model 只接受这一组；若你已有这类 ID，它仍留在配置里，用 /model 暂时选不了。`,
    `Invalid model name ${model}: use letters, digits and . _ : @ + / [ ] -, and keep the complete <route>/<model> within ${MODEL_ID_MAX_LENGTH} characters. `
    + 'DSH serves more characters than this (spaces, #, non-ASCII), but Vika\'s /model accepts only this set; an existing id of that kind stays in your configuration and cannot be selected from /model yet.'));
}

export function routeProblem(route) {
  if (!ROUTE_PATTERN.test(route)) {
    throw new UsageError(t(`route id ${route} 不合法：只能用字母、数字、点、下划线和短横线，且不能以符号开头`,
      `Invalid route name ${route}: use letters, digits, dots, underscores and hyphens, starting with a letter or digit`));
  }
  return route;
}

/**
 * Why a route id cannot be used, or null when it can.
 *
 * Exported for the setup wizard, which asks for a route id in a text prompt and
 * has to refuse a bad one there: the rule belongs to this module so the prompt
 * and the wizard/`remove` can never disagree about which names are legal.
 * @param {string} route - the route id as typed.
 * @returns {string|null} the message to show, or null.
 */
export function routeIdProblem(route) {
  try { routeProblem(text(route)); return null; } catch (error) { return error.message; }
}

/**
 * Why a `<route>/<model>` pair cannot be selected, or null when it can.
 * The complete id is measured, exactly as the wizard and `/model` measure it.
 * @param {string} route - the route id.
 * @param {string} model - the model half as typed (may contain `/`).
 * @returns {string|null} the message to show, or null.
 */
export function modelIdProblem(route, model) {
  try { parseModelId(`${text(route)}/${typeof model === 'string' ? model : ''}`); return null; } catch (error) { return error.message; }
}

/**
 * Why a credential reference cannot be used, or null when it can.
 * @param {string} ref - the reference as typed.
 * @returns {string|null} the message to show, or null.
 */
export function credentialRefProblem(ref) {
  try { credentialRef(ref); return null; } catch (error) { return error.message; }
}

/**
 * Build the operations that make a model write an idempotent upsert.
 *
 * The current resolved profile is the base, so fields this command does not
 * name (compat, headers, capacities, other models) survive the write. One
 * `set` at `providers.<route>` is what the settings service takes; the CLI
 * itself never sees or writes the YAML document.
 * A new route's reference is allocated the same way a migration allocates one:
 * the conventional `<ROUTE>_API_KEY` is only taken when no other route already
 * resolves it, because two routes sharing a reference means one of them sends
 * the other's secret.
 * @param {object|undefined} providers - current resolved providers dict.
 * @param {object} spec - the route/model spec to merge.
 * @param {object} [options] - `notes`, the sink for a disambiguation message.
 * @returns {Array<object>} settings path operations.
 */
export function addModelOps(providers, spec, { notes = [] } = {}) {
  // The grammar is enforced here, not only in the wizard's prompts. This is the
  // write seam (`writeModelDraft` and `setup` both reach it), so a caller that
  // skips the prompts -- or a future one that does -- cannot land a route id the
  // CLI could no longer address, a model id `/model` would reject, or a base URL
  // the protocol cannot use. Each helper throws the same message the prompt shows.
  routeProblem(text(spec.route));
  parseModelId(`${text(spec.route)}/${text(spec.model)}`);
  if (spec.keyEnv !== null && spec.keyEnv !== undefined) credentialRef(spec.keyEnv);
  if (spec.baseURL !== null && spec.baseURL !== undefined) {
    let protocol = null;
    try { protocol = new URL(String(spec.baseURL)).protocol; } catch { protocol = null; }
    if (protocol !== 'http:' && protocol !== 'https:') {
      throw new UsageError(t(`网关地址 ${spec.baseURL} 不合法：要写成 http(s) 地址`,
        `Invalid gateway URL ${spec.baseURL}: expected an http(s) address`));
    }
  }
  const existing = plainObject(providers) && plainObject(providers[spec.route]) ? providers[spec.route] : null;
  const profile = existing ? structuredClone(existing) : {};
  if (spec.api !== null) profile.api = spec.api;
  if (spec.baseURL !== null) profile.baseURL = spec.baseURL;
  if (!text(profile.api)) profile.api = PROTOCOLS[0];
  if (!text(profile.baseURL)) {
    throw new UsageError(t(`route ${spec.route} 还没有 baseURL：添加 model 时请填网关地址`,
      `Route ${spec.route} has no baseURL yet: fill in the gateway URL when adding the model`));
  }
  const allocator = new KeyRefAllocator(providers, notes);
  if (spec.keyEnv !== null) {
    // An explicit reference is a decision, not a guess: if another route already
    // resolves it, the two would silently authenticate with one secret, and a
    // later import of either route would find the name shared. Refusing keeps
    // the operator in charge of a name that is about to mean one secret.
    const others = allocator.holdersOf(spec.keyEnv).filter((holder) => holder !== spec.route);
    if (others.length) {
      throw new UsageError(t(
        `credential reference ${spec.keyEnv} 已经被 ${others.join('、')} 解析，两个 route 不能共用同一个 reference：`
        + `那样一个 route 会带上另一个的密钥。换一个 reference；要复用同一个密钥，用 vika dsh model key set <新 reference> 存同一个值。`,
        `The reference ${spec.keyEnv} is already resolved by ${others.join(', ')}; two routes may not share one reference, `
        + 'because one of them would then send the other\'s secret. Pick another reference; to reuse the same secret, store it under a new reference with vika dsh model key set <REF>.'));
    }
    profile.apiKeyEnv = spec.keyEnv;
  } else if (!existing && !text(profile.apiKeyEnv)) {
    profile.apiKeyEnv = allocator.claim(spec.route);
  }
  // A route that already names a reference another route resolves (hand-edited
  // documents, an older build) is not silently re-pointed — the write is an upsert —
  // but the sharing is said out loud instead of staying invisible.
  const sharedWith = allocator.holdersOf(text(profile.apiKeyEnv)).filter((holder) => holder !== spec.route);
  if (sharedWith.length) {
    notes.push(t(
      `${spec.route}：credential reference ${text(profile.apiKeyEnv)} 同时被 ${sharedWith.join('、')} 解析，两个 route 会用同一个密钥`,
      `${spec.route}: the reference ${text(profile.apiKeyEnv)} is also resolved by ${sharedWith.join(', ')}, so both routes use the same secret`));
  }
  if (spec.name !== null && !existing) profile.displayName = spec.name;

  const models = Array.isArray(profile.models) ? [...profile.models] : [];
  const index = models.findIndex((entry) => plainObject(entry) && entry.id === spec.model);
  const entry = index >= 0 && plainObject(models[index]) ? { ...models[index] } : { id: spec.model };
  entry.id = spec.model;
  if (spec.name !== null) entry.name = spec.name;
  if (spec.input !== null) entry.input = [...spec.input];
  if (spec.contextWindow !== null) entry.contextWindow = spec.contextWindow;
  if (spec.maxTokens !== null) entry.maxTokens = spec.maxTokens;
  if (spec.noReasoning) entry.reasoningEfforts = false;
  if (index >= 0) models[index] = entry;
  else models.push(entry);
  profile.models = models;
  return [{ op: 'set', path: ['providers', spec.route], value: profile }];
}

/**
 * Operations that remove one model, or one whole route.
 *
 * A route whose last model is removed is dropped entirely: an empty route can
 * never serve a request, and DSH only reports it as a route with no models.
 * @param {object|undefined} providers - current resolved providers dict.
 * @param {{route: string, model: string|null}} target - what to remove.
 * @returns {{ops: Array<object>, droppedRoute: boolean, keyRef: string|null}} the edit.
 */
export function removeModelOps(providers, target) {
  const current = plainObject(providers) && plainObject(providers[target.route]) ? providers[target.route] : null;
  if (!current) {
    throw new Error(t(`没有 ${target.route} 这个 route`, `There is no route named ${target.route}`));
  }
  const keyRef = text(current.apiKeyEnv) || null;
  if (target.model === null) {
    return { ops: [{ op: 'unset', path: ['providers', target.route] }], droppedRoute: true, keyRef };
  }
  const models = Array.isArray(current.models) ? current.models : [];
  const remaining = models.filter((entry) => !(plainObject(entry) && entry.id === target.model));
  if (remaining.length === models.length) {
    throw new Error(t(`${target.route} 下没有 ${target.model} 这个 model`, `Route ${target.route} has no model ${target.model}`));
  }
  if (remaining.length === 0) {
    return { ops: [{ op: 'unset', path: ['providers', target.route] }], droppedRoute: true, keyRef };
  }
  const profile = { ...structuredClone(current), models: remaining };
  return { ops: [{ op: 'set', path: ['providers', target.route], value: profile }], droppedRoute: false, keyRef };
}

// ---------------------------------------------------------------------------
// Effective model configuration
// ---------------------------------------------------------------------------

// The fields a Pi `modelOverrides` value may set that a DSH model profile has
// no counterpart for. Pi merges the override over the `models` entry it names
// (`applyModelOverride` in pi's provider-composer), so ignoring one changes what
// the model actually does; dropping it silently would be worse, because the
// operator would believe the override had been migrated. The five fields a DSH
// entry does have — name, contextWindow, maxTokens, input, reasoningEfforts —
// plus compat are merged in `effectiveModel` below.
const UNEXPRESSIBLE_MODEL_FIELDS = Object.freeze([
  'inputLimits', 'cost', 'promptCache', 'samplingParams', 'samplingParamsByThinkingLevel', 'headers',
]);

// Pi merges a model's `compat` with its override's field by field, and merges
// the dict-valued members one level deeper.
function mergeCompatValues(base, override) {
  if (!plainObject(override)) return plainObject(base) ? { ...base } : undefined;
  if (!plainObject(base)) return { ...override };
  const merged = { ...base, ...override };
  for (const key of COMPAT_DICTS) {
    if (plainObject(base[key]) || plainObject(override[key])) {
      merged[key] = {
        ...(plainObject(base[key]) ? base[key] : {}),
        ...(plainObject(override[key]) ? override[key] : {}),
      };
    }
  }
  return merged;
}

/**
 * One model as Pi serves it: its `models` entry with its `modelOverrides`
 * counterpart merged over it.
 *
 * The endpoint and protocol are not overridable — Pi's schema has no `api` or
 * `baseUrl` in an override — so they stay on the entry and are resolved later,
 * against the whole route.
 * @returns {object} a detached model entry carrying the effective values.
 */
function effectiveModel(label, model, override, notes) {
  if (!plainObject(override)) return { ...model };
  for (const field of UNEXPRESSIBLE_MODEL_FIELDS) {
    if (override[field] === undefined) continue;
    notes.push(t(`${label}：modelOverrides.${field} 在 DSH 里没有对应字段，已丢弃`,
      `${label}: modelOverrides.${field} has no DSH counterpart; dropped`));
  }
  if (override.id !== undefined) {
    notes.push(t(`${label}：modelOverrides 条目里的 "id" 就是它自己的键名，已忽略`,
      `${label}: "id" inside a modelOverrides entry is its own key; ignored`));
  }
  const merged = { ...model };
  if (override.name !== undefined) merged.name = override.name;
  if (override.reasoning !== undefined) merged.reasoning = override.reasoning;
  if (override.input !== undefined) merged.input = override.input;
  if (override.contextWindow !== undefined) merged.contextWindow = override.contextWindow;
  if (override.maxTokens !== undefined) merged.maxTokens = override.maxTokens;
  if (override.thinkingLevelMap !== undefined) {
    merged.thinkingLevelMap = {
      ...(plainObject(model.thinkingLevelMap) ? model.thinkingLevelMap : {}),
      ...(plainObject(override.thinkingLevelMap) ? override.thinkingLevelMap : {}),
    };
  }
  if (override.compat !== undefined) merged.compat = mergeCompatValues(model.compat, override.compat);
  return merged;
}

// The distinct non-empty values these entries declare for one field, and the
// single value they agree on ('' when they do not agree).
function agreedValue(models, read) {
  const values = [...new Set(models.map(read).filter(Boolean))];
  return { values, single: values.length === 1 ? values[0] : '' };
}

/**
 * Translate one Pi provider entry into the native DSH routes it needs.
 *
 * Pi lets every model name its own protocol and endpoint while DSH serves one
 * of each per route, so the decision is made on each model's *effective*
 * configuration — its entry merged with any `modelOverrides` — and the models
 * are then grouped by that pair. A route whose models resolve to more than one
 * pair is split into `<route>`, `<route>--2`, … rather than re-pointed: taking
 * the first model's endpoint (or the first one after skipping a model DSH
 * cannot serve) sent requests to a provider the model never named.
 *
 * @param {object} context - the auth document, the note sink and the route
 *   names already claimed by this migration.
 * @returns {{groups: Array<{route: string, profile: object}>, credential: object|null}|null}
 *   one entry per native route, or null when nothing survives translation.
 */
function translateProvider(route, provider, { auth, notes, taken }) {
  // The same rule the wizard and `remove` apply. An import that wrote a key they
  // refuse would leave a route nobody can address again: it would serve, and
  // `remove` would reject the name it was stored under.
  if (!ROUTE_PATTERN.test(route)) {
    notes.push(t(`${route}：route id 不合法（只能用字母、数字、点、下划线和短横线，且不能以符号开头），已跳过`,
      `${route}: invalid route name (letters, digits, dots, underscores and hyphens, starting with a letter or digit); skipped`));
    return null;
  }
  if (!plainObject(provider)) {
    notes.push(t(`${route}：provider 不是对象，已跳过`, `${route}: provider is not an object; skipped`));
    return null;
  }
  const declaredApi = text(provider.api);
  if (declaredApi && !PROTOCOLS.includes(declaredApi)) {
    notes.push(t(`${route}：api "${declaredApi}" 不是 DSH 能服务的 protocol（${PROTOCOLS.join(', ')}），已跳过`,
      `${route}: api "${declaredApi}" is not served by DSH (${PROTOCOLS.join(', ')}); skipped`));
    return null;
  }
  const declaredBaseUrl = text(provider.baseUrl);
  const catalogModels = Array.isArray(provider.models) ? provider.models : [];
  const overrides = plainObject(provider.modelOverrides) ? provider.modelOverrides : {};
  if (catalogModels.length === 0) {
    // DSH refuses `modelOverrides` on a route its installed catalogue does not
    // describe, and a hand-declared route has no catalogue: there is no model
    // for an override to land on, so say that instead of migrating a fragment
    // the settings service would reject.
    notes.push(Object.keys(overrides).length
      ? t(`${route}：只有 modelOverrides 没有 models 列表；手写 route 必须先列出 model，已跳过`,
        `${route}: declares modelOverrides but no models list; a hand-declared route must list its models, so it is skipped`)
      : t(`${route}：没有 models 列表；手写 route 无法继承内置目录，已跳过`,
        `${route}: no models list; a hand-declared route cannot serve the installed catalog, so it is skipped`));
    return null;
  }

  // Every model that survives its own protocol check, carrying the effective
  // values Pi would serve.
  const entries = [];
  const declaredIds = new Set();
  for (const model of catalogModels) {
    if (!plainObject(model)) {
      notes.push(t(`${route}：有一个 model 条目不是对象，已跳过`, `${route}: a model entry is not an object; skipped`));
      continue;
    }
    // The id is migrated byte for byte. Trimming it looked harmless and was
    // not: `"model "` silently became `model`, and two ids that differ only in
    // whitespace collapsed into a duplicate DSH then refuses as
    // "lists model more than once" — a rejection that points at the wrong
    // problem. A migration that cannot keep an id says so or skips it; it never
    // quietly renames one.
    const id = typeof model.id === 'string' ? model.id : '';
    if (!id.trim()) {
      notes.push(t(`${route}：有一个 model 条目的 id 是空的（或只有空白），已跳过`,
        `${route}: a model entry has an empty id (or whitespace only); skipped`));
      continue;
    }
    if (declaredIds.has(id)) {
      notes.push(t(`${route}/${id}：这个 id 在 models 里出现了不止一次，已保留第一条（DSH 会因重复 id 拒收整个 route）`,
        `${route}/${id}: appears more than once in the models list; the first one is kept (DSH refuses a route that lists an id twice)`));
      continue;
    }
    declaredIds.add(id);
    // DSH serves these ids, but `/model` cannot select them yet. Saying so is
    // the promise the documentation makes; rewriting the id to fit would be the
    // silent rename this migration must not do.
    if (!selectableModelId(id, `${route}/${id}`)) {
      notes.push(t(`${route}/${id}：这个 id 已原样迁移，但 /model 现在选不了它（只接受字母、数字和 . _ : @ + / [ ] -，且完整 <route>/<model> 不超过 ${MODEL_ID_MAX_LENGTH} 个字符）`,
        `${route}/${id}: migrated verbatim, but /model cannot select it (it accepts letters, digits and . _ : @ + / [ ] -, and at most ${MODEL_ID_MAX_LENGTH} characters for <route>/<model>)`));
    }
    const modelApi = text(model.api);
    if (modelApi && !PROTOCOLS.includes(modelApi)) {
      notes.push(t(`${route}/${id}：api "${modelApi}" 不是 DSH 能服务的 protocol，跳过该 model`,
        `${route}/${id}: api "${modelApi}" is not served by DSH; model skipped`));
      continue;
    }
    const effective = effectiveModel(`${route}/${id}`, model, overrides[id], notes);
    entries.push({
      id,
      model: effective,
      declaredApi: modelApi,
      declaredBaseUrl: text(effective.baseUrl),
    });
  }
  // An override whose model is not in the list (or was just dropped) has
  // nothing to apply to. Pi ignores unknown ids; a migration must say so.
  for (const id of Object.keys(overrides)) {
    if (declaredIds.has(id)) continue;
    notes.push(t(`${route}：modelOverrides 里的 ${id} 没有对应的 models 条目，已忽略（Pi 对未知 id 也忽略）`,
      `${route}: modelOverrides names ${id}, which the models list does not declare; ignored`));
  }
  if (entries.length === 0) {
    notes.push(t(`${route}：没有 model 能翻译过来，已跳过`, `${route}: no model survives translation; skipped`));
    return null;
  }

  // A value only the models declare is still the route's value — dropping it
  // migrated a private gateway with no endpoint at all, which DSH then refuses
  // at request time. It may be promoted only when the models that declare one
  // agree on a single value; otherwise each model keeps its own, and a model
  // that declares none has no endpoint that can be determined.
  const agreedApi = agreedValue(entries, (entry) => entry.declaredApi);
  const agreedBaseUrl = agreedValue(entries, (entry) => entry.declaredBaseUrl);
  const promotedApi = declaredApi ? '' : agreedApi.single;
  const promotedBaseUrl = declaredBaseUrl ? '' : agreedBaseUrl.single;
  if (!declaredApi && promotedApi) {
    notes.push(t(`${route}：route 没有声明 api，按 model 条目的 "${promotedApi}" 迁移`,
      `${route}: the route declares no api; the api taken from the model entries is "${promotedApi}"`));
  }
  if (!declaredBaseUrl && promotedBaseUrl) {
    notes.push(t(`${route}：route 没有声明 baseUrl，按 model 条目的 ${promotedBaseUrl} 迁移`,
      `${route}: the route declares no baseUrl; the baseURL taken from the model entries is ${promotedBaseUrl}`));
  }

  // One group per effective (protocol, endpoint) pair, in document order so the
  // first model's route keeps the plain name.
  const groups = new Map();
  for (const entry of entries) {
    // Pi's own precedence: the model first, then the route, then the value the
    // model entries agree on (which is the only safe promotion when the route
    // declares nothing).
    const api = entry.declaredApi || declaredApi || promotedApi;
    const baseURL = entry.declaredBaseUrl || declaredBaseUrl || promotedBaseUrl;
    if (!api) {
      notes.push(t(`${route}/${entry.id}：model 和 route 都没有声明 api，DSH 无法服务该 model，已跳过`,
        `${route}/${entry.id}: neither the model nor the route declares an api, so DSH cannot serve it; skipped`));
      continue;
    }
    if (!baseURL) {
      // Naming the ambiguity is the whole point: this is the model that used to
      // be sent to whichever sibling happened to declare an endpoint first.
      notes.push(t(`${route}/${entry.id}：model 没有声明 baseUrl，route 也没有，同一 route 的其他 model 又用了不止一个端点；DSH 需要确切端点，已跳过该 model`,
        `${route}/${entry.id}: declares no endpoint, the route declares none, and its sibling models use more than one; DSH needs an exact endpoint, so the model is skipped`));
      continue;
    }
    const key = `${api}\u0000${baseURL}`;
    if (!groups.has(key)) groups.set(key, { api, baseURL, models: [] });
    groups.get(key).models.push(entry);
  }
  if (groups.size === 0) {
    notes.push(t(`${route}：没有 model 能翻译过来，已跳过`, `${route}: no model survives translation; skipped`));
    return null;
  }

  if (plainObject(provider.headers) && Object.keys(provider.headers).length) {
    notes.push(t(`${route}：headers 不迁移（值里可能有 credential；DSH 只通过 apiKeyEnv 解析 credential）`,
      `${route}: headers are not migrated (values may carry credentials; DSH resolves credentials through apiKeyEnv)`));
  }

  const split = groups.size > 1;
  const outputs = [];
  let index = 0;
  for (const group of groups.values()) {
    index += 1;
    let name = route;
    if (index > 1) {
      let suffix = index;
      while (taken.has(`${route}--${suffix}`)) suffix += 1;
      name = `${route}--${suffix}`;
    }
    taken.add(name);
    const profile = {};
    if (text(provider.name)) {
      profile.displayName = split && index > 1 ? `${text(provider.name)} (${index})` : text(provider.name);
    }
    profile.api = group.api;
    profile.baseURL = group.baseURL;
    // The route-level compat belongs to the protocol it was written for, so it
    // is filtered against each group's own protocol rather than once for all.
    const groupCompat = filterCompat(provider.compat, group.api, name, notes);
    if (groupCompat) profile.compat = groupCompat;
    const models = [];
    for (const entry of group.models) {
      const model = entry.model;
      const modelEntry = { id: entry.id };
      if (text(model.name)) modelEntry.name = text(model.name);
      if (Number.isFinite(model.contextWindow) && model.contextWindow > 0) {
        modelEntry.contextWindow = Math.round(model.contextWindow);
      }
      if (Number.isFinite(model.maxTokens) && model.maxTokens > 0) modelEntry.maxTokens = Math.round(model.maxTokens);
      if (Array.isArray(model.input)) {
        const input = [...new Set(model.input.filter((modality) => MODALITIES.includes(modality)))];
        if (input.length) modelEntry.input = input;
        else notes.push(t(`${route}/${entry.id}：input 里没有已知模态，按 route 默认值处理`,
          `${route}/${entry.id}: input declares no known modality; the route default applies`));
      }
      const efforts = reasoningEffortsOf(model);
      if (efforts === false) modelEntry.reasoningEfforts = false;
      else if (efforts) modelEntry.reasoningEfforts = efforts;
      const modelCompat = filterCompat(model.compat, group.api, `${route}/${entry.id}`, notes);
      if (modelCompat) modelEntry.compat = modelCompat;
      if (model.cost) notes.push(t(`${route}/${entry.id}：cost 不属于 DSH 配置，已忽略`,
        `${route}/${entry.id}: cost is not part of the DSH profile; ignored`));
      models.push(modelEntry);
    }
    profile.models = models;
    outputs.push({ route: name, profile });
  }
  if (split) {
    notes.push(t(`${route}：model 的有效配置有 ${outputs.length} 种（端点或 protocol 不同），已拆成 ${outputs.length} 个 route：${outputs.map((entry) => entry.route).join(', ')}`,
      `${route}: the models resolve to ${outputs.length} distinct effective endpoints or protocols; split into ${outputs.length} routes: ${outputs.map((entry) => entry.route).join(', ')}`));
  }

  return { groups: outputs, credential: resolveCredential(route, provider, { auth, notes }) };
}

// A filtered `compat` block, or undefined when nothing usable is left.
function filterCompat(compat, api, label, notes) {
  if (!plainObject(compat)) return undefined;
  if (!api) {
    if (Object.keys(compat).length) notes.push(t(`${label}：route 没有声明 api，compat 已丢弃`,
      `${label}: compat dropped because the route declares no api`));
    return undefined;
  }
  const offered = COMPAT_FIELDS[api] ?? [];
  const filtered = {};
  for (const [key, value] of Object.entries(compat)) {
    if (!offered.includes(key)) {
      notes.push(t(`${label}：compat.${key} 在 ${api} 上不可配置，已丢弃`,
        `${label}: compat.${key} is not configurable on ${api}; dropped`));
      continue;
    }
    if (Object.hasOwn(COMPAT_ENUMS, key)) {
      if (COMPAT_ENUMS[key].includes(value)) filtered[key] = value;
      else notes.push(t(`${label}：compat.${key}="${String(value)}" 不受支持，已丢弃`,
        `${label}: compat.${key}="${String(value)}" is not supported; dropped`));
      continue;
    }
    if (COMPAT_DICTS.includes(key)) {
      const entries = plainObject(value)
        ? Object.entries(value).filter(([, member]) => member === null
          || ['string', 'number', 'boolean'].includes(typeof member)
          || (plainObject(member) && CHAT_TEMPLATE_VARS.includes(member.$var)))
        : [];
      if (entries.length) filtered[key] = Object.fromEntries(entries);
      else notes.push(t(`${label}：compat.${key} 没有可用值，已丢弃`,
        `${label}: compat.${key} has no usable value; dropped`));
      continue;
    }
    if (typeof value === 'boolean' || typeof value === 'number') filtered[key] = value;
    else notes.push(t(`${label}：compat.${key} 的值类型不受支持，已丢弃`,
      `${label}: compat.${key} has an unsupported value; dropped`));
  }
  return Object.keys(filtered).length ? filtered : undefined;
}

/**
 * Pi's `thinkingLevelMap` as DSH's `reasoningEfforts`.
 * @returns {false|Record<string, string|null>|undefined} false for a
 *   non-reasoning model, undefined when nothing is declared.
 */
export function reasoningEffortsOf(model) {
  if (model?.reasoning !== true) return false;
  const map = plainObject(model.thinkingLevelMap) ? model.thinkingLevelMap : {};
  const efforts = {};
  for (const level of THINKING_LEVELS) {
    const wire = map[level];
    if (wire === null) continue;
    if (typeof wire === 'string' && wire) {
      efforts[level] = wire;
      continue;
    }
    if (!IMPLICIT_LEVELS.includes(level)) continue;
    // `off` with no value is the correct spelling of "send nothing"; every
    // other implicit level sends its own name.
    efforts[level] = level === 'off' ? null : level;
  }
  // DSH refuses a declaration that offers nothing beyond "off"; such a route
  // cannot think anyway, so it is migrated as non-reasoning.
  if (!Object.keys(efforts).some((level) => level !== 'off')) return false;
  return efforts;
}

// What one Pi route says about its credential: a reference it names explicitly,
// a literal to store under the reference this migration allocates, or nothing.
// @returns {{kind: 'explicit'|'literal'|'none', ref: string|null, value: string|null}}
function resolveCredential(route, provider, { auth, notes }) {
  const declared = provider.apiKey;
  if (typeof declared === 'string' && declared) {
    const envRef = /^\$([A-Za-z_][A-Za-z0-9_]*)$/.exec(declared);
    if (envRef) {
      const name = envRef[1];
      if (WITHHELD_ENV.test(name)) {
        notes.push(t(`${route}：apiKey 指向 ${name}，这个变量会被 DSH 子进程环境过滤，只能从 credential 服务解析；迁移不带值，存一次才可用：vika dsh model key set ${name}`,
          `${route}: apiKey names ${name}, which the DSH child environment withholds; only the credential service can resolve it, so the migration carries no value and the route works after: vika dsh model key set ${name}`));
      }
      return { kind: 'explicit', ref: name, value: null };
    }
    if (declared.startsWith('!')) {
      notes.push(t(`${route}：apiKey 是一条命令；CLI 不执行命令，该 route 迁移后没有 credential`,
        `${route}: apiKey runs a command; the CLI does not execute it, so the route is migrated without a credential`));
      return { kind: 'none', ref: null, value: null };
    }
    return { kind: 'literal', ref: null, value: declared };
  }
  const stored = auth?.[route];
  const key = typeof stored?.key === 'string' ? stored.key : '';
  if (key) return { kind: 'literal', ref: null, value: key };
  if (stored) {
    notes.push(t(`${route}：auth.json 里是 ${stored.type ?? '非 api-key'} credential，DSH 不能当 API key 用；该 route 迁移后没有 credential`,
      `${route}: auth.json holds a ${stored.type ?? 'non-api-key'} credential DSH cannot use as an API key; the route is migrated without a credential`));
  }
  return { kind: 'none', ref: null, value: null };
}

// Pi's auth.json search order, as the daemon's old model layer used it: beside
// models.json first, then the Pi agent directory.
function readPiAuth(file, env = process.env) {
  const candidates = [path.join(path.dirname(file), 'auth.json')];
  const configured = text(env.PI_CODING_AGENT_DIR);
  const home = text(env.HOME) || '';
  if (configured) candidates.push(path.join(configured.replace(/^~(?=\/|$)/, home), 'auth.json'));
  else if (home) candidates.push(path.join(home, '.pi', 'agent', 'auth.json'));
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(fs.readFileSync(candidate, 'utf8'));
      if (plainObject(parsed)) return parsed;
    } catch {}
  }
  return {};
}

/**
 * Translate a whole `models.json` into native provider operations.
 *
 * The plan is a pure function of the document and, when given, the routes the
 * instance already serves plus the references its credential store already
 * holds: reference allocation has to know both, or a migration could write a
 * name another route resolves.
 *
 * @param {object} document - parsed Pi models.json.
 * @param {object} [options] - `file` (for the auth.json search), `env`,
 *   `providers` (the instance's current resolved providers dict, when
 *   reachable), `stored` (references the credential store holds a value under)
 *   and `pinned` (route → the reference this run already stored a value under).
 * @returns {{ops: Array<object>, credentials: Array<object>, routes: Array<object>, notes: string[]}}
 *   `credentials` carries `{ref, value, route, previous}`: the reference the
 *   value is written under, the literal, and the reference the route resolved
 *   before this migration (`null` when the route is new). That previous
 *   reference is never written and never removed — rotation, not replacement.
 */
export function importPlan(document, {
  file = '', env = process.env, providers = null, stored = null, pinned = null,
} = {}) {
  if (!plainObject(document) || !plainObject(document.providers)) {
    throw new Error(t('models.json 里没有 providers 对象', 'models.json has no providers object'));
  }
  const notes = [];
  const ops = [];
  const credentials = [];
  const routes = [];
  const auth = file ? readPiAuth(file, env) : {};
  const allocator = new KeyRefAllocator(providers, notes, { stored: stored ?? [] });
  const configured = plainObject(providers) ? providers : {};
  const taken = new Set([...Object.keys(document.providers), ...Object.keys(configured)]);
  const previousRefOf = (route) => text(configured[route]?.apiKeyEnv) || null;

  const translated = [];
  for (const route of Object.keys(document.providers).sort()) {
    const result = translateProvider(route, document.providers[route], { auth, notes, taken });
    if (result) translated.push({ route, ...result });
  }
  // Reference names are decided in two passes so an explicit `$NAME` is claimed
  // before any derived name can take it: the document said those two routes
  // share a secret, and a derived name must never quietly become the second
  // claimant of a reference somebody else already resolves.
  for (const item of translated) {
    if (item.credential.kind === 'explicit') allocator.reserve(item.credential.ref, item.route);
  }
  const claimed = new Map();
  for (const item of translated) {
    const previous = previousRefOf(item.route);
    let ref = item.credential.kind === 'explicit' ? item.credential.ref : null;
    if (!ref && item.credential.kind === 'literal') {
      // A value is only ever written under a reference this run allocated. On a
      // re-plan, reuse the one it already wrote (same value, same name) instead
      // of leaving that value behind and writing a second copy — unless a
      // concurrent writer started resolving that name meanwhile, in which case
      // it is no longer this run's to point at and a fresh one is allocated.
      const carried = plainObject(pinned) ? text(pinned[item.route]) : '';
      if (carried && allocator.canAdopt(carried, item.route)) {
        ref = allocator.adopt(carried, item.route);
      } else {
        if (carried) {
          notes.push(t(
            `${item.route}：导入过程中有别的 route 开始使用 ${carried}，本次改用新 reference；那个 reference 和它的值都保留`,
            `${item.route}: another route started resolving ${carried} while the import was running; a new reference is used instead and ${carried} is left untouched`));
        }
        ref = allocator.claimFresh(item.route, previous);
      }
    }
    item.ref = ref;
    if (!ref) continue;
    if (!claimed.has(ref)) {
      claimed.set(ref, {
        ref,
        value: item.credential.value,
        route: item.route,
        previous,
      });
    }
  }
  for (const item of translated) {
    // A route the document mentions but gives no credential for must keep the
    // one it already resolves: the whole-route `set` replaces the profile, so
    // omitting `apiKeyEnv` would silently move the route from its stored key to
    // DSH's own environment discovery — usually a `MISSING_CREDENTIAL` at the
    // first request, with no note to explain it.
    //
    // Only an existing route can have a reference to keep. A split half this
    // migration creates is a new route, so there is nothing to preserve and the
    // document's silence means "resolve it yourself".
    const kept = new Map();
    if (item.credential.kind === 'none') {
      for (const group of item.groups) {
        const existingRef = plainObject(configured[group.route]) ? text(configured[group.route]?.apiKeyEnv) : '';
        if (existingRef) kept.set(group.route, existingRef);
      }
      const keptList = [...kept.entries()].map(([route, ref]) => `${route} → ${ref}`);
      const bare = item.groups.map((group) => group.route).filter((route) => !kept.has(route));
      if (keptList.length) {
        notes.push(t(
          `${item.route}：文件没有声明 credential，保留现有 reference（${keptList.join('、')}）`
          + (bare.length ? `；新拆出的 ${bare.join(', ')} 本来就没有 reference，按无 reference 迁移` : ''),
          `${item.route}: the document declares no credential, so the existing reference is kept (${keptList.join(', ')})`
          + (bare.length ? `; the new split half ${bare.join(', ')} had none, so it is migrated without one` : '')));
      } else if (plainObject(configured[item.route])) {
        notes.push(t(
          `${item.route}：文件没有声明 credential，这个 route 原本也没有 reference，继续由 DSH 自己的环境变量发现解析`,
          `${item.route}: the document declares no credential and the route had none, so DSH's own environment discovery keeps resolving it`));
      }
    }
    for (const group of item.groups) {
      const profile = group.profile;
      const ref = item.ref || kept.get(group.route) || '';
      if (ref) profile.apiKeyEnv = ref;
      ops.push({ op: 'set', path: ['providers', group.route], value: profile });
      routes.push({
        route: group.route,
        source: item.route,
        api: profile.api ?? '',
        baseURL: profile.baseURL ?? '',
        apiKeyEnv: profile.apiKeyEnv ?? '',
        // The reference the source route resolved before this migration; the
        // report names it so the operator knows what was left in place.
        previousRef: previousRefOf(item.route) ?? '',
        models: profile.models.map((model) => model.id),
      });
    }
  }
  for (const credential of claimed.values()) credentials.push(credential);
  return { ops, credentials, routes, notes };
}

// ---------------------------------------------------------------------------
// What the credential store already holds
// ---------------------------------------------------------------------------

// The reference names in `<DSH_HOME>/.credentials.yaml`, or an empty set when
// the document does not exist yet (a fresh instance).
//
// The bridge has no "list credentials" method, and a migration must not write a
// name that already holds a value: doing so would destroy a secret nothing has
// claimed yet, which is the same data loss as overwriting a live one, only
// quieter. Reading the store is the one way to know; it is a read of DSH's own
// document, never a write, and the values are never touched — only the keys.
//
// The document is YAML, and this CLI has no YAML dependency on purpose. Only the
// top-level keys under `refs:` are needed, so a line scan is enough: every line
// deeper than `refs:` that starts with `key:` contributes its key, and the scan
// stops at the first line that dedents. An unparsable key still counts (the raw
// text before the first colon is used), because over-reporting a name only
// makes the migration pick a higher suffix while under-reporting would overwrite.
/**
 * @param {string|Buffer} contents - the credential document's bytes.
 * @returns {Set<string>} the reference names it declares.
 */
export function credentialRefsOf(contents) {
  const refs = new Set();
  const lines = String(contents ?? '').split('\n');
  let inside = false;
  let indentOfRefs = -1;
  for (const line of lines) {
    if (!line.trim() || line.trimStart().startsWith('#')) continue;
    const indent = line.length - line.trimStart().length;
    if (!inside) {
      if (/^refs:\s*$/.test(line)) {
        inside = true;
        indentOfRefs = indent;
      } else if (/^refs:\s*\{\s*\}\s*$/.test(line)) {
        return refs;
      }
      continue;
    }
    if (indent <= indentOfRefs) break;
    const colon = line.indexOf(':');
    if (colon < 0) continue;
    const key = line.slice(0, colon).trim().replace(/^["']|["']$/g, '');
    if (key) refs.add(key);
  }
  return refs;
}

export function credentialRef(value) {
  const ref = text(value);
  if (!REF_PATTERN.test(ref)) {
    // `key set REF=secret` is the natural mistake, and it puts the secret in the
    // reference position. Quoting the whole token would print it to stderr and
    // into whatever captures stderr, so only the part before an `=` is shown --
    // and that part is what the operator meant to type.
    const shown = ref.includes('=') ? `${ref.slice(0, ref.indexOf('='))}=…` : ref;
    throw new UsageError(t(`credential reference ${shown || '(空)'} 不合法：只能用字母、数字、下划线，且不能以数字开头`,
      `Invalid credential reference ${shown || '(empty)'}: use letters, digits and underscores, not starting with a digit`));
  }
  return ref;
}

// The catalogue as a lookup by route, so settings-declared routes that the
// catalogue omits (a failed route, or one DSH does not list) still show up.
export function rowsOf(providers, catalog) {
  const rows = new Map();
  for (const entry of Array.isArray(catalog?.providers) ? catalog.providers : []) {
    if (!plainObject(entry) || typeof entry.provider !== 'string') continue;
    rows.set(entry.provider, { catalog: entry });
  }
  for (const [route, profile] of Object.entries(providers)) {
    rows.set(route, { ...(rows.get(route) ?? {}), profile: plainObject(profile) ? profile : {} });
  }
  return [...rows.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([provider, { catalog: entry = null, profile = null }]) => {
      const catalogModels = Array.isArray(entry?.models) ? entry.models : [];
      const known = new Set(catalogModels.map((model) => model.id));
      const declared = Array.isArray(profile?.models) ? profile.models : [];
      const models = [
        ...catalogModels.map((model) => ({
          id: model.id,
          name: model.name ?? model.id,
          input: Array.isArray(model.inputModalities) ? [...model.inputModalities] : null,
          inCatalog: true,
        })),
        ...declared
          .filter((model) => plainObject(model) && typeof model.id === 'string' && !known.has(model.id))
          .map((model) => ({
            id: model.id,
            name: typeof model.name === 'string' && model.name ? model.name : model.id,
            input: Array.isArray(model.input) && model.input.length ? [...model.input] : null,
            inCatalog: false,
          })),
      ];
      const apiKeyEnv = text(profile?.apiKeyEnv) || null;
      return {
        provider,
        name: text(profile?.displayName) || text(entry?.name) || provider,
        declared: profile !== null,
        api: text(profile?.api) || null,
        baseURL: text(profile?.baseURL) || null,
        apiKeyEnv,
        // `known:false` means the credential service answered late, so a
        // false `present` is not proof that the value is missing.
        credential: plainObject(entry?.credential)
          ? {
            ref: entry.credential.ref,
            present: entry.credential.present === true,
            // `known:false` (bridge v2.1) means the credentials service had not
            // answered yet; `present:null` is the same state from a v2 bridge.
            known: entry.credential.known === true
              || (entry.credential.known === undefined && entry.credential.present !== null),
          }
          : null,
        models,
        error: entry?.error && typeof entry.error.message === 'string'
          ? { code: entry.error.code ?? null, message: entry.error.message }
          : null,
      };
    });
}
