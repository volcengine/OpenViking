// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The writing half of `vika dsh model`: everything that spawns DSH or touches a
// file on disk.
//
// The only source of truth is DSH's own `llm-pi-ai` settings document. Nearly
// every path here edits it through the bridge of a short-lived DSH process
// (dsh-control.mjs), so the service that owns the file is the one writer and a
// second writer cannot race it. The exception is `repair`, whose helpers below
// back that document up and rewrite it directly, and only after a successful
// probe has shown the settings service cannot read it at all.
//
// This is the one file to audit for "who can touch the document": if a change
// can create, rewrite or back up a file under an instance's DSH home, it belongs
// here, not in the command surface.

import fs from 'node:fs';
import path from 'node:path';
import { t } from './i18n.mjs';
import { CONTROL_PATCH_FILE, DshControlError, withDshControl } from '../../daemon/src/dsh-control.mjs';
import { addModelOps, credentialRefsOf, plainObject, text } from './dsh-model-plan.mjs';

// The credential document of one instance. It sits in DSH's home beside the
// settings document, and DSH's own credentials service is the only writer.
function credentialsDocumentPath(agent) {
  return path.join(agent.dataDir, 'dsh', 'home', '.credentials.yaml');
}

/**
 * The references the instance's credential store already holds a value under.
 *
 * An absent document is a fresh instance and answers with an empty set. Any
 * other read failure is *not* "nothing is stored": continuing would let the
 * migration pick a name whose value it cannot see, so it fails closed and writes
 * nothing.
 * @param {object} agent - the resolved instance.
 * @returns {Set<string>} reference names.
 */
export function storedCredentialRefs(agent) {
  const file = credentialsDocumentPath(agent);
  let contents;
  try {
    contents = fs.readFileSync(file, 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') return new Set();
    throw new Error(t(
      `读不了 credential 库 ${file}（${error.message}），因此无法确认哪些 reference 已经有值；这次什么都没有写入。\n`
      + '请修好它的权限后重试。',
      `Cannot read the credential store ${file} (${error.message}), so it cannot be shown which references already hold a value; nothing was written.\n`
      + 'Fix its permissions and retry.'));
  }
  return credentialRefsOf(contents);
}

// DSH's binary, resolved the way the daemon resolves it: the operator
// environment first, the deployment manifest second
// (deployment-config.cjs `resolvedDeploymentDaemonEnv`). `DSH_BIN` is a
// deployment fact, so the manifest is a legitimate home for it — a machine whose
// `dsh` is not on PATH pins it there. Nothing else from `deployment.env` is
// merged into the child: the daemon applies that env to itself, never to the DSH
// process, and a second source here would edit a document with a binary the
// instance does not run.
export function dshBin(env, deployment) {
  for (const candidate of [env.DSH_BIN, deployment?.env?.DSH_BIN]) {
    const value = text(candidate);
    if (value) return value;
  }
  return 'dsh';
}

// Every command except `import --dry-run` edits one instance through one
// short-lived DSH process; this is the only place that knows how to start it.
export function withControl({ agent, deployment, env }, run) {
  return withDshControl({
    dataDir: agent.dataDir,
    bin: dshBin(env, deployment),
    env,
  }, run);
}

export async function describeProviders(client) {
  const { descriptor } = await client.describeSettings();
  if (!descriptor) {
    // v2.1 answers `descriptor: null` (not an error) when the settings
    // document cannot be read, which is the state `repair` exists for.
    throw new Error(t(
      'DeepSeek Harness 读不到可写的 llm-pi-ai 配置项：文档可能被手工写坏，或 model 插件被禁用。\n先运行 vika dsh model repair 尝试修复。',
      'DeepSeek Harness cannot read a writable llm-pi-ai entry: the document may be hand-broken, or the model plugin is disabled.\nRun vika dsh model repair first.'));
  }
  // The resolved value is the base for an upsert: it already carries every
  // schema default, so merging into it keeps fields this command never names.
  return {
    descriptor,
    providers: plainObject(descriptor.value?.providers) ? descriptor.value.providers : {},
  };
}

export function isConcurrentWrite(error) {
  // Bridge v2.1 names the taxonomy in `data.code`; the message match is the
  // fallback for a v2 bridge, where the numeric code is all that survives.
  if (error?.data?.code === 'SETTINGS_CONFLICT') return true;
  // `no longer configurable` is deliberately absent: the bridge classifies that
  // text as SETTINGS_NOT_CONFIGURABLE, a state retrying cannot fix. Matching it
  // here would spend the one retry on it before the real instruction was shown.
  return /no longer available|changed since it was read|SETTINGS_CONFLICT/i.test(String(error?.message ?? ''));
}

// The three refusals a settings write can answer with, each with the action
// that actually fixes it. Anything else is passed through unchanged.
export function settingsFailure(error, { dataDir }) {
  const message = String(error?.message ?? error);
  // v2.1 answers with `data.code`; classify on it when it is there and fall
  // back to the message for an older bridge, which forwards only the numeric
  // JSON-RPC code. The message is always what the user sees.
  const code = error?.data?.code;
  const overridden = code === 'SETTINGS_OVERRIDDEN'
    || /overridden by a home patch or command-line overlay/i.test(message);
  const notConfigurable = code === 'SETTINGS_NOT_CONFIGURABLE'
    // The text fallback mirrors the bridge's own classification for a v2 frame,
    // which carries no `data.code`: these messages mean the entry cannot be
    // written at all, never that another writer won a race. `no longer available`
    // is deliberately absent -- that one is the concurrency class.
    || /No configurable plugin entry|no longer configurable|is not configurable|is no longer active|no volatile fields/i.test(message);
  const invalid = code === 'SETTINGS_INVALID';
  if (overridden) {
    // DSH composes bundle patches, then the profile document it writes routes
    // into, then the home patch (<DSH_HOME>/cordis.patch.yml), then launch-time
    // `--patch` overlays (packages/boot/app-boot/src/profile-context.ts:74-87 in
    // the DSH source). An override therefore comes from one of the last two, and
    // pointing at the profile document would tell the operator to delete exactly
    // the entry the routes live in.
    const routeDocument = path.join(dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
    const homePatch = path.join(dataDir, 'dsh', 'home', 'cordis.patch.yml');
    const overlay = path.join(dataDir, 'dsh', 'run', CONTROL_PATCH_FILE);
    return new Error(t(
      `实例的 home patch 覆盖了 llm-pi-ai，DSH 拒绝写入。请删掉这两处里的 llm-pi-ai 段后重试：\n`
      + `  ${homePatch}（home patch）\n  ${overlay}（启动时的 --patch 覆盖层）\n`
      + `${routeDocument} 是这次要写的 route 文档本身，不是覆盖来源：删它等于删掉全部 route。`,
      `The instance’s home patch overrides llm-pi-ai, so DSH refuses the write. Remove the llm-pi-ai entry from one of these and retry:\n`
      + `  ${homePatch} (the home patch)\n  ${overlay} (the --patch launch overlay)\n`
      + `${routeDocument} is the route document this command writes, not the source of the override: deleting its entry would drop every route.`));
  }
  if (notConfigurable) {
    return new Error(t(
      `DeepSeek Harness 没有可写的 llm-pi-ai 配置项（可能被禁用或文档被手工写坏）。运行 vika dsh model repair 尝试修复。\n${message}`,
      `DeepSeek Harness exposes no writable llm-pi-ai entry (it may be disabled, or the document was hand-edited). Run vika dsh model repair to recover.\n${message}`));
  }
  if (invalid) {
    return new Error(t(`DSH 拒绝了这次配置：${message}`, `DSH refused this configuration: ${message}`));
  }
  if (isConcurrentWrite(error)) {
    return new Error(t(
      '配置在读取后被其他进程改写，重试一次仍然冲突；请重新运行命令。',
      'The configuration changed after it was read and still conflicted on retry; run the command again.'));
  }
  return error;
}

// Read → build → write, with one retry when a concurrent writer won the race.
// The retry re-reads, so the operations are rebuilt against the new revision
// instead of replaying a stale draft.
//
// `buildOps` must be a function of the state it is handed. A closure that
// ignores its argument and returns a plan captured earlier turns the retry into
// a replay: the conflict disappears, the concurrent write is overwritten, and
// the command reports success. The migration needs the values written between
// the read and the mutate, so it drives this same read/build/write loop itself
// (`runImport`) rather than through this helper.
export async function updateSettings(client, buildOps, { dataDir }) {
  for (let attempt = 0; ; attempt += 1) {
    const state = await describeProviders(client);
    const ops = buildOps(state);
    if (ops.length === 0) return { descriptor: state.descriptor, ops };
    try {
      await client.mutateSettings(ops, state.descriptor.revision);
      return { descriptor: state.descriptor, ops };
    } catch (error) {
      if (attempt === 0 && isConcurrentWrite(error)) continue;
      throw settingsFailure(error, { dataDir });
    }
  }
}

/**
 * Write one model route into a DSH home through DSH's own settings service.
 *
 * This is the write `vika setup` uses for an instance whose DSH home already
 * exists: the settings service owns that document, so the merge into the
 * existing `providers` map is the same upsert `add` performs (`addModelOps`),
 * never a second YAML writer. A literal credential captured by the caller is
 * stored under the reference the route ends up resolving; a route that names a
 * reference with no value is written as-is and DSH reports it as unusable
 * until one is stored.
 * @param {object} options
 * @param {string} options.dataDir - the instance data directory (its DSH home).
 * @param {object} options.draft - `{route, model, api, baseURL, name, keyEnv, keyValue}`.
 * @param {string} [options.bin] - the dsh executable.
 * @param {object} [options.env] - environment to pass on.
 * @param {Function} [options.spawnImpl] - injectable spawn for tests.
 * @returns {Promise<{profile: object, keyRef: string, notes: string[]}>} what was written.
 */
/**
 * The spec a wizard draft translates into.
 *
 * Every field the wizard accepts travels with the draft: it asks only the
 * setup questions, but a caller that already knows `--input`, a size or
 * `--no-reasoning` must not have them dropped on the way to `addModelOps`. The
 * setup wizard's fresh-home seeding uses this too, so a directly seeded
 * document and a service write cannot disagree about the fields.
 * @param {object} draft - `{route, model, api, baseURL, name, keyEnv, input, ...}`.
 * @returns {object} the spec `addModelOps` takes.
 */
export function modelSpecFromDraft(draft) {
  return {
    route: draft.route,
    model: draft.model,
    api: draft.api,
    baseURL: draft.baseURL,
    keyEnv: draft.keyEnv ?? null,
    name: draft.name ?? null,
    input: Array.isArray(draft.input) && draft.input.length ? [...draft.input] : null,
    contextWindow: Number.isSafeInteger(draft.contextWindow) ? draft.contextWindow : null,
    maxTokens: Number.isSafeInteger(draft.maxTokens) ? draft.maxTokens : null,
    noReasoning: draft.noReasoning === true,
  };
}

export async function writeModelDraft({ dataDir, draft, bin = 'dsh', env = process.env, spawnImpl }) {
  const spec = modelSpecFromDraft(draft);
  const notes = [];
  const options = { dataDir, bin, env };
  if (spawnImpl) options.spawnImpl = spawnImpl;
  let profile = null;
  await withDshControl(options, async (client) => {
    profile = await writeRouteWithCredential({
      client,
      dataDir,
      spec,
      notes,
      keyValue: draft.keyValue,
      build: (state) => addModelOps(state.providers, spec, { notes }),
    });
  });
  return { profile, keyRef: text(profile?.apiKeyEnv), notes };
}

/**
 * One route write, with its credential stored first.
 *
 * The order is the import path's invariant I2 applied to the interactive path:
 * at the instant the route lands, the reference it names already holds its
 * value. Writing it the other way round leaves a route pointing at a reference
 * with nothing behind it whenever the credential write is refused -- the
 * instance then looks configured and serves nothing, and `vika setup` reports a
 * failure while the route stays in the document.
 *
 * A refused credential therefore leaves the document untouched. A refused
 * settings write removes the value this call stored, but only when it created
 * that reference: an unconfirmed write is kept, because it may have landed.
 * @returns {Promise<object|null>} the written route profile, or null.
 */
async function writeRouteWithCredential({ client, dataDir, spec, notes, keyValue, build }) {
  const state = await describeProviders(client);
  const ops = build(state);
  if (ops.length === 0) return null;
  const profile = ops[0].value;
  const ref = text(profile?.apiKeyEnv);
  const wanted = text(keyValue);
  if (wanted && ref) {
    // Refused here means nothing was written at all: the route write has not
    // been attempted yet.
    await client.setCredential(ref, wanted);
  }
  try {
    await client.mutateSettings(ops, state.descriptor.revision);
  } catch (error) {
    if (isConcurrentWrite(error)) {
      // Another writer moved the document between the read and the write. The
      // value is already in place and is the reference this attempt allocated,
      // so it is kept and the caller sees the conflict.
      throw settingsFailure(error, { dataDir });
    }
    if (wanted && ref && !routeStillNames(state.providers, ref)) {
      // This attempt is what put the value there and no route resolves it, so
      // removing it restores the state before the call. A failure to remove it
      // is reported: a stray value is not a write, but it is not nothing either.
      try {
        await client.unsetCredential(ref);
      } catch (cleanupError) {
        notes.push(t(
          `credential reference ${ref} 已写入但 route 未写成，且清理失败（${cleanupError.message}）；用 vika dsh model key unset ${ref} 移除它`,
          `The reference ${ref} was stored but the route was not written, and removing it failed (${cleanupError.message}); remove it with vika dsh model key unset ${ref}`));
      }
    }
    throw settingsFailure(error, { dataDir });
  }
  return profile;
}

// Whether any route already resolves a reference, the test for "this call
// created it" in `writeRouteWithCredential`.
function routeStillNames(providers, ref) {
  return Object.values(plainObject(providers) ? providers : {})
    .some((profile) => text(profile?.apiKeyEnv) === ref);
}

/**
 * Update one existing entry in place, including a rename of its model id.
 *
 * The write is the same upsert `writeModelDraft` performs, so every field the
 * edit does not name survives. A rename is one extra step inside the same
 * `set`: `addModelOps` merges the new id into the route's model list, and the
 * old id is filtered out of that list before the op is sent, so the document
 * ends up with the new id only -- no stale duplicate, no second `mutate`.
 * @param {object} options - `dataDir`, the edited draft, the previous
 *   `{route, model}`, the dsh `bin` and `env`.
 * @returns {Promise<{profile: object|null, keyRef: string, notes: string[]}>}
 */
export async function writeModelEdit({ dataDir, draft, previous, bin = 'dsh', env = process.env, spawnImpl }) {
  const spec = modelSpecFromDraft(draft);
  const notes = [];
  const options = { dataDir, bin, env };
  if (spawnImpl) options.spawnImpl = spawnImpl;
  let profile = null;
  await withDshControl(options, async (client) => {
    // The rename is one extra step inside the same `set`: `addModelOps` merges
    // the new id into the route's model list, and the old id is filtered out of
    // that list before the op is sent, so the document ends up with the new id
    // only -- no stale duplicate, no second `mutate`.
    profile = await writeRouteWithCredential({
      client,
      dataDir,
      spec,
      notes,
      keyValue: draft.keyValue,
      build: (state) => {
        const operations = addModelOps(state.providers, spec, { notes });
        if (previous?.model && previous.model !== spec.model) {
          // The rename replaces the entry, so everything the wizard does not ask
          // about (compat, reasoningEfforts, headers, a name DSH materialized) has
          // to be carried across by hand -- otherwise an edit that only changes
          // the id silently strips the model's wire configuration.
          const old = (state.providers?.[spec.route]?.models ?? [])
            .find((entry) => entry?.id === previous.model);
          for (const op of operations) {
            if (op.op !== 'set' || op.path[0] !== 'providers' || op.path[1] !== spec.route) continue;
            // The new entry holds what the draft named; the old one supplies the
            // rest. `reasoningEfforts: false` from the draft must win over an
            // old map, so this merge order is deliberate.
            op.value.models = (op.value.models ?? []).map((entry) => (entry?.id === spec.model && old
              ? { ...old, ...entry, id: spec.model }
              : entry)).filter((entry) => entry?.id !== previous.model);
          }
        }
        return operations;
      },
    });
  });
  return { profile, keyRef: text(profile?.apiKeyEnv), notes };
}

// What the rewrite is about to throw away, so the one destructive step in this
// command group says so before it happens. The document is read as text, never
// through a YAML parser (this CLI has no YAML dependency and repair must work on
// a document that parser would refuse): JSON is tried first because it is a
// YAML subset and the minimal document itself is JSON, then a line scan counts
// the direct keys under `providers:` at the first level deeper than it.
// @returns {number|null} the route count, or null when the document's shape is
//   not recognizable -- the caller then reports its size instead of a number it
//   would have had to guess.
function discardedRouteCount(contents) {
  if (contents === null) return 0;
  const textValue = Buffer.isBuffer(contents) ? contents.toString('utf8') : String(contents ?? '');
  const parsed = jsonRouteCount(textValue);
  if (parsed !== null) return parsed;
  return yamlRouteCount(textValue);
}

function providerKeys(providers) {
  return plainObject(providers) ? Object.keys(providers) : null;
}

// The count of routes under `llm-pi-ai`, or null when this document's shape is
// not one this reader understands. "Understood but no llm-pi-ai entry" is 0: the
// rewrite discards nothing, which is a fact, not a guess.
function jsonRouteCount(textValue) {
  let parsed;
  try { parsed = JSON.parse(textValue); } catch { return null; }
  const rows = Array.isArray(parsed) ? parsed : [parsed];
  if (rows.length === 0 || rows.some((row) => !plainObject(row) || typeof row.id !== 'string')) return null;
  const entry = rows.find((row) => row.id === 'llm-pi-ai');
  if (!entry) return 0;
  return providerKeys(entry.config?.providers)?.length ?? 0;
}

function yamlRouteCount(textValue) {
  const lines = textValue.split('\n');
  const indentOf = (line) => line.length - line.trimStart().length;
  // One entry row per document is the shape DSH writes; anything else is left to
  // the byte-count fallback rather than reported as a wrong number.
  if (!lines.some((line) => /^\s*-\s*id:\s*\S+\s*$/.test(line))) return null;
  const entry = lines.findIndex((line) => /^\s*-\s*id:\s*llm-pi-ai\s*$/.test(line));
  if (entry < 0) return 0;
  const entryIndent = indentOf(lines[entry]);
  // Any `providers:` under the entry, in any spelling, so the flow form below can
  // be told apart from "there is no providers key at all".
  const anyProvidersAt = lines.findIndex((line, index) => index > entry
    && indentOf(line) > entryIndent
    && /^\s*providers:/.test(line));
  if (anyProvidersAt < 0) return 0;
  const providersAt = lines.findIndex((line, index) => index > entry
    && indentOf(line) > entryIndent
    && /^\s*providers:\s*(\{\s*\})?\s*$/.test(line));
  if (providersAt < 0) {
    // A `providers:` that this reader cannot scan (`providers: {a: ..., b: ...}` on
    // one line) is unknown, not zero: repair prints this count immediately before
    // the one destructive step in the command group, so it must never under-count
    // -- "0 routes will be discarded" next to a rewrite that drops three is worse
    // than not naming a number.
    return null;
  }
  // `providers: {}` on one line is an answer of zero, not an unknown.
  if (/\{\s*\}\s*$/.test(lines[providersAt])) return 0;
  const providersIndent = indentOf(lines[providersAt]);
  let childIndent = null;
  const routes = new Set();
  for (const line of lines.slice(providersAt + 1)) {
    if (!line.trim() || line.trimStart().startsWith('#')) continue;
    const indent = indentOf(line);
    if (indent <= providersIndent) break;
    childIndent ??= indent;
    if (indent !== childIndent) continue;
    const match = /^\s*([^\s:#][^:]*):/.exec(line);
    if (match) routes.add(match[1].trim());
  }
  return routes.size;
}

// The line repair prints before it rewrites: the minimal document carries no
// provider section, so every route in the backup leaves the menu with it. A
// document whose shape this reader does not recognize is reported by size rather
// than by a number it would have had to guess.
export function discardedNoteFor(contents) {
  const discarded = discardedRouteCount(contents);
  if (discarded === null) {
    return t(
      `将丢弃原文档中的全部 route（${contents.length} 字节；无法识别其结构，故未计数）\n`,
      `This discards every route in the original document (${contents.length} bytes; its shape was not recognizable, so no count)\n`);
  }
  return t(
    `将丢弃 ${discarded} 个 route（最小文档不含 providers 段）\n`,
    `This discards ${discarded} route(s) (the minimal document has no providers section)\n`);
}

// The DSH profile document that owns llm-pi-ai. `repair` is the only writer:
// every other path goes through DSH's own settings service, so a second writer
// never races it.
export function settingsDocumentPath(agent) {
  return path.join(agent.dataDir, 'dsh', 'home', 'profiles', 'sdk', 'cordis.patch.yml');
}

// The bytes of the document, or null when it does not exist yet. A permission
// error is not "absent": repair must not guess about a file it cannot read.
export function readFileIfExists(file) {
  try {
    return fs.readFileSync(file);
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

// The backup carries the exact bytes the probe described, not whatever the file
// holds a moment later, and `wx` refuses to clobber a backup that appeared
// concurrently.
export function backupFile(file, contents) {
  const stamp = new Date().toISOString().replace(/[^0-9]/g, '').slice(0, 14);
  for (let index = 0; ; index += 1) {
    const candidate = `${file}.bak-${stamp}${index === 0 ? '' : `-${index}`}`;
    try {
      fs.writeFileSync(candidate, contents, { mode: 0o600, flag: 'wx' });
      fs.chmodSync(candidate, 0o600);
      return candidate;
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
    }
  }
}

// JSON is a YAML subset, so the recovery document needs no YAML dependency:
// a bare entry row restores the address without claiming any provider, and the
// settings service fills it on the next tracked write.
const MINIMAL_SETTINGS_DOCUMENT = '[{"id":"llm-pi-ai"}]\n';

// The rewrite is one atomic replace: a temporary file in the same directory,
// fsynced so a crash cannot expose a short document, then renamed over the old
// one. A failure at any point leaves either the old document or the new one,
// never a half-written file, and the temporary is removed on the way out.
//
// `expected` is the document as the probe saw it (null: absent). It is checked
// once more immediately before the rename, so a concurrent writer is never
// silently overwritten: repair refuses and says so instead.
export function writeMinimalSettingsDocument(file, { expected = undefined } = {}) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  if (expected !== undefined) {
    const current = readFileIfExists(file);
    const same = expected === null ? current === null : current !== null && current.equals(expected);
    if (!same) {
      throw new Error(t(
        `设置文档在修复过程中被改动，未覆盖它：${file}\n没有写入最小文档；请重新运行 vika dsh model repair。`,
        `The settings document changed while repair was running; it was not overwritten: ${file}\n`
        + 'The minimal document was not written; run vika dsh model repair again.'));
    }
  }
  const temporary = `${file}.${process.pid}.tmp`;
  try {
    const descriptor = fs.openSync(temporary, 'w', 0o600);
    try {
      fs.writeSync(descriptor, MINIMAL_SETTINGS_DOCUMENT);
      fs.fsyncSync(descriptor);
    } finally {
      fs.closeSync(descriptor);
    }
    fs.chmodSync(temporary, 0o600);
    fs.renameSync(temporary, file);
  } catch (error) {
    try { fs.unlinkSync(temporary); } catch {}
    throw error;
  }
}

// The bridge folds every settings failure into a closed wire taxonomy
// (`data.code`), so a probe that got an answer is classified without matching
// prose. Only the invalid/unreadable class speaks about the document itself: an
// overridden entry or a disabled plugin is not something rewriting this file can
// fix, and a control process that never answered (spawn failure, handshake
// timeout, crash, bridge too old) says nothing about the document at all.
// @returns {string|null} the service's message when it could not read the
//   document, null when the failure proves nothing about it.
export function unreadableDocumentAnswer(error) {
  if (!(error instanceof DshControlError)) return null;
  // No numeric code means no error frame: the process failed, the service never
  // answered.
  if (!Number.isSafeInteger(error.code)) return null;
  if (error.data?.code !== 'SETTINGS_INVALID') return null;
  return text(error.message).replace(/^vika\/settings\/describe:\s*/, '');
}

// Whether a layer ABOVE the profile document owns llm-pi-ai. The settings service
// answers `descriptor: null` both for a document it cannot read and for an entry
// an upper layer disabled, so the probe alone cannot tell the two apart -- and
// rewriting the profile document in the second case destroys every route without
// fixing anything. Reading the two candidate layers is the only way to know
// before the destructive step.
//
// A YAML syntax error in these files is not our business: DSH reports that as a
// startup failure, not as an unreadable llm-pi-ai, and repair aborts on those
// anyway. An unreadable file here is therefore treated as "cannot rule it out".
export function overridingLayer({ dataDir }) {
  const layers = [
    { kind: 'home patch', file: path.join(dataDir, 'dsh', 'home', 'cordis.patch.yml') },
    { kind: 'launch overlay', file: path.join(dataDir, 'dsh', 'run', CONTROL_PATCH_FILE) },
  ];
  for (const layer of layers) {
    let contents;
    try {
      contents = fs.readFileSync(layer.file, 'utf8');
    } catch (error) {
      if (error.code === 'ENOENT') continue;
      return { ...layer, contents: null };
    }
    // The entry is identified by its id, in either the block form DSH writes
    // (`- id: llm-pi-ai`) or the JSON form Vika writes (`{"id":"llm-pi-ai"}`).
    // Matching the id alone is deliberate: this guard answers "does a layer above
    // claim this namespace", not "is the claim a disable", and refusing a layer
    // that merely configures it is the safe direction -- the rewrite could not
    // win against that layer either.
    if (/(?:^|[\s\[{,])["']?id["']?\s*:\s*["']?llm-pi-ai(?![\w.-])/m.test(contents)) {
      return { ...layer, contents };
    }
  }
  return null;
}

// A document with broken YAML syntax never reaches the settings service: DSH's
// own loader refuses it while booting, so the control process exits before
// answering and there is no error frame to classify. Repair still aborts there —
// a boot crash has many causes and is not a settings answer — but the loader
// does name the file it could not parse, so the message points at the document
// instead of leaving the operator with a stack trace.
export function documentParseHint(error, document) {
  if (!(error instanceof DshControlError)) return '';
  const diagnosis = `${text(error.message)}\n${text(error.stderr)}`;
  if (!/failed to parse (?:overlay|patch|file)/i.test(diagnosis)) return '';
  if (!diagnosis.includes(document)) return '';
  return t(
    `\n这份文档看起来就是 dsh 起不来的原因。先把它挪开或改好再重试：\n`
    + `  cp ${document} ${document}.bak-manual && printf '[{"id":"llm-pi-ai"}]\\n' > ${document}`,
    `\nThis document looks like what keeps dsh from starting. Move it aside or fix it by hand, then retry:\n`
    + `  cp ${document} ${document}.bak-manual && printf '[{"id":"llm-pi-ai"}]\\n' > ${document}`);
}
