// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// `ovsa dsh model`: the model settings of one DeepSeek Harness instance.
//
// The only source of truth is DSH's own `llm-pi-ai` settings document, which
// this command edits through the bridge of a short-lived DSH process
// (dsh-control.mjs). Nothing here writes a patch or a YAML file, with one
// deliberate exception: `repair` rewrites that one document, and only after a
// successful probe has shown the settings service cannot read it at all.
// Every other path goes through the service, which owns the file, so a second
// writer cannot race it.
//
// The command never takes a credential on its command line. `key set` reads
// the value from stdin, so it does not land in the shell history, the process
// table, or a log, and only the reference name is ever printed.
//
// This file is the command surface: the flags, the instance a command acts on,
// the interactive menu and the subcommands. What an id or a route `means` lives
// in dsh-model-plan.mjs; every spawn and every file write lives in
// dsh-model-store.mjs.

import fs from 'node:fs';
import path from 'node:path';
import { t } from './i18n.mjs';
import { chooseInstance, loadInstances } from './instances.mjs';
import { GoBack } from './prompt.mjs';
import { DshControlError } from '../../daemon/src/dsh-control.mjs';
import {
  UsageError, credentialRef, credentialRefProblem, importPlan, plainObject, removeModelOps,
  routeProblem, rowsOf, text,
} from './dsh-model-plan.mjs';
import {
  backupFile, describeProviders, discardedNoteFor, dshBin, documentParseHint, isConcurrentWrite,
  overridingLayer, readFileIfExists, settingsDocumentPath, settingsFailure, storedCredentialRefs,
  unreadableDocumentAnswer, updateSettings, withControl, writeMinimalSettingsDocument, writeModelEdit,
} from './dsh-model-store.mjs';

// Re-exported so that every existing `import ... from 'dsh-model.mjs'` keeps
// working: the split moved the definitions, not the surface.
export {
  MODALITIES, PROTOCOLS, addModelOps, credentialRefProblem, credentialRefsOf, deriveKeyRef,
  importPlan, modelIdProblem, parseModelId, reasoningEffortsOf, removeModelOps, routeIdProblem,
  selectableModelId,
} from './dsh-model-plan.mjs';
export { modelSpecFromDraft, writeModelDraft, writeModelEdit } from './dsh-model-store.mjs';

// One flag parser for every subcommand: `--name value` and `--name=value`, an
// unknown flag is a usage error rather than a silently ignored typo.
function parseArguments(argv, { values = [], booleans = [] } = {}) {
  const positionals = [];
  const flags = Object.create(null);
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--') {
      positionals.push(...argv.slice(index + 1));
      break;
    }
    if (!arg.startsWith('-') || arg === '-') {
      positionals.push(arg);
      continue;
    }
    const equals = arg.indexOf('=');
    const name = equals >= 0 ? arg.slice(0, equals) : arg;
    if (booleans.includes(name)) {
      if (equals >= 0) throw new UsageError(t(`${name} 不需要值`, `${name} takes no value`));
      flags[name] = true;
      continue;
    }
    if (!values.includes(name)) throw new UsageError(t(`不认识的选项 ${name}`, `unknown option ${name}`));
    const value = equals >= 0 ? arg.slice(equals + 1) : argv[++index];
    if (value === undefined || value === '') throw new UsageError(t(`${name} 后面缺少值`, `${name} needs a value`));
    flags[name] = value;
  }
  return { positionals, flags };
}

function instanceFlag(flags) {
  const value = flags['--instance'];
  return value === undefined ? null : text(value);
}

// The instance a command acts on. `--instance` names it, a single instance is
// used without asking, and several ask on a terminal through the same chooser
// `ovsa setup` uses. There is deliberately no "fall back to the manifest's
// defaultInstance": that silently pointed every command at the first instance,
// which made a second instance unreachable from this command.
//
// The deployment comes back with it because `DSH_BIN` — the one DSH key the
// manifest is allowed to carry — is read from there.
async function resolveInstance({ env, prompter, requested, question }) {
  const deployment = loadInstances(env);
  const agent = await chooseInstance({ deployment, requested, prompter, question });
  return { agent, deployment };
}

// What the instance question says when several are configured.
function modelInstanceQuestion() {
  return t('配置哪个实例的 model route', 'Which instance’s model routes');
}

function formatModelList(rows, { agent }) {
  const lines = [t(`实例 ${agent.id}（${agent.dataDir}）`, `instance ${agent.id} (${agent.dataDir})`)];
  if (rows.length === 0) {
    lines.push(t('  没有任何 model route。', '  No model routes.'));
    lines.push(t(`  添加一个：ovsa dsh model --instance ${agent.id}（菜单里选「添加 model」）`,
      `  Add one: ovsa dsh model --instance ${agent.id} (pick "Add a model" in the menu)`));
    return `${lines.join('\n')}\n`;
  }
  for (const row of rows) {
    const head = [`${row.provider}`, row.name];
    if (row.declared) head.push(row.api ?? t('未声明 protocol', 'no api'));
    else head.push(t('未声明', 'not declared'));
    if (row.baseURL) head.push(row.baseURL);
    lines.push(head.join('  '));
    const keyRef = row.credential?.ref ?? row.apiKeyEnv;
    if (keyRef) {
      const state = row.credential === null
        ? t('credential 状态未知', 'credential state unknown')
        : row.credential.present ? t('credential 已配置', 'credential ready')
          : row.credential.known ? t('credential 未配置', 'credential missing') : t('credential 状态未知', 'credential state unknown');
      const hint = row.credential?.present === false && row.credential?.known !== false
        ? `  → ovsa dsh model key set ${keyRef}`
        : '';
      lines.push(`  ${t('密钥', 'key')} ${keyRef}  ${state}${hint}`);
    } else {
      lines.push(`  ${t('密钥：无（由 DSH 自带鉴权解析）', 'key: none (DSH resolves its own auth)')}`);
    }
    for (const model of row.models) {
      const parts = [`  ${row.provider}/${model.id}`];
      if (model.name && model.name !== model.id) parts.push(model.name);
      if (model.input?.length) parts.push(`[${model.input.join(', ')}]`);
      if (!model.inCatalog) parts.push(t('（目录未返回）', '(not in the catalogue)'));
      lines.push(parts.join('  '));
    }
    if (row.models.length === 0) {
      const reason = row.error?.message
        ? `${t('目录查询失败', 'catalogue lookup failed')}: ${row.error.message}`
        : row.credential?.present === false && row.credential?.known !== false
          ? t('credential 未配置，DSH 不提供该 route 的 model', 'credential missing, so DSH offers no model on this route')
          : t('没有可用的 model', 'no models available');
      lines.push(`  ${t('（', '(')}${reason}${t('）', ')')}`);
    } else if (row.error?.message) {
      lines.push(`  ${t('目录查询失败', 'catalogue lookup failed')}: ${row.error.message}`);
    }
  }
  return `${lines.join('\n')}\n`;
}

// ---------------------------------------------------------------------------
// Interactive surface
// ---------------------------------------------------------------------------

// The wizard lives in its own module and is loaded on demand: it imports the plan
// and store modules, never this one, so a static import here would not be a cycle
// -- but the flag paths (the majority) still never need it, and `ovsa setup`
// reaches the wizard through its own import without this file at all.
async function loadWizard() {
  return import('./dsh-model-wizard.mjs');
}

// ← and Esc mean "back" only while an interactive surface is open; the flag
// paths must never start interpreting them. A menu with nowhere to go back to
// passes `back: false` instead of relying on this.
async function withNavigable(prompter, run) {
  const before = prompter.navigable === true;
  prompter.navigable = true;
  try {
    return await run();
  } finally {
    prompter.navigable = before;
  }
}


// The same read as `list`, with a spinner: the menu draws it between actions.
async function readRowsInteractively({ prompter, agent, deployment, env }) {
  const spinning = prompter.spin(t('读取 DSH 的 model route', 'Reading the DSH model routes'));
  try {
    const { rows, diagnostics } = await readModelRows({ agent, deployment, env });
    spinning.stop();
    for (const line of diagnostics) prompter.warn(line.trim());
    return rows;
  } catch (error) {
    spinning.fail(t('读不了实例的 DSH model route', 'Could not read the instance’s DSH model routes'));
    throw error;
  }
}

// Ask the shared wizard and return the payload to persist, or null.
//
// `seed` pre-answers fields the caller already knows (the edit flow passes the
// entry's current values; a fresh add passes nothing), `extra` carries the
// fields the wizard does not ask about, and `known` is the instance's declared
// routes for the dedupe rule. Nothing is written here.
async function collectModelDraft({ env, prompter, agent, deployment, seed = null, extra = {}, known = [] }) {
  const wizard = await loadWizard();
  const draft = await wizard.collectDshModelDraft({
    prompter,
    bin: dshBin(env, deployment),
    env,
    seed,
    known,
    onDecline: () => prompter.info(t('没有写入任何内容', 'Nothing was written')),
  });
  return draft ? { wizard, payload: { ...extra, ...draft } } : null;
}

// "添加 model": the same questions setup asks, written to the instance's home.
async function addModelInteractively({ env, stdout, prompter, agent, deployment, rows }) {
  const collected = await collectModelDraft({ env, prompter, agent, deployment, known: knownRoutesOf(rows) });
  if (!collected) return false;
  const { wizard, payload } = collected;
  const written = await wizard.writeDshModelDraft({ dataDir: agent.dataDir, draft: payload, bin: dshBin(env, deployment), env });
  const id = wizard.canonicalModelId(payload);
  stdout.write(t(`已写入 ${id}（route ${payload.route}，api=${payload.api}）\n`,
    `Wrote ${id} (route ${payload.route}, api=${payload.api})\n`));
  writeNotes(stdout, written.notes ?? []);
  if (!payload.keyValue && written.keyRef) {
    stdout.write(t(`  credential reference ${written.keyRef}：还没写入值时 model 不会出现在 /model 里，运行 ovsa dsh model key set ${written.keyRef}\n`,
      `  credential ${written.keyRef}: the model stays out of /model until the value is stored; run ovsa dsh model key set ${written.keyRef}\n`));
  }
  // DSH reads its settings document at start-up only; the daemon's process has
  // already started, so the new route is invisible until it is restarted.
  stdout.write(t(`  DSH 只在启动时读这份设置：让正在运行的 ${agent.id} 用上它，运行 ovsa restart ${agent.id}\n`,
    `  DSH reads this document only at start-up: to let the running ${agent.id} use it, run ovsa restart ${agent.id}\n`));
  return true;
}

// The fields a "修改 model" run has to preserve: the wizard does not ask for
// them, but they are part of the entry and must survive the write.
function preservedModelFields(entry) {
  return {
    input: Array.isArray(entry.input) ? [...entry.input] : null,
    contextWindow: Number.isSafeInteger(entry.contextWindow) ? entry.contextWindow : null,
    maxTokens: Number.isSafeInteger(entry.maxTokens) ? entry.maxTokens : null,
    noReasoning: entry.reasoningEfforts === false,
  };
}

// What an edit changed, one short line per field. Empty means the write would be
// a no-op, and the caller then writes nothing at all.
function describeModelChanges(seed, draft) {
  const lines = [];
  if ((seed.baseURL || '') !== (draft.baseURL || '')) {
    lines.push(`${t('网关', 'gateway')} ${seed.baseURL || '—'} → ${draft.baseURL}`);
  }
  if ((seed.api || '') !== (draft.api || '')) lines.push(`protocol ${seed.api || '—'} → ${draft.api}`);
  if (seed.model !== draft.model) lines.push(`model ${seed.model} → ${draft.model}`);
  if ((seed.keyEnv || '') !== (draft.keyEnv || '')) {
    lines.push(`credential reference ${seed.keyEnv || '—'} → ${draft.keyEnv || '—'}`);
  }
  // A pasted value is a change even when no field moved: it is the one thing the
  // operator cannot see in the document, and reporting "no change" would drop it.
  if (text(draft.keyValue)) {
    lines.push(t(`credential ${draft.keyEnv || seed.keyEnv} 的值已更新`,
      `the value of credential ${draft.keyEnv || seed.keyEnv} was updated`));
  }
  return lines;
}

// "修改 model": pick an entry the instance declares, seed the wizard with its
// current values, and update that same entry in place. The route is locked
// (there is nothing to guess), the key VALUE is never read or shown, and a new
// model id is a rename on the same route.
async function editModelInteractively({ env, stdout, prompter, agent, deployment, rows }) {
  const choices = [];
  for (const row of rows) {
    if (!row.declared) continue;
    for (const model of row.models) {
      choices.push({
        value: { route: row.provider, model: model.id },
        label: `${row.provider}/${model.id}`,
        hint: model.name && model.name !== model.id ? model.name : undefined,
      });
    }
  }
  if (choices.length === 0) {
    prompter.info(t('没有可修改的 model：这个实例还没有自己声明过 model', 'Nothing to edit: this instance declares no model route yet'));
    return false;
  }
  choices.push({ value: null, label: t('取消', 'Cancel') });
  const target = await prompter.select(t('修改哪个 model？', 'Edit which model?'), choices, { label: t('修改 model', 'Edit model') });
  if (!target) return false;
  // The list only carries what is safe to print; the seed needs the profile too
  // (sizes, the entry's own name, its reasoning declaration).
  const profile = await withControl({ agent, deployment, env }, async (client) => {
    const { providers } = await describeProviders(client);
    return plainObject(providers[target.route]) ? providers[target.route] : null;
  });
  const entry = plainObject(profile) && Array.isArray(profile.models)
    ? profile.models.find((model) => plainObject(model) && model.id === target.model)
    : null;
  if (!plainObject(profile) || !plainObject(entry)) {
    prompter.warn(t(`读不到 ${target.route}/${target.model} 的当前配置，什么都没有改`,
      `Could not read the current configuration of ${target.route}/${target.model}; nothing changed`));
    return false;
  }
  const seed = {
    route: target.route,
    model: target.model,
    api: text(profile.api) || null,
    baseURL: text(profile.baseURL) || null,
    keyEnv: text(profile.apiKeyEnv) || null,
    name: text(entry.name) || null,
  };
  // The route being edited is not a conflict with itself.
  const known = knownRoutesOf(rows).filter((route) => route.route !== target.route);
  const collected = await collectModelDraft({
    env, prompter, agent, deployment, seed, extra: preservedModelFields(entry), known,
  });
  if (!collected) return false;
  const { payload } = collected;
  if (payload.route !== target.route) {
    // Defensive: the edit wizard seeds its route, so this means the shared
    // wizard changed shape. Moving a model between routes is not an edit.
    prompter.warn(t('修改不能把 model 换到另一个 route，什么都没有改',
      'Editing cannot move a model to another route; nothing changed'));
    return false;
  }
  const changes = describeModelChanges(seed, payload);
  if (changes.length === 0) {
    prompter.info(t('没有改动，未写入', 'No change; nothing was written'));
    return false;
  }
  const written = await writeModelEdit({
    dataDir: agent.dataDir, draft: payload, previous: target, bin: dshBin(env, deployment), env,
  });
  const id = `${payload.route}/${payload.model}`;
  stdout.write(t(`已更新 ${id}\n`, `Updated ${id}\n`));
  for (const line of changes) stdout.write(`  ${line}\n`);
  writeNotes(stdout, written.notes ?? []);
  if (!payload.keyValue && written.keyRef && seed.keyEnv !== payload.keyEnv) {
    stdout.write(t(`  credential reference ${written.keyRef}：还没写入值时 model 不会出现在 /model 里，运行 ovsa dsh model key set ${written.keyRef}\n`,
      `  credential ${written.keyRef}: the model stays out of /model until the value is stored; run ovsa dsh model key set ${written.keyRef}\n`));
  }
  stdout.write(t(`  DSH 只在启动时读这份设置：让正在运行的 ${agent.id} 用上它，运行 ovsa restart ${agent.id}\n`,
    `  DSH reads this document only at start-up: to let the running ${agent.id} use it, run ovsa restart ${agent.id}\n`));
  return true;
}

// The top-level menu is bare labels: every row is an action, and the list it
// acts on is already printed above the menu, so restating either as text only
// makes the screen longer. A hint survives only in the pickers below, where a
// row's display name is something its id cannot show.
function menuChoices({ canSwitch = false } = {}) {
  return [
    { value: 'add', label: t('添加 model', 'Add a model') },
    { value: 'edit', label: t('修改 model', 'Edit a model') },
    { value: 'remove', label: t('删除 model 或 route', 'Remove a model or route') },
    { value: 'key-set', label: t('设置 credential 的值', 'Set a credential value') },
    { value: 'key-unset', label: t('删除 credential', 'Remove a credential') },
    ...(canSwitch ? [{ value: 'switch', label: t('切换实例', 'Switch instance') }] : []),
    { value: 'quit', label: t('退出', 'Quit') },
  ];
}

// The delete picker lists what the instance really declares: a route DSH merely
// ships (the built-in `deepseek-official`) cannot be removed from here.
async function removeInteractively({ env, prompter, stdout, agent, deployment, rows }) {
  const choices = [];
  for (const row of rows) {
    if (!row.declared) continue;
    for (const model of row.models) {
      choices.push({
        value: { route: row.provider, model: model.id },
        label: `${row.provider}/${model.id}`,
        hint: model.name && model.name !== model.id ? model.name : undefined,
      });
    }
    if (row.models.length > 0) {
      choices.push({ value: { route: row.provider, model: null }, label: t(`整个 route ${row.provider}`, `the whole route ${row.provider}`) });
    }
  }
  if (choices.length === 0) {
    prompter.info(t('没有可删除的 route：这个实例还没有自己声明过 model', 'Nothing to remove: this instance declares no model route yet'));
    return false;
  }
  choices.push({ value: null, label: t('取消', 'Cancel') });
  const target = await prompter.select(t('删除哪一项？', 'Remove which?'), choices, { label: t('删除', 'Delete') });
  if (!target) return false;
  const shown = target.model === null ? target.route : `${target.route}/${target.model}`;
  if (!await prompter.confirm(t(`删除 ${shown}？`, `Remove ${shown}?`), false)) return false;
  await removeTarget({ agent, deployment, env, target, stdout });
  return true;
}

// The references this instance already resolves: the ones routes name, plus a
// manual entry (a value may be stored before the route that uses it exists).
async function credentialInteractively({ action, env, prompter, stdout, agent, deployment, rows }) {
  const known = [];
  for (const row of rows) {
    if (row.apiKeyEnv && !known.some((entry) => entry.ref === row.apiKeyEnv)) known.push({ ref: row.apiKeyEnv, route: row.provider });
  }
  const choices = known.map(({ ref, route }) => ({
    value: ref, label: ref, hint: t(`route ${route}`, `route ${route}`),
  }));
  choices.push({ value: '__manual__', label: t('输入另一个 reference', 'Type another reference') });
  choices.push({ value: null, label: t('取消', 'Cancel') });
  const chosen = await prompter.select(
    action === 'set' ? t('设置哪个 credential 的值？', 'Set which credential?') : t('删除哪个 credential？', 'Remove which credential?'),
    choices, { label: t('credential', 'Credential') });
  if (!chosen) return false;
  const ref = chosen === '__manual__'
    ? await prompter.text(t('credential reference', 'Credential reference'), {
      label: t('credential reference', 'Credential reference'),
      initial: '',
      validate: (value) => credentialRefProblem(value),
    })
    : chosen;
  if (action === 'unset') {
    if (!await prompter.confirm(t(`删除 credential ${ref}？值会一起删掉，用到它的 route 会变得不可用。`,
      `Remove credential ${ref}? Its value is deleted too, and any route naming it stops working.`), false)) return false;
    await unsetCredentialRef({ agent, deployment, env, ref, stdout });
    return true;
  }
  const value = String(await prompter.secret(t(`credential ${ref} 的值（不会回显）`, `Value for ${ref} (not echoed)`)) ?? '').trim();
  if (!value) {
    prompter.warn(t('没有输入值，什么都没有改', 'No value entered; nothing changed'));
    return false;
  }
  await setCredentialRef({ agent, deployment, env, ref, value, stdout });
  return true;
}

// One menu action. Returns true when it changed something, which is what makes
// the menu re-read the routes before drawing again.
async function runMenuAction({ choice, env, prompter, stdout, agent, deployment, rows }) {
  if (choice === 'add') return await addModelInteractively({ env, stdout, prompter, agent, deployment, rows });
  if (choice === 'edit') return await editModelInteractively({ env, stdout, prompter, agent, deployment, rows });
  if (choice === 'remove') return await removeInteractively({ env, prompter, stdout, agent, deployment, rows });
  if (choice === 'key-set') return await credentialInteractively({ action: 'set', env, prompter, stdout, agent, deployment, rows });
  if (choice === 'key-unset') return await credentialInteractively({ action: 'unset', env, prompter, stdout, agent, deployment, rows });
  return false;
}

// `ovsa dsh model` on a terminal: the routes, then a menu that keeps coming
// back until the operator quits. Nothing here is full-screen; every question is
// a prompt the rest of the CLI already uses, so keys never stop responding.
//
// The instance is chosen once (or switched from the menu), and every read is
// done against that instance's own DSH home: switching re-reads, so one
// instance's document is never shown under another instance's name.
async function interactiveCommand({ argv, env, stdin, stdout, stderr, prompter }) {
  const { flags } = parseArguments(argv, { values: ['--instance'] });
  return withNavigable(prompter, async () => {
    let target;
    try {
      target = await resolveInstance({
        env, prompter, requested: instanceFlag(flags), question: modelInstanceQuestion(),
      });
    } catch (error) {
      // ← at the instance chooser is "leave", not a failure.
      if (error instanceof GoBack) return 0;
      throw error;
    }
    let { agent } = target;
    const { deployment } = target;
    let rows = await readRowsInteractively({ prompter, agent, deployment, env });
    let showList = true;
    for (;;) {
      if (showList) {
        stdout.write(formatModelList(rows, { agent }));
        showList = false;
      }
      // The menu always names the instance it acts on, above the options.
      stdout.write(t(`模型设置：实例 ${agent.id}（${agent.displayName}）\n`,
        `Model settings: instance ${agent.id} (${agent.displayName})\n`));
      const choice = await prompter.select(t('要做什么', 'What to do'), menuChoices({ canSwitch: deployment.agents.length > 1 }), {
        back: false, label: t('模型设置', 'Model settings'),
      });
      if (choice === 'quit') return 0;
      if (choice === 'switch') {
        try {
          agent = await chooseInstance({
            deployment, requested: null, prompter, question: modelInstanceQuestion(),
          });
        } catch (error) {
          if (!(error instanceof GoBack)) throw error;
          showList = true;
          continue;
        }
        rows = await readRowsInteractively({ prompter, agent, deployment, env });
        showList = true;
        continue;
      }
      let changed = false;
      try {
        changed = await runMenuAction({ choice, env, prompter, stdout, agent, deployment, rows });
      } catch (error) {
        // ← cancels one action, not the whole session, and a failed action says
        // why and leaves the menu usable instead of exiting on a typo.
        if (error instanceof GoBack) {
          showList = true;
          continue;
        }
        if (error instanceof DshControlError) throw error;
        prompter.warn(error.message);
        showList = true;
        continue;
      }
      if (changed) {
        rows = await readRowsInteractively({ prompter, agent, deployment, env });
        showList = true;
      }
    }
  });
}

// `ovsa dsh model` with only its own flags names no subcommand: it is the menu in
// a terminal and the list anywhere else. `--json` belongs to that list, so it
// must not read as a subcommand name. Anything else is a subcommand (or a typo)
// and follows the old dispatch.
function isBareInvocation(argv) {
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === '--json') continue;
    if (token === '--instance') {
      if (index + 1 >= argv.length) return false;
      index += 1;
      continue;
    }
    if (token.startsWith('--instance=')) continue;
    return false;
  }
  return true;
}

// The declared routes in the shape the wizard dedupes with: route id, endpoint
// and the model ids it already holds. Catalogue-only routes are not part of the
// document, so adding one is always a new entry.
function knownRoutesOf(rows) {
  return rows
    .filter((row) => row.declared)
    .map((row) => ({
      route: row.provider,
      baseURL: row.baseURL,
      // The reference the route already resolves, so adding a model to it keeps
      // that name instead of re-deriving one (an imported route may legitimately
      // hold `_2` after a rotation, and re-pointing it would silently change
      // which secret the route sends).
      keyEnv: row.credential?.ref ?? row.apiKeyEnv ?? null,
      models: row.models.map((model) => model.id),
    }));
}

function usageText() {
  return t(
    `用法：
  ovsa dsh model [list] [--instance <实例>] [--json]
  ovsa dsh model                         在终端里打开 model 菜单：添加/修改 model、删除、credential、列表
  ovsa dsh model remove <route>[/<model>] [--instance <实例>]
  ovsa dsh model import <models.json> [--dry-run] [--instance <实例>]
  ovsa dsh model repair [--instance <实例>]
  ovsa dsh model key set <reference> [--stdin] [--instance <实例>]
  ovsa dsh model key unset <reference> [--instance <实例>]

说明：
  list      列出 route、model 和 credential 是否就绪（--json 输出机器可读的数组）
  无子命令  在终端里是菜单：添加 model、修改 model、删除 model 或 route、设置/删除
            credential、切换实例、退出；不是终端时就是 list
            （有多个实例又没写 --instance 时报错并列出实例）
  添加/修改 model 只在菜单里（和 ovsa setup 里那份是同一套问题）：选「添加 model」按提示填
            网关地址、protocol、model id 和 credential；选「修改 model」先挑一个已有的
            route/model，再改它的字段。写入前都会先用一次临时 DSH 验证，并在之后提示
            重启实例。命令行不再提供 add 子命令。
  remove    删除一个 route 或其中一个 model；删掉最后一个 model 时整个 route 一起删除
  import    一次性把旧 models.json 迁移成 DSH 原生配置；--dry-run 只打印将要写入的内容
  repair    文档被手工写坏、describe 读不到 llm-pi-ai 时：备份后写回最小文档并验证
            备份是原文档的完整字节，落在同一目录的 <文档>.bak-<14 位时间戳>，
            已存在则追加 -1、-2 …；写回前会打印将丢弃的 route 个数
  key set   从标准输入或隐藏提示读取密钥，只回显 reference
  key unset 删除一个 credential

实例：--instance 指定；只有一个实例时直接用；多个实例在终端里会问，不是终端则报错并列出实例。

import 永远为字面密钥分配新 reference，不改写任何已有 reference：重复导入会轮换到下一个 reference，
旧 reference 连同原值保留，用 key unset 单独清理。值先写、route 后写，所以中途被打断
（Ctrl-C、崩溃）可能留下没有任何 route 引用的 credential：list 只显示 route 引用的那些，
用 key unset <reference> 清理。`,
    `Usage:
  ovsa dsh model [list] [--instance <id>] [--json]
  ovsa dsh model                         open the model menu in a terminal: add/edit models, remove,
                                         credentials, list
  ovsa dsh model remove <route>[/<model>] [--instance <id>]
  ovsa dsh model import <models.json> [--dry-run] [--instance <id>]
  ovsa dsh model repair [--instance <id>]
  ovsa dsh model key set <REF> [--stdin] [--instance <id>]
  ovsa dsh model key unset <REF> [--instance <id>]

Notes:
  list      show routes, models and whether each credential is ready (--json for machines)
  (none)    in a terminal: the menu -- add a model, edit a model, remove a model or route,
            set/remove a credential, show the full list, switch instance, quit; without a
            terminal it is list (with several instances and no --instance it refuses and
            lists them)
  add/edit  only in the menu (the same questions ovsa setup asks): "Add a model" asks for the
            gateway URL, protocol, model id and credential; "Edit a model" picks an existing
            route/model first, then changes its fields. Both check the draft against a
            temporary DSH and tell you to restart the instance afterwards. There is no add
            subcommand on the command line.
  remove    delete a route or one of its models; removing the last model removes the route
  import    one-off migration of a legacy models.json; --dry-run prints what would be written
  repair    when the document was hand-edited and llm-pi-ai is unreadable: back up, write a
            minimal document, then verify through the settings service. The backup is the
            original bytes in the same directory as <document>.bak-<14-digit timestamp>,
            with -1, -2 ... appended if it already exists; the number of routes about to be
            discarded is printed before the rewrite.
  key set   read a credential from stdin or a hidden prompt; only the reference is echoed
  key unset delete one credential reference

Instance: --instance names it; a single instance is used directly; several ask on a
terminal and are an error listing them otherwise.

import always allocates a new reference for a literal key and never rewrites an
existing one: a repeated import rotates to the next reference, the previous one
keeps its value, and key unset removes it. Values are written before routes, so an
import interrupted (Ctrl-C, a crash) can leave a credential no route references:
list shows only the references routes use, and key unset <REF> removes it.`);
}

async function readStdinText(stdin) {
  const chunks = [];
  for await (const chunk of stdin) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk)));
  return Buffer.concat(chunks).toString('utf8');
}

// The value never touches the argument list: stdin when piped (or --stdin),
// a hidden prompt on a terminal.
async function readCredentialValue({ ref, stdin, forceStdin, prompter }) {
  if (!forceStdin && stdin?.isTTY) {
    if (!prompter || typeof prompter.secret !== 'function') {
      throw new Error(t('没有可用的隐藏输入，请改用 --stdin 从标准输入提供密钥',
        'No hidden-input prompt is available; use --stdin to pass the credential on stdin'));
    }
    const value = String(await prompter.secret(t(`credential ${ref} 的值（不会回显）`, `Value for ${ref} (not echoed)`)) ?? '').trim();
    return value;
  }
  return (await readStdinText(stdin)).trim();
}

// One control process reads what the list and the interactive menu both need:
// the settings-declared routes and the live catalogue, each reported even when
// the other fails (a broken document must not hide the catalogue, and a slow
// catalogue must not hide the routes).
async function readModelRows({ agent, deployment, env }) {
  const collected = await withControl({ agent, deployment, env }, async (client) => {
    let settingsError = null;
    let providers = null;
    try {
      providers = (await describeProviders(client)).providers;
    } catch (error) {
      settingsError = error;
      providers = {};
    }
    let catalog = null;
    let catalogError = null;
    try {
      catalog = await client.listModels();
    } catch (error) {
      catalogError = error;
    }
    return { providers, settingsError, catalog, catalogError };
  });
  const rows = rowsOf(collected.providers ?? {}, collected.catalog);
  const diagnostics = [];
  if (collected.settingsError) {
    diagnostics.push(t(`设置读取失败：${collected.settingsError.message}\n`, `Could not read the settings: ${collected.settingsError.message}\n`));
  }
  if (collected.catalogError) {
    diagnostics.push(t(`model 目录读取失败：${collected.catalogError.message}\n`, `Could not read the model catalogue: ${collected.catalogError.message}\n`));
  }
  return { rows, diagnostics, settingsFailed: collected.settingsError !== null };
}

async function listCommand({ argv, env, stdout, stderr, prompter }) {
  const { flags } = parseArguments(argv, { values: ['--instance'], booleans: ['--json'] });
  const { agent, deployment } = await resolveInstance({ env, prompter, requested: instanceFlag(flags), question: modelInstanceQuestion() });
  const { rows, diagnostics, settingsFailed } = await readModelRows({ agent, deployment, env });
  if (flags['--json']) {
    // JSON mode keeps stdout parseable: an unreadable document is a diagnostic,
    // not a second payload, so it goes to stderr there. Which is exactly why the
    // exit code has to carry it: a caller parsing stdout sees an array with
    // `declared: false` on every row and cannot tell "this instance has no
    // routes" from "the document could not be read", and it would infer the
    // first. The payload shape is unchanged so an existing parser still works.
    stdout.write(`${JSON.stringify(rows, null, 2)}\n`);
    for (const line of diagnostics) stderr.write(line);
    return settingsFailed ? 1 : 0;
  }
  stdout.write(formatModelList(rows, { agent }));
  for (const line of diagnostics) stdout.write(line);
  return 0;
}

// Delete one route or one model through the settings service. Shared by
// `remove` and the interactive picker so both report the same thing.
async function removeTarget({ agent, deployment, env, target, stdout }) {
  let keyRef = null;
  await withControl({ agent, deployment, env }, async (client) => {
    const { ops } = await updateSettings(client, ({ providers }) => {
      const result = removeModelOps(providers, target);
      keyRef = result.keyRef;
      return result.ops;
    }, { dataDir: agent.dataDir });
    if (ops.length === 0) throw new Error(t('没有需要删除的内容', 'Nothing to remove'));
  });
  stdout.write(target.model === null
    ? t(`已删除 route ${target.route}\n`, `Removed route ${target.route}\n`)
    : t(`已从 ${target.route} 删除 model ${target.model}\n`, `Removed model ${target.model} from ${target.route}\n`));
  if (keyRef) {
    stdout.write(t(`  credential reference ${keyRef} 保留着；要一起删除：ovsa dsh model key unset ${keyRef}\n`,
      `  the credential ${keyRef} is left in place; remove it with ovsa dsh model key unset ${keyRef}\n`));
  }
}

async function removeCommand({ argv, env, stdout, prompter }) {
  const { positionals, flags } = parseArguments(argv, { values: ['--instance'] });
  if (positionals.length !== 1) throw new UsageError(t('remove 需要且只需要一个 <route>[/<model>]', 'remove takes exactly one <route>[/<model>]'));
  const raw = text(positionals[0]);
  const slash = raw.indexOf('/');
  const target = slash < 0
    ? { route: routeProblem(raw), model: null }
    : { route: routeProblem(raw.slice(0, slash)), model: raw.slice(slash + 1) };
  if (target.model === '') throw new UsageError(t(`"${raw}" 后面没有 model`, `"${raw}" names no model`));
  const { agent, deployment } = await resolveInstance({ env, prompter, requested: instanceFlag(flags), question: modelInstanceQuestion() });
  await removeTarget({ agent, deployment, env, target, stdout });
  return 0;
}

// The references a providers dict resolves, for the cleanup decision.
function liveRefsOf(providers) {
  const refs = new Set();
  for (const profile of Object.values(plainObject(providers) ? providers : {})) {
    const ref = text(profile?.apiKeyEnv);
    if (ref) refs.add(ref);
  }
  return refs;
}

// Remove references this run created.
//
// Only ever called with names the allocator handed out through `claimFresh`,
// which is a fact rather than an assumption: that method never returns a name a
// configured route resolves and never one the credential store already held. A
// removal that fails is reported, never retried into silence.
async function withdrawCreated(client, refs, withdrawn, failed) {
  for (const ref of refs) {
    try {
      await client.unsetCredential(ref);
      withdrawn.push(ref);
    } catch (error) {
      failed.push({ ref, error });
    }
  }
}

// What a failed settings write actually proves.
//
// A refusal arrives as a JSON-RPC error frame (a numeric `code`), which is
// proof the write did not land. A failure with no frame — an expired request
// bound, a process that died, a closed stdin — is a different fact: the frame
// may have reached DSH and been applied. The cleanup decision depends on the
// difference, so it is made here and nowhere else.
// @returns {'refused'|'unstarted'|'unknown'}
function commitOutcome(error) {
  if (error?.data?.code || Number.isSafeInteger(error?.code)) return 'refused';
  if (/before \S+ was sent/.test(String(error?.message ?? ''))) return 'unstarted';
  return 'unknown';
}

// One migration transaction, inside one control process.
//
// I1 — no write ever targets a reference an existing route resolves. Every
//      literal key is stored under a reference this run allocated and nobody
//      else can be using, so the migration cannot change what a route that the
//      document does not mention authenticates with. Existing references are
//      kept byte for byte; the visible consequence is deliberate rotation.
// I2 — value first, then one settings mutate carrying the endpoint and the
//      reference together, then cleanup of what this run created. A refused
//      write removes those references again; an unconfirmed one does not,
//      because the write may have landed.
// I3 — every attempt rebuilds the plan from the state it was just handed, and a
//      reference this run already wrote is carried over instead of re-derived.
//      A second conflict aborts; it never turns into a silent success.
//
// @returns {{plan, created: Map<string,string>, refused, withdrawFailed,
//   failure: Error|null, outcome: string|null, withdrawn: string[],
//   unverifiable: string[], keptLive: string[]}}
async function runImport(client, { parsed, file, env, agent }) {
  // Read once, before the first write: from here on the set also carries every
  // reference this run created, so a re-plan can neither adopt a name somebody
  // else owns nor hand out one it has already used.
  const stored = storedCredentialRefs(agent);
  const created = new Map();      // reference → the value this run wrote there
  const pinned = {};              // source route → the reference carrying its value
  const refused = [];
  const withdrawFailed = [];
  const withdrawn = [];
  let plan = null;
  let commitState = null;
  let failure = null;
  let outcome = null;
  for (let attempt = 0; ; attempt += 1) {
    const state = await describeProviders(client);
    plan = importPlan(parsed, {
      file,
      env,
      providers: state.providers,
      stored,
      pinned: Object.keys(pinned).length ? { ...pinned } : null,
    });
    if (plan.ops.length === 0) return {
      plan, created, refused, withdrawFailed, withdrawn, unverifiable: [],
      keptLive: [], failure: null, outcome: null,
    };

    // 1. The values, under this run's own new references. Writing them first is
    //    what makes a route write safe to commit: at the instant it lands, the
    //    references it names already hold their values.
    for (const credential of plan.credentials) {
      if (credential.value === null) continue;
      pinned[credential.route] = credential.ref;
      if (created.get(credential.ref) === credential.value) continue;
      try {
        await client.setCredential(credential.ref, credential.value);
        created.set(credential.ref, credential.value);
        stored.add(credential.ref);
      } catch (error) {
        refused.push({ credential, error });
      }
    }
    if (refused.length) break;

    // 2. One mutate for every route: an import either writes all of them or
    //    none of them, bound to the revision this plan was built from.
    try {
      await client.mutateSettings(plan.ops, state.descriptor.revision);
      commitState = state;
      break;
    } catch (error) {
      if (attempt === 0 && isConcurrentWrite(error)) continue;
      failure = settingsFailure(error, { dataDir: agent.dataDir });
      outcome = commitOutcome(error);
      break;
    }
  }

  const committed = commitState !== null;
  // References this run created that the committed plan does not name: either a
  // re-plan replaced them, or the settings write failed. They are deleted only
  // when nothing resolves them — a name a route started using meanwhile is not
  // an orphan, whatever this run wrote under it.
  const usedRefs = committed ? new Set(plan.credentials.map((entry) => entry.ref)) : new Set();
  const candidates = [...created.keys()].filter((ref) => !usedRefs.has(ref));
  const unverifiable = [];
  const keptLive = [];
  if (candidates.length) {
    let live = null;
    if (committed) {
      live = liveRefsOf(commitState.providers);
    } else if (outcome === 'refused' || outcome === 'unstarted' || refused.length) {
      // A best-effort read: if the service can still answer, prefer its view of
      // which references are in use over deleting on the assumption that they
      // are not.
      try {
        live = liveRefsOf((await describeProviders(client)).providers);
      } catch {
        live = null;
      }
    }
    if (live === null) {
      unverifiable.push(...candidates);
    } else {
      keptLive.push(...candidates.filter((ref) => live.has(ref)));
      await withdrawCreated(client, candidates.filter((ref) => !live.has(ref)), withdrawn, withdrawFailed);
    }
  }
  return { plan, created, refused, withdrawFailed, withdrawn, unverifiable, keptLive, failure, outcome };
}

async function importCommand({ argv, env, stdout, prompter }) {
  const { positionals, flags } = parseArguments(argv, { values: ['--instance'], booleans: ['--dry-run'] });
  if (positionals.length !== 1) throw new UsageError(t('import 需要且只需要一个 models.json 路径', 'import takes exactly one models.json path'));
  const file = path.resolve(positionals[0]);
  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    throw new Error(t(`读不了 ${file}：${error.message}`, `Cannot read ${file}: ${error.message}`));
  }
  // The dry run starts no process, so it cannot see which references the
  // instance already resolves or which names already hold a value; it reports
  // the document's own view and says the live names are decided at write time.
  const dryPlan = importPlan(parsed, { file, env });
  if (flags['--dry-run']) {
    stdout.write(t(`会把 ${dryPlan.routes.length} 个 route 写入 llm-pi-ai：\n`, `Would write ${dryPlan.routes.length} route(s) to llm-pi-ai:\n`));
    stdout.write(`${JSON.stringify(dryPlan.ops, null, 2)}\n`);
    const dryValues = dryPlan.credentials.filter((entry) => entry.value !== null);
    if (dryValues.length) {
      stdout.write(t(`会写入 credential：${dryValues.map((entry) => entry.ref).join(', ')}\n`,
        `Would store credentials: ${dryValues.map((entry) => entry.ref).join(', ')}\n`));
    }
    // The real run decides these names from the instance's live routes and its
    // credential store, and it always allocates a new one. Saying so here keeps
    // the dry run from reading like a promise about the names it prints.
    stdout.write(t('注意：实际导入会按实例当前的 route 与 credential 分配**新的**reference（不改写任何已有 reference），上面打印的名字可能与之不同。\n',
      'Note: the real run allocates *new* references from the instance\'s current routes and credentials (it never overwrites an existing one), so the names above may differ.\n'));
    writeNotes(stdout, dryPlan.notes);
    return 0;
  }
  if (dryPlan.ops.length === 0) {
    writeNotes(stdout, dryPlan.notes);
    throw new Error(t('models.json 里没有能迁移的 route', 'models.json declares no route this DSH can serve'));
  }
  const { agent, deployment } = await resolveInstance({ env, prompter, requested: instanceFlag(flags), question: modelInstanceQuestion() });
  const result = await withControl({ agent, deployment, env }, (client) => runImport(client, { parsed, file, env, agent }));
  const plan = result.plan;

  const routeLines = () => {
    for (const route of plan.routes) {
      const key = route.apiKeyEnv ? `  ${t('credential', 'key')} ${route.apiKeyEnv}` : '';
      stdout.write(`  ${route.route}  ${route.api}${route.baseURL ? `  ${route.baseURL}` : ''}${key}\n`);
      stdout.write(`    ${t('model', 'models')}: ${route.models.join(', ')}\n`);
    }
  };
  const leftoverNote = result.withdrawFailed.length
    ? t(`\n这些 reference 没能删除，请手工清理：${result.withdrawFailed.map((entry) => entry.ref).join(', ')}`,
      `\nThese references could not be removed; clean them up by hand: ${result.withdrawFailed.map((entry) => entry.ref).join(', ')}`)
    : '';
  const removedLine = t(
    `本次新建的 reference${result.withdrawn.length ? `已删除（${result.withdrawn.join(', ')}）` : '已全部删除'}。\n`,
    `The references this run created ${result.withdrawn.length ? `were removed (${result.withdrawn.join(', ')})` : 'were all removed'}.\n`);
  if (result.refused.length) {
    // No route was written: the value a route needs did not reach the
    // credentials service, and the routes never left this process.
    const detail = result.refused.map(({ credential, error }) => `  ${credential.ref}: ${error.message}`).join('\n');
    const held = result.unverifiable.length
      ? t(`这些 reference 没能确认并删除，请手工检查：${result.unverifiable.join(', ')}\n`,
        `These references could not be confirmed and removed; inspect them by hand: ${result.unverifiable.join(', ')}\n`)
      : removedLine;
    throw new Error(t(
      `一个 route 都没有写入：${result.refused.length} 个 credential 没能保存。\n`
      + `未保存：\n${detail}\n`
      + held
      + '在用配置未被改动：这次没有写任何已有 route 正在使用的 credential reference。\n'
      + '修好 credential 服务后重新运行同一条 import。',
      `No route was written: ${result.refused.length} credential(s) could not be stored.\n`
      + `Not stored:\n${detail}\n`
      + held
      + 'The in-use configuration was not changed: this run wrote no reference a configured route resolves.\n'
      + 'Fix the credentials service and run the same import again.') + leftoverNote);
  }
  if (result.failure) {
    if (result.outcome === 'unknown') {
      // The one case the protocol must not guess about: the write may have
      // landed, so the new references are kept instead of being deleted.
      const held = [...result.created.keys()];
      throw new Error(t(
        `无法确认这次导入是否已经写入：${result.failure.message}\n`
        + `已写入的 credential reference${held.length ? `（${held.join(', ')}）` : ''}故意保留，因为 route 可能已经指向它们。\n`
        + '请先 `ovsa dsh model list` 复查；重跑 import 会另分配新 reference，不会覆盖这些。',
        `Cannot tell whether this import landed: ${result.failure.message}\n`
        + `The references this run stored${held.length ? ` (${held.join(', ')})` : ''} are kept on purpose, because the routes may already point at them.\n`
        + 'Check with ovsa dsh model list first; running the import again allocates new references and never overwrites these.') + leftoverNote);
    }
    throw new Error(t(
      `一个 route 都没有写入：${result.failure.message}\n`
      + removedLine
      + '在用配置未被改动：这次没有写任何已有 route 正在使用的 credential reference。',
      `No route was written: ${result.failure.message}\n`
      + removedLine
      + 'The in-use configuration was not changed: this run wrote no reference a configured route resolves.') + leftoverNote);
  }
  stdout.write(t(`已迁移 ${plan.routes.length} 个 route：\n`, `Imported ${plan.routes.length} route(s):\n`));
  routeLines();
  const stored = [...result.created.keys()];
  if (stored.length) {
    stdout.write(t(`已写入 ${stored.length} 个 credential：${stored.join(', ')}\n`,
      `Stored ${stored.length} credential(s): ${stored.join(', ')}\n`));
  }
  // The rotation contract, in the words the operator needs: what the route uses
  // now, what was left in place, and how to remove the leftover.
  // A split provider gives several routes the same rotation; it is one fact, so
  // it is reported once, with every route it applies to.
  const rotations = new Map();
  for (const route of plan.routes) {
    if (!route.previousRef || route.previousRef === route.apiKeyEnv) continue;
    const key = `${route.previousRef}\u0000${route.apiKeyEnv}`;
    if (!rotations.has(key)) rotations.set(key, { from: route.previousRef, to: route.apiKeyEnv, routes: [] });
    rotations.get(key).routes.push(route.route);
  }
  if (rotations.size) {
    stdout.write(t('导入不改写已有 reference：下面的旧 reference（连同原值）都保留着，需要时用 ovsa dsh model key unset 删除\n',
      'The import never overwrites an existing reference; these previous references keep their values (remove one with ovsa dsh model key unset)\n'));
    for (const rotation of rotations.values()) {
      stdout.write(`  ${rotation.routes.join(', ')}: ${rotation.from} → ${rotation.to}\n`);
    }
  }
  if (result.keptLive.length) {
    stdout.write(t(`注意：这些 reference 在导入期间被别的 route 占用，未删除：${result.keptLive.join(', ')}\n`,
      `Note: these references were claimed by another route while the import ran and were not removed: ${result.keptLive.join(', ')}\n`));
  }
  if (result.unverifiable.length) {
    stdout.write(t(`注意：这些 reference 没能确认是否为孤儿，未删除：${result.unverifiable.join(', ')}\n`,
      `Note: these references could not be confirmed as orphans and were not removed: ${result.unverifiable.join(', ')}\n`));
  }
  writeNotes(stdout, plan.notes);
  return 0;
}

function writeNotes(stdout, notes) {
  if (!notes.length) return;
  stdout.write(t(`注意（${notes.length}）：\n`, `Notes (${notes.length}):\n`));
  for (const note of notes) stdout.write(`  - ${note}\n`);
}

// A document hand-edited into an unusable shape leaves no way in through
// `settings.mutate`, so repair has to touch the file itself: back it up, write
// back the one row the settings service needs, then prove it took by asking the
// service again.
//
// The rewrite is the one destructive step in this command group, so it happens
// only with evidence that the document is what is wrong. The probe reports that
// evidence one of two ways: `descriptor: null` when DSH read the file but sees
// no usable llm-pi-ai entry, or the settings service's invalid/unreadable
// answer, which says it could not read the document at all. Any other probe
// failure — a control process that starts but never answers, a handshake
// timeout, a crash, a bridge too old — says nothing about the document, and
// rewriting there would destroy a configuration that may be perfectly valid.
// Those abort before the first byte is written.
async function repairCommand({ argv, env, stdout, prompter }) {
  const { positionals, flags } = parseArguments(argv, { values: ['--instance'] });
  if (positionals.length) throw new UsageError(t('repair 不接受参数', 'repair takes no arguments'));
  const { agent, deployment } = await resolveInstance({ env, prompter, requested: instanceFlag(flags), question: modelInstanceQuestion() });
  const document = settingsDocumentPath(agent);
  const probe = () => withControl({ agent, deployment, env }, async (client) => {
    let descriptor;
    let unreadable = null;
    try {
      ({ descriptor } = await client.describeSettings());
    } catch (error) {
      // A settings answer that says the document cannot be read carries the same
      // information as `descriptor: null`. It is still a successful probe: the
      // service answered. Everything else — no answer at all — propagates, so
      // repair aborts instead of guessing.
      unreadable = unreadableDocumentAnswer(error);
      if (unreadable === null) throw error;
      descriptor = null;
    }
    return {
      descriptor,
      unreadable,
      routes: descriptor ? Object.keys(descriptor.value?.providers ?? {}).length : 0,
    };
  });

  let health;
  try {
    health = await probe();
  } catch (error) {
    const detail = error instanceof DshControlError ? error.stderr.trim() : '';
    const hint = documentParseHint(error, document);
    throw new Error(t(
      `无法判断设置文档是否需要修复，未做任何改动（${document}）。\n`
      + `原因：${error.message}${detail ? `\n${detail}` : ''}\n`
      + '请重试；如果一直失败，先确认 dsh 本身能正常启动（DSH_BIN / 版本）；'
      + '如果 dsh 正是因为这个文档才起不来，请手工改好或移走它。',
      `Cannot tell whether the settings document needs repair; nothing was changed (${document}).\n`
      + `Reason: ${error.message}${detail ? `\n${detail}` : ''}\n`
      + 'Retry; if it keeps failing, check that dsh itself starts (DSH_BIN / version). '
      + 'If this document is what keeps dsh from starting, fix or move it by hand.')
      + hint);
  }
  if (health.descriptor) {
    stdout.write(t(
      `配置可写，无需修复（${health.routes} 个 route，revision ${health.descriptor.revision}）。\n`,
      `The document is writable; nothing to repair (${health.routes} route(s), revision ${health.descriptor.revision}).\n`));
    return 0;
  }

  stdout.write(health.unreadable
    ? t(`修复前：${health.unreadable}\n`, `Before repair: ${health.unreadable}\n`)
    : t('修复前：llm-pi-ai 配置项不可写\n', 'Before repair: the llm-pi-ai entry is not writable\n'));
  // The probe cannot distinguish "the document is unreadable" from "an upper layer
  // disabled the entry", and the rewrite only helps the first. Refusing here keeps
  // the routes of the second case intact instead of destroying them for nothing.
  const overriding = overridingLayer({ dataDir: agent.dataDir });
  if (overriding) {
    throw new Error(t(
      `llm-pi-ai 被上层覆盖层接管了（${overriding.kind}：${overriding.file}），改写 ${document} 修不好它，反而会丢掉里面全部 route。未做任何改动。\n`
      + `请删掉那个文件里的 llm-pi-ai 条目后重试；如果它必须留着，就让 route 配在那一层。`,
      `An upper layer owns llm-pi-ai (${overriding.kind}: ${overriding.file}); rewriting ${document} cannot fix it and would drop every route it holds. Nothing was changed.\n`
      + 'Remove the llm-pi-ai entry from that file and retry; if it has to stay, configure the routes in that layer instead.'));
  }
  const original = readFileIfExists(document);
  // Say what the rewrite costs before it costs it: the minimal document has no
  // provider section, so every route in the backup disappears from the menu.
  const discardedNote = original === null ? null : discardedNoteFor(original);
  const backup = original === null ? null : backupFile(document, original);
  stdout.write(backup
    ? t(`已备份到 ${backup}\n`, `Backed up to ${backup}\n`)
    : t(`没有可备份的文档（${document} 不存在）\n`, `Nothing to back up (${document} does not exist)\n`));
  if (discardedNote !== null) stdout.write(discardedNote);
  writeMinimalSettingsDocument(document, { expected: original });
  stdout.write(t(`已写入最小 llm-pi-ai 文档：${document}\n`, `Wrote the minimal llm-pi-ai document: ${document}\n`));

  let verified;
  try {
    verified = await probe();
  } catch (error) {
    throw new Error(t(
      `修复后仍然不可写：${error.message}${backup ? `（原文档备份在 ${backup}）` : ''}`,
      `Still not writable after repair: ${error.message}${backup ? ` (the original is backed up at ${backup})` : ''}`));
  }
  if (!verified.descriptor) {
    throw new Error(t(
      `修复后 llm-pi-ai 仍不可写${verified.unreadable ? `（${verified.unreadable}）` : ''}${backup ? `（原文档备份在 ${backup}）` : ''}：请检查实例的 DSH 安装是否启用了 model 插件。`,
      `llm-pi-ai is still not writable after repair${verified.unreadable ? ` (${verified.unreadable})` : ''}${backup ? ` (the original is backed up at ${backup})` : ''}: check that the instance’s DSH install enables the model plugin.`));
  }
  stdout.write(t(
    `验证通过：${verified.routes} 个 route，revision ${verified.descriptor.revision}。\n`,
    `Verified: ${verified.routes} route(s), revision ${verified.descriptor.revision}.\n`));
  return 0;
}

// Store or delete one credential through DSH's own credentials service. Shared
// by `key set`/`key unset` and the interactive menu.
async function setCredentialRef({ agent, deployment, env, ref, value, stdout }) {
  await withControl({ agent, deployment, env }, (client) => client.setCredential(ref, value));
  stdout.write(t(`已保存 credential ${ref}\n`, `Stored credential ${ref}\n`));
}

async function unsetCredentialRef({ agent, deployment, env, ref, stdout }) {
  await withControl({ agent, deployment, env }, (client) => client.unsetCredential(ref));
  stdout.write(t(`已删除 credential ${ref}\n`, `Removed credential ${ref}\n`));
}

async function keyCommand({ argv, env, stdin, stdout, prompter }) {
  const [action, ...rest] = argv;
  if (action !== 'set' && action !== 'unset') {
    throw new UsageError(t('key 只能是 set 或 unset', 'key takes set or unset'));
  }
  const { positionals, flags } = parseArguments(rest, { values: ['--instance'], booleans: ['--stdin'] });
  if (positionals.length !== 1) {
    throw new UsageError(t(`key ${action} 需要且只需要一个 credential reference`,
      `key ${action} takes exactly one credential reference`));
  }
  const ref = credentialRef(positionals[0]);
  if (flags['--stdin'] && action === 'unset') throw new UsageError(t('key unset 不需要 --stdin', 'key unset takes no --stdin'));
  const { agent, deployment } = await resolveInstance({ env, prompter, requested: instanceFlag(flags), question: modelInstanceQuestion() });
  if (action === 'unset') {
    await unsetCredentialRef({ agent, deployment, env, ref, stdout });
    return 0;
  }
  const value = await readCredentialValue({ ref, stdin, forceStdin: flags['--stdin'] === true, prompter });
  if (!value) {
    throw new Error(t('没有读到密钥值：请用管道或 --stdin 提供，例如 echo "$KEY" | ovsa dsh model key set REF --stdin',
      'No credential value arrived: pipe one or pass --stdin, e.g. echo "$KEY" | ovsa dsh model key set REF --stdin'));
  }
  await setCredentialRef({ agent, deployment, env, ref, value, stdout });
  return 0;
}

/**
 * Run `ovsa dsh model` (arguments after the `model` token).
 * @param {string[]} argv - subcommand and its arguments.
 * @param {object} [context] - injectable environment and streams.
 * @returns {Promise<number>} the process exit code.
 */
export async function dshModelCommand(argv, {
  env = process.env, stdin = process.stdin, stdout = process.stdout, stderr = process.stderr,
  prompter = null,
} = {}) {
  try {
    const context = { env, stdin, stdout, stderr, prompter };
    // No subcommand: the menu in a terminal, and exactly today's list anywhere
    // else. A pipe must never see a prompt.
    if (isBareInvocation(argv)) {
      return prompter?.tty ? await interactiveCommand({ ...context, argv }) : await listCommand({ ...context, argv });
    }
    const [subcommand = 'list', ...rest] = argv;
    if (subcommand === 'help' || subcommand === '-h' || subcommand === '--help') {
      stdout.write(`${usageText()}\n`);
      return 0;
    }
    switch (subcommand) {
      case 'list': return await listCommand({ ...context, argv: rest });
      case 'remove': return await removeCommand({ ...context, argv: rest });
      case 'import': return await importCommand({ ...context, argv: rest });
      case 'repair': return await repairCommand({ ...context, argv: rest });
      case 'key': return await keyCommand({ ...context, argv: rest });
      default:
        stderr.write(t(`ovsa dsh model：没有「${subcommand}」这个子命令\n\n`, `ovsa dsh model: there is no "${subcommand}" subcommand\n\n`));
        stderr.write(`${usageText()}\n`);
        return 2;
    }
  } catch (error) {
    if (error instanceof UsageError) {
      stderr.write(`${error.message}\n\n${usageText()}\n`);
      return 2;
    }
    // The transport still speaks English diagnostics; the framing around it is
    // in the user's language, and the stderr tail explains a failed DSH boot.
    if (error instanceof DshControlError) {
      const detail = error.stderr.trim();
      // A DSH that cannot start is often a hand-edited profile document, which
      // is exactly what `repair` recovers from; say so instead of leaving the
      // user with a boot trace.
      const hint = /before answering/i.test(error.message)
        ? t('\n如果刚手工改过实例的 DSH 配置，运行 ovsa dsh model repair 修复。',
          '\nIf the instance’s DSH configuration was just hand-edited, run ovsa dsh model repair.')
        : '';
      throw new Error(t(
        `与临时 DSH 子进程交互失败：${error.message}${detail ? `\n${detail}` : ''}${hint}`,
        `Talking to the temporary DSH process failed: ${error.message}${detail ? `\n${detail}` : ''}${hint}`));
    }
    throw error;
  }
}
