// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The model-route wizard shared by `ov-superagent setup` and `ov-superagent dsh model`.
//
// One implementation, two entry points: setup asks these questions while it
// creates an instance (the draft is written only when the instance is saved),
// and `ov-superagent dsh model` asks the same ones for an instance that already exists
// (the draft is written right away). The questions, their validators and the
// write path live here so the two cannot drift apart.
//
// Nothing in this module writes to an instance until `writeDshModelDraft` is
// called, and that call is the caller's decision: setup defers it to its save
// step, the model command runs it immediately after the draft checks out.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { t } from './i18n.mjs';
import {
  PROTOCOLS, addModelOps, credentialRefProblem, deriveKeyRef, modelIdProblem, routeIdProblem,
} from './dsh-model-plan.mjs';
import { modelSpecFromDraft, writeModelDraft } from './dsh-model-store.mjs';
import { probeDsh } from './runtime-probe.mjs';

// The namespace row of DSH's settings document; the settings service is the
// document's writer, and its global `ns` for the model plugin is this same id.
export const DSH_MODEL_NAMESPACE = 'llm-pi-ai';

// The document paths of one instance's DSH home.
export function dshSettingsDocument(dataDir) {
  return path.join(dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
}

export function dshCredentialsDocument(dataDir) {
  return path.join(dataDir, 'dsh', 'home', '.credentials.yaml');
}

/**
 * The model id an operator copies from the terminal: `<route>/<model>`.
 * It is the spelling `/model` and `ov-superagent dsh model remove` take, not the
 * `dsh:`-qualified value the manifest carries.
 * @param {{route: string, model: string}} draft - the route and model.
 * @returns {string} the canonical id.
 */
export function canonicalModelId(draft) {
  return `${draft.route}/${draft.model}`;
}

// A file only this user reads (0700 directories, 0600 file), replaced by one
// rename so an interruption never leaves a half-written document.
function writePrivateFile(file, contents) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(temporary, contents, { encoding: 'utf8', mode: 0o600 });
    fs.chmodSync(temporary, 0o600);
    fs.renameSync(temporary, file);
  } catch (error) {
    try { fs.unlinkSync(temporary); } catch {}
    throw error;
  }
}

/**
 * The two documents a DSH home needs before its first start.
 *
 * The profile patch is a top-level YAML array and JSON is a YAML subset, so a
 * fresh home needs no YAML writer here -- `ov-superagent dsh model repair` writes its
 * minimal document the same way. The provider profile comes from
 * `addModelOps`, the same builder the model menu's wizard uses, so the fields are
 * exactly the ones the flag form writes.
 * @param {object} draft - `{route, model, api, baseURL, name, keyEnv, keyValue}`.
 * @returns {{settings: string, credentials: string, keyRef: string}} the bytes.
 */
export function freshDshModelDocuments(draft) {
  const [{ value: profile }] = addModelOps({}, modelSpecFromDraft(draft));
  const keyRef = typeof profile.apiKeyEnv === 'string' ? profile.apiKeyEnv.trim() : '';
  const settings = `${JSON.stringify([{ id: DSH_MODEL_NAMESPACE, config: { providers: { [draft.route]: profile } } }])}\n`;
  // Version 1 of DSH's credentials document. JSON quoting is valid YAML, so a
  // value holding punctuation (`:`, `#`, `"`) survives byte for byte.
  const credentials = draft.keyValue && keyRef
    ? `version: 1\nrefs:\n  ${keyRef}: ${JSON.stringify(draft.keyValue)}\n`
    : '';
  return { settings, credentials, keyRef };
}

/**
 * Persist a draft route into an instance's own DSH home.
 *
 * A home DSH has never written is safe to seed directly -- there is no document
 * to merge with, and the directory may not be created yet (setup calls this
 * where it persists the instance, so cancelling leaves nothing behind). Once a
 * settings document exists it belongs to DSH, so the route goes in through
 * DSH's settings service, exactly the upsert the add wizard performs
 * (`addModelOps`: every other route and key is kept).
 * @param {object} options - `dataDir`, the draft, the dsh `bin` and `env`.
 * @returns {Promise<{via: 'file'|'service', settings: string, credentials: string|null, keyRef: string, notes: string[]}>}
 */
export async function writeDshModelDraft({ dataDir, draft, bin = 'dsh', env = process.env }) {
  const settings = dshSettingsDocument(dataDir);
  const credentials = dshCredentialsDocument(dataDir);
  if (fs.existsSync(settings) || fs.existsSync(credentials)) {
    const written = await writeModelDraft({ dataDir, draft, bin, env });
    return { via: 'service', settings, credentials: null, keyRef: written.keyRef, notes: written.notes };
  }
  const documents = freshDshModelDocuments(draft);
  // The value first: a route that lands without its credential would report
  // itself as unusable until somebody stored the key by hand.
  if (documents.credentials) writePrivateFile(credentials, documents.credentials);
  writePrivateFile(settings, documents.settings);
  return { via: 'file', settings, credentials: documents.credentials ? credentials : null, keyRef: documents.keyRef, notes: [] };
}

/**
 * Prove a draft before it is written anywhere the daemon reads.
 *
 * The draft is written into a throwaway DSH home and the same handshake the
 * runtime probe uses runs against it, so a wrong protocol, a malformed endpoint
 * or a model id DSH cannot serve is reported before anything is persisted. The
 * catalogue also resolves the credential, so a pasted key is checked; whether
 * the gateway itself accepts it can only be learned on the first turn.
 * @param {object} options - the dsh `bin`, `env`, the draft, and an injectable `probe`.
 * @returns {Promise<{ok: boolean, route: string, reason: string, note: string}>}
 */
export async function validateDshModelDraft({ bin, env = process.env, draft, probe = probeDsh }) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-dsh-model-'));
  fs.chmodSync(dataDir, 0o700);
  try {
    const documents = freshDshModelDocuments(draft);
    writePrivateFile(dshSettingsDocument(dataDir), documents.settings);
    if (documents.credentials) writePrivateFile(dshCredentialsDocument(dataDir), documents.credentials);
    const result = await probe(bin, { env, dataDir });
    if (!result.compatible) return { ok: false, route: '', reason: result.reason || 'DSH did not answer', note: '' };
    const routes = Array.isArray(result.routes) ? result.routes : [];
    const served = routes.some((route) => route.provider === draft.route && route.models.includes(draft.model));
    return served
      ? { ok: true, route: canonicalModelId(draft), reason: '', note: '' }
      : { ok: false, route: result.route ?? '', reason: '', note: result.note ?? '' };
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

// The route id a gateway host suggests: `api.example.com` -> `example`,
// `192.0.2.10` -> `gateway` (an address is not a name).
// Public suffixes whose last label is shared by many registries. Taking the
// second-to-last label would turn every `.com.cn` gateway into `com`, so two
// unrelated registries collide on one route id -- and that id is also the model
// id prefix the operator types.
const TWO_LABEL_SUFFIXES = new Set([
  'co.uk', 'com.cn', 'com.au', 'co.jp', 'co.kr', 'com.br', 'com.mx', 'com.sg',
  'com.hk', 'com.tw', 'co.in', 'co.nz', 'co.za', 'com.tr', 'com.sa', 'co.il',
  'org.uk', 'net.cn', 'org.cn', 'gov.cn', 'edu.cn',
]);

export function routeFromGateway(baseURL) {
  let host = '';
  try { host = new URL(String(baseURL)).hostname; } catch { return 'gateway'; }
  host = host.replace(/^\[|\]$/g, '');
  if (!host || /^[0-9.]+$/.test(host) || host.includes(':')) return 'gateway';
  const labels = host.split('.').filter(Boolean);
  const tail = labels.slice(-2).join('.').toLowerCase();
  // The label that names the registry, not the suffix: `api.example.co.uk` is
  // `example`, and a host that is only a suffix (`co.uk`) has none, so the
  // leftmost label stands in rather than an empty id.
  const candidate = labels.length > 2 && TWO_LABEL_SUFFIXES.has(tail)
    ? labels[labels.length - 3]
    : labels.length > 1 ? labels[labels.length - 2] : labels[0];
  const slug = String(candidate ?? '').toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, '');
  return slug || 'gateway';
}

function baseUrlProblem(value) {
  try {
    const { protocol } = new URL(String(value));
    if (protocol === 'http:' || protocol === 'https:') return null;
  } catch {}
  return t('要写成 http(s) 地址，例如 https://api.deepseek.com/v1',
    'Expected an http(s) URL, e.g. https://api.deepseek.com/v1');
}

// What each wire protocol is, in the operator's terms. A bare list of three
// identifiers says nothing to someone who has never written a provider profile,
// so the menu carries the explanation the way hermes' does.
const PROTOCOL_OPTIONS = Object.freeze([
  {
    value: 'openai-completions',
    label: t('OpenAI 兼容（/v1/chat/completions）', 'OpenAI-compatible (/v1/chat/completions)'),
    hint: t('绝大多数自建网关都是这个', 'what nearly every self-hosted gateway speaks'),
  },
  {
    value: 'openai-responses',
    label: t('OpenAI Responses（/v1/responses）', 'OpenAI Responses (/v1/responses)'),
    hint: t('Codex 一类的后端', 'Codex-style backends'),
  },
  {
    value: 'anthropic-messages',
    label: t('Anthropic Messages（/v1/messages）', 'Anthropic Messages (/v1/messages)'),
    hint: t('Claude 兼容的网关', 'Claude-compatible gateways'),
  },
]);

/**
 * The protocol a gateway most likely speaks, from its URL alone.
 *
 * This mirrors the heuristics hermes applies before asking: the default has to
 * be right for the endpoint people actually paste, because pressing Enter is
 * the normal answer. An unknown URL is the OpenAI-compatible case, which is
 * what almost every self-hosted gateway serves.
 * @param {string} baseURL - the gateway address.
 * @returns {string} one of {@link PROTOCOLS}.
 */
export function guessProtocol(baseURL) {
  let url;
  try { url = new URL(String(baseURL)); } catch { return 'openai-completions'; }
  const host = url.hostname.toLowerCase();
  const path = `${url.pathname}${url.search}`.toLowerCase();
  if (host === 'api.anthropic.com' || host.includes('anthropic')) return 'anthropic-messages';
  if (/(^|\/)anthropic(\/v1)?\/?$/.test(path)) return 'anthropic-messages';
  if (host === 'api.openai.com' || host === 'api.x.ai') return 'openai-responses';
  if (/(^|\/)(responses|codex)(\/|$)/.test(path)) return 'openai-responses';
  return 'openai-completions';
}

// The protocol question: every option says what it is, and the default comes
// from the URL just typed.
async function askProtocol({ prompter, baseURL, seed }) {
  const detected = seed?.api && PROTOCOLS.includes(seed.api) ? seed.api : guessProtocol(baseURL);
  const initial = Math.max(0, PROTOCOL_OPTIONS.findIndex((option) => option.value === detected));
  return prompter.select(t('protocol', 'Protocol'), PROTOCOL_OPTIONS.map((option) => ({ ...option })), {
    initial,
    label: t('protocol', 'Protocol'),
  });
}

// Two gateway addresses name the same entry when they differ only by trailing
// slashes: `add` is an upsert keyed by route, so this decides whether a second
// run updates the entry the first one made.
function sameGateway(left, right) {
  const normalize = (value) => String(value ?? '').trim().replace(/\/+$/, '').toLowerCase();
  return normalize(left) === normalize(right);
}

// Settle the route id the gateway address implies.
//
// One implementation of the "re-adding the same URL updates it" rule: a route
// that already points at this URL is reused (the settings write is an upsert,
// so nothing is duplicated), and a route that points elsewhere is only
// repointed after the operator says so. Declining asks for another route id
// instead of silently overwriting another model's endpoint.
async function settleRoute({ prompter, baseURL, route, known }) {
  for (;;) {
    const existing = known.find((entry) => entry.route === route);
    if (!existing) return route;
    if (sameGateway(existing.baseURL, baseURL)) {
      prompter.info(t(`route ${route} 已经存在并指向这个地址：这次只更新它包含的 model，不会新建一个。`,
        `Route ${route} already exists at this URL: its models are updated in place, no second route is added.`));
      return route;
    }
    const current = existing.baseURL || t('（没有声明地址）', 'no declared base URL');
    prompter.warn(t(`route ${route} 现在指向 ${current}，继续会把它改成 ${baseURL}`,
      `Route ${route} currently points at ${current}; continuing would repoint it to ${baseURL}`));
    if (await prompter.confirm(t(`把 ${route} 改到新地址？`, `Repoint ${route} to the new URL?`), false)) return route;
    route = await prompter.text(t('换一个 route id', 'Another route id'), {
      label: t('route id', 'Route id'),
      initial: '',
      validate: (value) => routeIdProblem(value),
    });
  }
}

// What is about to be written, in the operator's terms, right before the
// question that persists it. The route is marked as derived because nobody was
// asked for it.
function showDraft(prompter, draft, { derived }) {
  const credential = draft.keyValue
    ? `${draft.keyEnv}${t('（现在粘贴了值）', ' (a value was pasted now)')}`
    : `${draft.keyEnv}${t('（只写 reference，稍后用 key set 存值）', ' (reference only; store the value later with key set)')}`;
  prompter.write([
    t('将写入：', 'About to write:'),
    `  ${t('route', 'route')}    ${draft.route}${derived ? t('（从网关地址推导）', ' (derived from the gateway URL)') : ''}`,
    `  ${t('model', 'model')}    ${canonicalModelId(draft)}`,
    `  ${t('protocol', 'protocol')}  ${draft.api}`,
    `  ${t('网关', 'gateway')}   ${draft.baseURL}`,
    `  ${t('credential', 'credential')} ${credential}`,
  ].join('\n') + '\n');
}

/**
 * The add-model questions, in the order hermes asks them: the endpoint first,
 * then the protocol (with the URL's guess as the default), the model id and the
 * credential. Nothing is written here.
 *
 * The route id is not a question: it is only the local provider key inside
 * DSH's document and the prefix of the model id, so it is derived from the
 * gateway host. It is shown in the summary before the write, and the only field
 * the operator can correct there is that route -- everything else is a rerun.
 *
 * `seed` pre-answers the fields a caller already knows (`--base-url`, `--api`,
 * `--name`, `--key-env` on the interactive `add`); each question is still
 * asked, with the seeded value as its default. `known` is the instance's
 * current routes, used to update in place instead of duplicating a gateway.
 * @param {object} options - `prompter`, an optional `seed`, and `known` routes.
 * @returns {Promise<object>} the draft.
 */
export async function askDshModelDraft({ prompter, seed = null, known = [] }) {
  const baseURL = await prompter.text(t('网关地址（例如 https://api.deepseek.com/v1）', 'Gateway base URL (e.g. https://api.deepseek.com/v1)'), {
    label: t('网关地址', 'Gateway URL'),
    initial: seed?.baseURL || 'http://127.0.0.1:8080/v1',
    validate: (value) => baseUrlProblem(value),
  });
  const api = await askProtocol({ prompter, baseURL, seed });
  // Editing an existing entry seeds its route: the route is a local key with no
  // relation to the URL, so re-deriving it would move the model to another
  // route (or invent one) when only the endpoint changed.
  const derivedRoute = routeFromGateway(baseURL);
  let route = await settleRoute({ prompter, baseURL, route: seed?.route || derivedRoute, known });
  let model = await prompter.text(t('model id（可以带 /）', 'Model id (may contain /)'), {
    label: t('model id', 'Model id'),
    initial: seed?.model || '',
    validate: (value) => modelIdProblem(route, value),
  });
  // The reference follows the route that was actually settled on: a repoint
  // declined at the dedupe step may have handed out a different route id. A route
  // that already resolves a reference keeps it -- an imported route may hold
  // `_2` after a rotation, and re-deriving `_1` here would silently change which
  // secret the route sends (and leave it unconfigured when only `_2` has a value).
  const existingRef = known.find((entry) => entry.route === route)?.keyEnv ?? null;
  const namedRef = seed?.keyEnv || existingRef || deriveKeyRef(route);
  const credential = await prompter.select(t('credential', 'Credential'), [
    { label: t('现在粘贴密钥（写进实例的 DSH credential 库）', 'Paste the key now (stored in the instance’s DSH credential store)'), value: 'paste' },
    { label: t(`只写 reference ${namedRef}，稍后用 key set 存值`, `Only name the reference ${namedRef}; store its value later with key set`), value: 'ref' },
  ], { initial: 0, label: t('credential', 'Credential') });
  let keyValue = null;
  let keyEnv = namedRef;
  let keyRefDerived = !seed?.keyEnv;
  if (credential === 'paste') {
    keyValue = String(await prompter.secret(t('密钥值（不会回显）', 'Key value (not echoed)')) ?? '').trim() || null;
  } else {
    keyEnv = await prompter.text(t('credential reference', 'Credential reference'), {
      label: t('credential reference', 'Credential reference'),
      initial: namedRef,
      validate: (value) => credentialRefProblem(value),
    });
    keyRefDerived = keyRefDerived && keyEnv === namedRef;
  }
  // The summary is the last question. "No" is not "start over": it is the one
  // place the field the tool guessed can be corrected. Adding fresh, that field
  // is the derived route; editing, the route was not guessed, so the field worth
  // a second thought is the model id (a rename on the same route).
  const fixedName = seed?.name || null;
  let routeIsDerived = !seed?.route && route === derivedRoute;
  for (;;) {
    const draft = { route, model, api, baseURL, name: fixedName || route, keyEnv, keyValue };
    showDraft(prompter, draft, { derived: routeIsDerived });
    if (await prompter.confirm(t('按这样写入？', 'Write it this way?'), true)) return draft;
    if (seed?.route) {
      prompter.info(t('这里只能改 model id；其他项重新运行一次命令即可。',
        'The model id is the only field to correct here (a rename inside the same route); rerun the command for anything else.'));
      model = await prompter.text(t('model id', 'Model id'), {
        label: t('model id', 'Model id'), initial: model, validate: (value) => modelIdProblem(route, value),
      });
      continue;
    }
    prompter.info(t('这里只能改 route id；其他项重新运行一次命令即可。',
      'The route id is the only field to correct here (a local key, also the model id prefix); rerun the command for anything else.'));
    const corrected = await prompter.text(t('route id', 'Route id'), {
      label: t('route id', 'Route id'),
      initial: route,
      validate: (value) => routeIdProblem(value),
    });
    route = await settleRoute({ prompter, baseURL, route: corrected, known });
    routeIsDerived = !seed?.route && route === derivedRoute;
    if (keyRefDerived) keyEnv = deriveKeyRef(route);
    const problem = modelIdProblem(route, model);
    if (problem) {
      prompter.warn(problem);
      model = await prompter.text(t('model id', 'Model id'), {
        label: t('model id', 'Model id'), initial: model, validate: (value) => modelIdProblem(route, value),
      });
    }
  }
}

/**
 * Prove the draft in a throwaway home, and say what that proves.
 *
 * A route whose credential is only a reference and whose value is not in this
 * environment cannot answer yet; the message says so instead of claiming the
 * gateway works. A failure is not fatal: the caller decides whether to keep the
 * draft anyway (setup and the interactive `add` both ask).
 * @param {object} options - `prompter`, the dsh `bin`/`env`, the draft, and the
 *   injectable `validate` (defaults to {@link validateDshModelDraft}).
 * @returns {Promise<{ok: boolean, route: string, reason: string, note: string}>}
 */
export async function verifyDshModelDraft({ prompter, validate = validateDshModelDraft, bin = 'dsh', env = process.env, draft }) {
  const spinning = prompter.spin(t('用临时 DSH 检查这个 route', 'Checking the route against a temporary DSH'));
  let result;
  try {
    result = await validate({ bin, env, draft });
  } catch (error) {
    result = { ok: false, reason: error.message, note: '' };
  }
  if (result.ok) {
    spinning.succeed(t(`DSH 接受了这个 route，并给出 model ${result.route}`,
      `DSH accepted the route and lists the model ${result.route}`));
    return result;
  }
  spinning.fail(t('这个 route 现在还不能服务 model', 'The route cannot serve a model yet'));
  const why = [result.reason, result.note].filter(Boolean).join('；');
  prompter.warn(t(`临时 DSH 没有给出 ${draft.route}/${draft.model}${why ? `（${why}）` : ''}`,
    `The temporary DSH did not offer ${draft.route}/${draft.model}${why ? ` (${why})` : ''}`));
  if (!draft.keyValue) {
    prompter.info(t(`reference ${draft.keyEnv} 现在没有值：DSH 要能解析它才会列出 model。可以先用 ov-superagent dsh model key set ${draft.keyEnv} 存值，或者保存后在实例里再存。`,
      `The reference ${draft.keyEnv} has no value yet: DSH lists the model only once it can resolve it. Store one with ov-superagent dsh model key set ${draft.keyEnv}, or after saving.`));
  }
  return result;
}

/**
 * Ask for a draft, check it, and let the operator keep it when the check fails.
 *
 * The two entry points share this so "ask, verify, keep anyway?" is the same
 * sequence everywhere; only what happens to an accepted draft differs (setup
 * keeps it in memory, the model command writes it). `onDecline` is how the
 * caller says what to run instead -- setup prints the command, the model
 * command just goes back to its menu.
 * @param {object} options - `prompter`, `bin`, `env`, `seed`, `validate`, and `onDecline`.
 * @returns {Promise<object|null>} the accepted draft, or null when skipped.
 */
export async function collectDshModelDraft({
  prompter, bin = 'dsh', env = process.env, seed = null, known = [], validate = validateDshModelDraft, onDecline = null,
}) {
  const draft = await askDshModelDraft({ prompter, seed, known });
  const checked = await verifyDshModelDraft({ prompter, validate, bin, env, draft });
  if (!checked.ok && !await prompter.confirm(t('仍然保存这个 route？', 'Keep this route anyway?'), true)) {
    onDecline?.(draft);
    return null;
  }
  return draft;
}
