// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { loginWeixin } from '../../daemon/src/weixin/auth.mjs';
import { WeixinState } from '../../daemon/src/weixin/state.mjs';
import { bundledLarkCli } from '../../daemon/src/lark-cli-bin.mjs';
import { managedPiBin } from '../../daemon/src/pi-runtime.mjs';
import { resolveRolePromptPath } from '../../daemon/src/conversation-policy.mjs';
import { LarkCli } from './lark-cli.mjs';
import {
  LarkCredentialsError, LarkPolicyError, LarkRegistrationError, MESSAGE_EVENT, grantedUserScopes, inspectApp, lacksAny, missingConfig, readApp,
  scanApp, tenantToken,
} from './lark-app.mjs';
import {
  ROLE_PRESETS, WEB_PORT, allocatePort, availableInstanceOvConfig, instanceIdProblem, readManifest, suggestInstanceId, validateManifest, vikaCommand,
  vikaHome, withChangedInstance, withDefaultTempDir, withInstance, writeManifest,
} from './manifest.mjs';
import { detectOpenViking, writeInstanceOvConfig } from './ov.mjs';
import { configureOpenViking, prepareInstanceOpenViking, saveOVConnection } from './ov-setup.mjs';
import { detectRuntimes, which } from './runtimes.mjs';
import { showScan } from './qr.mjs';
import { restartCommand, startCommand } from './control.mjs';
import { removeInstance } from './remove-instance.mjs';
import { autostartCommand, autostartEnabled } from './autostart.mjs';
import { openingBanner } from './banner.mjs';
import { box, padEnd, width } from './ui.mjs';
import { LANGUAGES, languageChoice, list, saveLanguage, setLanguage, t } from './i18n.mjs';
import { releaseInfo } from './version.mjs';
import { EmailCancelled, askEmail, emailAccountPath, emailInstanceId, mailboxesOf, writeEmailAccount } from './email.mjs';
import { GoBack } from './prompt.mjs';
import { IM_LABELS, connectorsOf, ignoredFieldsNotice, imList, primaryMeaning } from './instances.mjs';
import { connectTelegram } from './telegram.mjs';
import { connectQQ } from './qq.mjs';
import { connectSlack } from './slack.mjs';

// `vika setup`: add, change or remove an instance. A new instance is asked
// question by question; a change is a list of every setting with its current
// value, where the owner opens the ones to change and saves. Then the QR scans
// (only for a newly connected IM), then the manifest is written and the
// instance (re)started. A new instance's OV config is prepared and checked
// before IM setup, then removed on cancellation until its manifest is saved.
// It is also the one place that connects an IM; `vika login` only renews.

const PERMISSIONS = new URL('../../../permission.json', import.meta.url);

function resolveLarkCli(env) {
  const bundled = bundledLarkCli();
  return bundled === 'lark-cli' ? which('lark-cli', env) : bundled;
}

export function expandHome(value, homeDir = os.homedir()) {
  return value === '~' ? homeDir : value.replace(/^~(?=\/)/, homeDir);
}

function shortenHome(value, homeDir) {
  return value.startsWith(`${homeDir}${path.sep}`) ? path.join('~', path.relative(homeDir, value)) : value;
}

export function larkScopes() {
  const { scopes } = JSON.parse(fs.readFileSync(PERMISSIONS, 'utf8'));
  return { tenant: scopes.tenant, user: scopes.user };
}

const imChoices = () => [
  { label: IM_LABELS.lark, value: 'lark', hint: larkAccountNote() },
  { label: IM_LABELS.weixin, value: 'weixin' },
  { label: IM_LABELS.email, value: 'email',
    hint: t('IMAP/SMTP；新邮件提醒和回信确认发到主渠道', 'IMAP/SMTP; new-mail alerts and reply approvals go to the primary channel') },
  { label: 'Telegram', value: 'telegram',
    hint: t('官方 Bot API；需要 @BotFather 创建 Bot 和 token', 'official Bot API; needs a bot and its token from @BotFather') },
  { label: 'Slack', value: 'slack',
    hint: t('工作区里的 Slack App（新建或用已有的，Socket Mode）；需要粘贴两个 token', 'a new or existing app in your Slack workspace (Socket Mode); paste two tokens') },
  { label: 'QQ', value: 'qq',
    hint: t('腾讯官方机器人；需要 AppID / AppSecret 和本人私聊验证码', 'official Tencent bot; needs AppID / AppSecret and a private-chat verification code') },
];

// IMs connected by pasting tokens rather than scanning a code.
const TOKEN_IMS = ['telegram', 'slack', 'qq'];

// Where the Feishu/Lark bot app comes from. A scan can only pick an app the
// scanner owns or administers, and a test tenant cannot scan at all; then the
// App ID and App Secret are typed in, and the app's settings are the owner's
// to complete in the developer console.
const larkSources = () => [
  { value: 'create', label: t('扫码新建一个机器人', 'Scan to create a new bot'),
    hint: t('一次扫码建好应用、开好权限并发布', 'one scan makes the app, grants its permissions and publishes it') },
  { value: 'select', label: t('扫码选择已有的机器人', 'Scan to pick an existing bot'),
    hint: t('你是所有者或管理员的应用；缺的权限和事件在确认页补上', 'an app you own or administer; what it lacks is added on the confirm page') },
  { value: 'manual', label: t('手动填 App ID 和 App Secret', 'Enter the App ID and App Secret'),
    hint: t('不是所有者或管理员、在测试企业等扫不了码时；缺的配置要去开放平台补',
      'when a scan cannot be used (not an owner or administrator, a test tenant); what the app lacks is added in the developer console') },
];

// How the instance gets its Feishu/Lark app: `keep` the one it has, or one
// of larkSources. None without Feishu.
function larkSource(state, current) {
  if (!state.connectors.includes('lark')) return null;
  if (state.larkApp) return state.larkApp.source;
  return current && connectorsOf(current).includes('lark') ? 'keep' : 'create';
}

// The app as the settings list and the summaries show it; `needs` is what
// the current app lacks of this release (larkNeeds), when it lacks anything.
function larkAppShown(state, current, needs = null) {
  const source = larkSource(state, current);
  if (source === 'manual' && state.larkApp?.app) return t(`应用 ${state.larkApp.app.appId}`, `the app ${state.larkApp.app.appId}`);
  if (source === 'keep' && state.larkRepair) {
    return t(`现在的应用，保存时扫码补齐缺的 ${needs.count} 项`, `the current app; saving adds the ${needs.count} missing with a scan`);
  }
  if (source === 'keep') return needs ? t(`现在的应用（缺 ${needs.count} 项：${needsShown(needs)}）`, `the current app (${needs.count} missing: ${needsShown(needs)})`) : t('现在的应用', 'the current app');
  return larkSources().find(({ value }) => value === source)?.label ?? t('没有接', 'none');
}

// What larkNeeds found missing, in a few words.
function needsShown({ missing, login }) {
  const scopes = missing.scopes.tenant.length + missing.scopes.user.length;
  return list([
    ...(scopes ? [t(`${scopes} 个权限`, `${scopes} ${scopes === 1 ? 'permission' : 'permissions'}`)] : []),
    ...(missing.events?.includes(MESSAGE_EVENT) ? [t('接收消息事件', 'the message event')] : []),
    ...(missing.events?.some((event) => event !== MESSAGE_EVENT) ? [t('文档评论事件', 'the document comment event')] : []),
    ...(missing.callbacks.length ? [t('卡片回调', 'the card callback')] : []),
    ...(login.length ? [t('你的授权', 'your authorization')] : []),
  ]);
}

// An app made elsewhere may already be someone's receiver. Picking one by
// scan changes it, on the platform's confirm page: the page adds what it
// lacks and publishes, withdrawing a version under review first (seen in the
// page's own code), and an app with a webhook keeps it.
function existingAppNotes(source) {
  const { tenant, user } = larkScopes();
  return [
    ...(source === 'select' ? [t(
      `扫码后飞书的确认页会列出要给它补的权限（最多 ${tenant.length + user.length} 个）、消息事件和卡片回调，确认后发布一个新版本（企业可能要管理员审核）；它要是有版本正在审核，会先被撤回。不确认就什么都不改。`,
      `After the scan, Feishu/Lark's confirm page lists what it will add (up to ${tenant.length + user.length} permissions, the message event and the card callback); confirming publishes a new version (an organization may need admin approval), and a version under review is withdrawn first. Nothing changes unless you confirm.`,
    )] : []),
    t('同一个飞书应用不能两处同时收消息：长连接每条消息只随机推给其中一个连接，确认它没被别的程序（OpenClaw、botmux、另一台机器上的 Vika）用着。',
      'One Feishu/Lark app cannot receive messages in two places: the long connection hands each message to one of its connections at random, so make sure no other program (OpenClaw, botmux, Vika on another machine) uses it.'),
    t('Vika 只接用长连接接收消息的机器人：用 webhook 的，先在开放平台「事件与回调」把事件和回调都改成长连接（原来的服务随之收不到），再来接。',
      'Vika only takes bots that receive over the long connection: for one on a webhook, first switch its events and callbacks to the long connection under Events & Callbacks in the developer console (the webhook service then stops getting them).'),
  ];
}

// Vika receives over the long connection only. No API tells how an app's
// events arrive; its callbacks on a webhook say it has one.
const webhookRefusal = () => t(
  '这个机器人的卡片回调走 webhook，事件多半也是，Vika 接不了：在开放平台「事件与回调」把事件和回调都改成长连接（原来用 webhook 的服务就收不到了）再来接它，或者换一个机器人。',
  'This bot\'s card callbacks go to a webhook, and its events most likely do too, so Vika cannot take it: switch both to the long connection under Events & Callbacks in the developer console (the webhook service then stops getting them) and connect it again, or pick another bot.',
);

// Feishu signed in with a Doubao account makes and publishes an app, but the
// platform never gives the app a token (LarkPolicyError): it cannot run a
// bot. Said where Feishu is first offered, and again when it happens.
// Registering with a phone number makes an organization (Feishu's help
// center: 注册即视为你创建了一个企业), so that is what the owner is told.
const larkAccounts = () => t('手机号注册的飞书账号（注册时会建一个免费企业，你是管理员）或公司的飞书账号',
  'a Feishu/Lark account registered with a phone number (registering makes a free organization you administer) or a company one');

const larkAccountNote = () => t(`${larkAccounts()}；用豆包账号登录的飞书不行`,
  `${larkAccounts()}; Feishu signed in with a Doubao account will not do`);

// Why the platform gives the app no token, and what to do instead. `made`:
// the scan has just made the app, which stays there unusable; `typed`: the
// App ID was typed in, so another app is what to try.
const policyRefusal = (appId, error, { made = false, typed = false } = {}) => t(
  `飞书不给应用 ${appId} 发访问凭证（${error.message}）：它所在的飞书账号不能运行机器人，用豆包账号登录的飞书就是这样。${made ? '这个应用已经建好但用不了，可以在开放平台删掉。' : ''}${typed
    ? `换一个${larkAccounts()}下的应用，或者不接飞书，先用微信等其他渠道。`
    : `用${larkAccounts()}重新扫码，或者回去不接飞书，先用微信等其他渠道。`}`,
  `Feishu/Lark gives the app ${appId} no token (${error.message}): the account it belongs to cannot run bots, as with Feishu signed in with a Doubao account. ${made ? 'The app was made but cannot be used; you may delete it in the developer console. ' : ''}${typed
    ? `Use an app of ${larkAccounts()}, or connect another IM such as WeChat instead.`
    : `Scan again with ${larkAccounts()}, or go back and connect another IM such as WeChat instead.`}`,
);

const ims = (state) => state.connectors.filter((connector) => connector !== 'email');

// Which instance to change, or a new one. An instance whose Feishu/Lark app
// lacks what this release needs says so; its 飞书应用 setting completes it.
async function chooseTarget(prompter, manifest, lacking = []) {
  const agents = manifest?.agents ?? [];
  if (agents.length === 0) return null;
  const needsOf = (agent) => lacking.find((needs) => needs.agent.id === agent.id);
  return prompter.select(t('要做什么', 'What to do'), [
    { label: t('新建一个实例', 'Add an instance'), value: null },
    ...agents.map((agent) => ({
      label: t(`修改 ${agent.displayName}（${agent.id}）`, `Change ${agent.displayName} (${agent.id})`),
      hint: `${imList(connectorsOf(agent), agent.emailAccounts?.length)}${needsOf(agent) ? t(` · 飞书应用缺 ${needsOf(agent).count} 项`, ` · its Feishu/Lark app lacks ${needsOf(agent).count}`) : ''}`,
      value: agent,
    })),
    { label: t('删除一个实例', 'Remove an instance'), value: 'remove' },
  ], { back: false });
}

// Back to the first question, 要做什么: setup starts over from there.
class StartOver extends Error {}

// ← where a menu has a way out: the menu's answer instead of going back.
async function orElse(ask, menu, value) {
  try {
    return await ask();
  } catch (error) {
    if (menu && error instanceof GoBack) return value;
    throw error;
  }
}

// A runtime that is no longer detected stays selectable as it is.
function keptRuntime(current, runtimes) {
  if (!current || runtimes.some((runtime) => runtime.available && runtime.model === current.model)) return null;
  return { label: t(`保持现在的（${current.model}）`, `Keep the current one (${current.model})`), model: current.model, available: true, note: '', kept: true };
}

// The answers being put together: defaults for a new instance, or the
// instance's own values when changing one. Mailboxes change only at the end.
function initialState({ id, env, homeDir, current, runtimes, kept }) {
  const connectors = current ? [...connectorsOf(current)] : ['lark'];
  return {
    id,
    dataDir: current?.dataDir ?? path.join(vikaHome(env), 'data', id),
    displayName: current?.displayName ?? 'Vika',
    // A shipped role by name, or the absolute path of the owner's own file;
    // roleSeed is the role a new file of theirs starts from (written on save).
    role: current ? current.role ?? null : ROLE_PRESETS[0],
    roleSeed: null,
    workspace: current?.workspace ?? path.join(homeDir, 'vika-workspace', id),
    runtime: kept ?? runtimes.find((runtime) => runtime.available && (!current || runtime.model === current.model)),
    connectors,
    channel: current?.channel ?? connectors[0],
    ovConfig: current?.ovConfig,
    switches: Object.fromEntries(Object.entries(SWITCH_DEFAULTS).map(([key, value]) => [key, current?.[key] ?? value])),
    larkApp: null,
    mail: { kept: current ? mailboxesOf(current) : [], added: [], updated: new Map(), removed: [] },
  };
}

const mailboxList = (state) => [...state.mail.kept, ...state.mail.added];
const addressOf = (mailbox) => mailbox.account?.address ?? mailbox.address ?? mailbox.id;

// What the mailbox changes come to, for the manifest, the files and the summary.
function mailPlan(state) {
  return {
    list: mailboxList(state).map(({ id, config }) => ({ id, config })),
    write: [...state.mail.added, ...state.mail.updated.values()].map(({ config, account }) => ({ config, account })),
    removed: state.mail.removed,
    added: state.mail.added.map(addressOf),
    updated: [...state.mail.updated.values()].map(addressOf),
  };
}

async function askName({ prompter, current }, state) {
  const hasLark = current && connectorsOf(current).includes('lark');
  state.displayName = await prompter.text(hasLark
    ? t('机器人名字（已经建好的飞书应用名不会跟着改）', 'Bot name (the Feishu/Lark app made before keeps its name)')
    : t('机器人名字（新建的飞书应用也叫这个，@它时用这个名字）', 'Bot name (a new Feishu/Lark app is named so too, to @mention it by)'), {
    label: t('机器人名字', 'Bot name'),
    initial: state.displayName,
    validate: (value) => (value.length > 40 ? t('最多 40 个字符', 'At most 40 characters') : null),
  });
}

async function askWorkspace({ prompter, env, homeDir }, state) {
  state.workspace = path.resolve(expandHome(await prompter.text(t('工作目录（agent 在这里读写文件）', 'Workspace (where the agent reads and writes files)'), {
    label: t('工作目录', 'Workspace'),
    initial: shortenHome(state.workspace, homeDir),
    validate: (value) => {
      const resolved = path.resolve(expandHome(value, homeDir));
      if (resolved === homeDir) return t('不要直接用 home 目录，换一个子目录', 'Not the home directory itself; use a folder inside it');
      if (resolved.startsWith(`${vikaHome(env)}${path.sep}`)) return t('不能放在 ~/.vika 里', 'Not inside ~/.vika');
      return null;
    },
  }), homeDir));
}

// What each shipped role is for, as setup offers it.
const ROLE_CHOICES = () => ({
  colleague: {
    label: t('AI 同事', 'AI colleague'),
    hint: t('你的 AI 分身：在工作群里像你一样答疑、跟进，和同事及其他 bot 协作', 'your AI counterpart: answers and follows up in work chats as you would, with colleagues and other bots'),
  },
  assistant: {
    label: t('个人助理', 'Personal assistant'),
    hint: t('只为你工作：记事、提醒，主动把值得知道的事告诉你', 'works for you alone: remembers, reminds, and tells you what is worth knowing'),
  },
  qa: {
    label: t('答疑助手', 'Q&A assistant'),
    hint: t('在答疑群里查证后把问题讲明白，注意隐私，无关的话题不接', 'answers questions in Q&A groups after checking the sources, guards privacy, skips what is off-topic'),
  },
});

const customRole = (role) => Boolean(role) && path.isAbsolute(role);

function roleShown(state, homeDir) {
  if (!state.role) return t('没有设置（只用默认规则）', 'none (default rules only)');
  return customRole(state.role) ? shortenHome(state.role, homeDir) : ROLE_CHOICES()[state.role].label;
}

async function askRole({ prompter, env, homeDir }, state) {
  const choices = ROLE_CHOICES();
  const options = [
    ...ROLE_PRESETS.map((name) => ({ ...choices[name], value: name })),
    { label: t('自己写 prompt', 'My own prompt'), hint: t('用你指定的 Markdown 文件，可以从上面任一个角色改起', 'a Markdown file of yours, which can start from any role above'), value: 'custom' },
  ];
  const at = customRole(state.role) ? options.length - 1 : Math.max(0, ROLE_PRESETS.indexOf(state.role));
  const choice = await prompter.select(t('它的角色', 'Its role'), options, { initial: at, label: t('角色', 'Role') });
  if (choice !== 'custom') {
    state.role = choice;
    state.roleSeed = null;
    return;
  }
  const initial = customRole(state.role) ? state.role : path.join(vikaHome(env), 'roles', `${state.id}.md`);
  const file = path.resolve(expandHome(await prompter.text(t('角色 prompt 文件（Markdown）', 'Role prompt file (Markdown)'), {
    label: t('角色文件', 'Role file'),
    initial: shortenHome(initial, homeDir),
    validate: (value) => {
      const resolved = path.resolve(expandHome(value, homeDir));
      return fs.existsSync(resolved) && !fs.statSync(resolved).isFile() ? t('这不是一个文件', 'That is not a file') : null;
    },
  }), homeDir));
  state.role = file;
  state.roleSeed = fs.existsSync(file) ? null : await prompter.select(
    t('这个文件还没有。先用哪个角色的 prompt 填上？之后可以随便改', 'That file does not exist yet. Start it from which role? Change it freely afterwards'),
    [...ROLE_PRESETS.map((name) => ({ label: choices[name].label, value: name })), { label: t('空白', 'Blank'), value: '' }],
    { label: t('从哪个角色开始', 'Start from') },
  );
}

// A role file of the owner's own, begun from a shipped role (or blank).
function writeRoleFile({ role, roleSeed }, id) {
  if (roleSeed === null || fs.existsSync(role)) return;
  const note = `<!-- ${roleSeed ? `Started from Vika's "${roleSeed}" role. ` : ''}This is ${id}'s role: edit it freely, then run vika restart ${id}. Comments like this one are not sent to the model. -->\n\n`;
  fs.mkdirSync(path.dirname(role), { recursive: true, mode: 0o700 });
  fs.writeFileSync(role, `${note}${roleSeed ? fs.readFileSync(resolveRolePromptPath(roleSeed), 'utf8') : ''}`, { mode: 0o600 });
}

async function askRuntime({ prompter, runtimes, kept }, state) {
  const choices = [...(kept ? [kept] : []), ...runtimes];
  state.runtime = await prompter.select(t('Agent 运行时', 'Agent runtime'), choices.map((runtime) => ({
    label: runtime.label, value: runtime, hint: runtime.note, disabled: !runtime.available,
  })), { initial: Math.max(0, choices.findIndex((runtime) => runtime.available && runtime.model === state.runtime?.model)) });
}

// What a step says besides asking. A new instance's steps stay on screen;
// the change list is drawn again after each setting, so there it shows
// above the list.
function note(ctx, text) {
  if (ctx.menu) ctx.notes.push(text);
  else ctx.prompter.info(text);
}

// A primary IM that was dropped moves to what is left, Feishu first.
function keepChannel(ctx, state) {
  if (ims(state).includes(state.channel)) return;
  const next = ims(state).includes('lark') ? 'lark' : ims(state)[0];
  note(ctx, t(`主渠道原来是${IM_LABELS[state.channel]}，它不再接入，改成${IM_LABELS[next]}`,
    `The primary channel was ${IM_LABELS[state.channel]}, which is no longer connected; it is now ${IM_LABELS[next]}`));
  state.channel = next;
}

function dropMailboxes(state) {
  state.mail.removed.push(...state.mail.kept);
  state.mail.kept = [];
  state.mail.added = [];
  state.mail.updated.clear();
}

// One more mailbox. In the change menu, giving up on it goes back to the list.
async function addMailbox({ prompter, env, connect, menu, checkOV }, state) {
  let account;
  await checkOV();
  try {
    account = await connect.email({ prompter, taken: mailboxList(state).map(addressOf) });
  } catch (error) {
    if (menu && (error instanceof EmailCancelled || error instanceof GoBack)) return false;
    throw error;
  }
  const id = emailInstanceId(account.address, mailboxList(state).map((mailbox) => mailbox.id));
  state.mail.added.push({ id, config: emailAccountPath(env, state.id, id), account });
  return true;
}

async function addMailboxes(ctx, state) {
  do {
    if (!await addMailbox(ctx, state)) break;
  } while (await orElse(() => ctx.prompter.confirm(t('再接一个邮箱？', 'Add another mailbox?'), false), ctx.menu, false));
  const has = mailboxList(state).length > 0;
  state.connectors = state.connectors.filter((connector) => connector !== 'email').concat(has ? ['email'] : []);
}

async function askIms(ctx, state) {
  let connectors;
  for (;;) {
    const choices = imChoices();
    connectors = await ctx.prompter.multiselect(t('接入哪些 IM', 'Which IMs to connect'), choices,
      { initial: choices.map(({ value }, index) => (state.connectors.includes(value) ? index : -1)).filter((index) => index >= 0) });
    if (connectors.some((connector) => connector !== 'email')) break;
    ctx.prompter.warn(t('邮箱不能单独接：新邮件提醒和回信确认要发到飞书、微信、Telegram、Slack 或 QQ，再选一个。',
      'Email cannot be the only one: its alerts and reply approvals go to Feishu/Lark, WeChat, Telegram, Slack or QQ, so pick one of those too.'));
  }
  const hadEmail = state.connectors.includes('email');
  const hadLark = state.connectors.includes('lark');
  state.connectors = connectors;
  if (hadEmail && !connectors.includes('email')) dropMailboxes(state);
  if (!connectors.includes('lark')) state.larkApp = null;
  if (!connectors.includes('lark') && state.switches.meetingNotes) {
    state.switches.meetingNotes = false;
    note(ctx, t('不再接飞书，会议纪要入库随之关掉', 'Without Feishu/Lark, meeting notes are switched off'));
  }
  // When changing an instance, a newly ticked mailbox is connected right away,
  // and a newly ticked Feishu asks where its app comes from.
  if (ctx.menu && !hadEmail && connectors.includes('email')) await addMailboxes(ctx, state);
  if (ctx.menu && !hadLark && connectors.includes('lark')) await orElse(() => askLarkApp(ctx, state), true);
  if (ctx.menu) keepChannel(ctx, state);
}

// Where the Feishu/Lark app comes from: a scan, or the App ID and App Secret
// typed in and checked here, like a mailbox. An instance that has Feishu
// can keep its app. Giving up on typing them asks again where it comes from.
async function askLarkApp(ctx, state) {
  const { prompter, current } = ctx;
  if (!state.connectors.includes('lark')) {
    if (ctx.menu) note(ctx, t('没有接飞书：先在「接入的 IM」里勾上飞书', 'Feishu/Lark is not connected: tick it in Connected IMs first'));
    return;
  }
  const connected = Boolean(current && connectorsOf(current).includes('lark'));
  const needs = connected ? ctx.larkNeeds : null;
  const choices = [
    ...(needs ? [{ value: 'repair', label: t(`扫码补齐缺的 ${needs.count} 项`, `Add the ${needs.count} missing with a scan`),
      hint: t(`${needsShown(needs)}；保存时扫码，你要是这个应用的所有者或管理员`, `${needsShown(needs)}; scanned on saving, by an owner or administrator of the app`) }] : []),
    ...(connected ? [{ value: 'keep', label: t('不换，用现在的应用', 'Keep the current app') }] : []),
    ...larkSources(),
  ];
  const chosen = state.larkRepair ? 'repair' : larkSource(state, current);
  const mark = prompter.mark();
  for (;;) {
    const source = await prompter.select(t('飞书机器人', 'Feishu/Lark bot'), choices,
      { initial: Math.max(0, choices.findIndex(({ value }) => value === chosen)) });
    state.larkRepair = source === 'repair';
    if (source !== 'manual') {
      state.larkApp = ['keep', 'repair'].includes(source) ? null : { source };
      break;
    }
    try {
      state.larkApp = { source, app: await enterApp(ctx, { previous: state.larkApp?.app }) };
      break;
    } catch (error) {
      if (!(error instanceof GoBack)) throw error;
      prompter.rewind(mark);
    }
  }
  if (state.larkApp && state.larkApp.source !== 'create') for (const text of existingAppNotes(state.larkApp.source)) note(ctx, text);
}

// The primary IM is its own question, Feishu unless it is left out; with a
// single IM there is nothing to ask.
async function askChannel(ctx, state) {
  const choices = ims(state);
  if (choices.length > 1) {
    const preferred = [state.channel, 'lark'].find((im) => choices.includes(im)) ?? choices[0];
    state.channel = await ctx.prompter.select(t(`主渠道（${primaryMeaning()}）`, `Primary channel (${primaryMeaning()})`),
      choices.map((connector) => ({ label: IM_LABELS[connector], value: connector })),
      { initial: choices.indexOf(preferred), label: t('主渠道', 'Primary channel') });
    return;
  }
  state.channel = choices[0];
  if (ctx.menu) {
    note(ctx, t(`只接了${IM_LABELS[state.channel]}，主渠道只能是它；要换，先在「接入的 IM」里多接一个`,
      `Only ${IM_LABELS[state.channel]} is connected, so it is the primary channel; to change that, connect another IM first`));
  }
}

// The instance's mailboxes, from a menu until done; none yet means adding.
async function askMailboxes(ctx, state) {
  if (mailboxList(state).length === 0) {
    await addMailboxes(ctx, state);
    return;
  }
  for (;;) {
    const kept = state.mail.kept;
    const addresses = list(mailboxList(state).map(addressOf));
    const choice = await orElse(() => ctx.prompter.select(t(`邮箱（已接入：${addresses}）`, `Mailboxes (connected: ${addresses})`), [
      { label: t('不改了', 'Done'), value: { kind: 'done' } },
      { label: t('再接一个邮箱', 'Add a mailbox'), value: { kind: 'add' } },
      ...kept.map((mailbox) => ({ label: t(`更新 ${addressOf(mailbox)} 的密码`, `New password for ${addressOf(mailbox)}`), value: { kind: 'update', mailbox } })),
      ...mailboxList(state).map((mailbox) => ({ label: t(`移除 ${addressOf(mailbox)}`, `Remove ${addressOf(mailbox)}`), value: { kind: 'remove', mailbox } })),
    ], { label: t('邮箱', 'Mailboxes') }), ctx.menu, { kind: 'done' });
    if (choice.kind === 'done') break;
    if (choice.kind === 'add') await addMailbox(ctx, state);
    if (choice.kind === 'update') {
      await ctx.checkOV();
      try {
        const account = await ctx.connect.email({ prompter: ctx.prompter, fixed: choice.mailbox.account,
          taken: mailboxList(state).filter((mailbox) => mailbox !== choice.mailbox).map(addressOf) });
        state.mail.updated.set(choice.mailbox.id, { config: choice.mailbox.config, account });
      } catch (error) {
        if (!(error instanceof EmailCancelled || error instanceof GoBack)) throw error;
      }
    }
    if (choice.kind === 'remove') {
      const { mailbox } = choice;
      if (kept.includes(mailbox)) {
        kept.splice(kept.indexOf(mailbox), 1);
        state.mail.updated.delete(mailbox.id);
        state.mail.removed.push(mailbox);
      } else {
        state.mail.added.splice(state.mail.added.indexOf(mailbox), 1);
      }
    }
  }
  if (mailboxList(state).length === 0) state.connectors = state.connectors.filter((connector) => connector !== 'email');
  else if (!state.connectors.includes('email')) state.connectors.push('email');
}

// The instance's on/off settings, under 其他设置, and what each is when the
// manifest does not say.
const SWITCH_DEFAULTS = Object.freeze({ stagedReplies: false, naturalWriting: true, explicitSilence: false, ovAutoRecall: false, meetingNotes: false });
// Switching one of these changes the model's policy: a new session.
const POLICY_SWITCHES = new Set(['stagedReplies', 'naturalWriting', 'explicitSilence', 'meetingNotes']);

// A new personal assistant or AI colleague on Feishu/Lark stores the owner's
// meeting notes, unless another instance here already does; a Q&A bot, which
// serves other people, does not.
function defaultMeetingNotes(state, agents) {
  return ['colleague', 'assistant'].includes(state.role) && state.connectors.includes('lark')
    && !agents.some((agent) => agent.id !== state.id && agent.meetingNotes);
}

// Why meeting notes cannot be switched on here, or null.
function meetingNotesRefusal(ctx, state) {
  if (!state.connectors.includes('lark')) {
    return t('会议纪要来自飞书：先在「接入的 IM」里接上飞书', 'Meeting notes come from Feishu/Lark: connect it under Connected IMs first');
  }
  const holder = (ctx.agents ?? []).find((agent) => agent.id !== state.id && agent.meetingNotes);
  return holder ? t(`会议纪要已经由 ${holder.displayName}（${holder.id}）入库：一台机器上只有一个实例能开，先修改 ${holder.id} 把它关掉`,
    `${holder.displayName} (${holder.id}) stores the meeting notes already: only one instance on a machine can; switch it off there first`) : null;
}

const SWITCHES = () => [
  { key: 'stagedReplies', label: t('分段回复', 'Staged replies'),
    hint: t('复杂任务先表态，做的过程中发阶段性结论；简单问题仍只回一条', 'on a longer task, shows it is on it and shares findings along the way; a quick question still gets one reply') },
  { key: 'naturalWriting', label: t('自然写作', 'Natural writing'),
    hint: t('写得像人，去掉 AI 腔', 'writes like a person, without the AI tone') },
  { key: 'explicitSilence', label: t('显式不回复', 'Explicit silence'),
    hint: t('每条消息都要模型表态：回复，或记下不回；漏了提醒一次', 'the model decides on every message: a reply, or a note that it stays silent; a miss gets one reminder') },
  { key: 'ovAutoRecall', label: t('OpenViking 自动召回', 'OpenViking auto-recall'),
    hint: t('每轮自动召回 OpenViking 里的相关记忆', 'recalls related OpenViking memory on every turn') },
  { key: 'meetingNotes', label: t('会议纪要入库', 'Meeting notes'),
    hint: t('你参加的飞书会议，智能纪要和文字记录存进 OpenViking；一台机器上只有一个实例能开',
      'the Feishu/Lark AI notes and transcripts of your meetings go into OpenViking; one instance per machine') },
];

const onOff = (value) => (value ? t('开', 'on') : t('关', 'off'));

function switchesShown(state) {
  const on = SWITCHES().filter(({ key }) => state.switches[key]).map(({ label }) => label);
  return on.length ? t(`开着：${list(on)}`, `on: ${list(on)}`) : t('都关着', 'all off');
}

// Each switch with what it does, flipped where it is until Done; ← leaves
// them all as they were.
async function askSwitches(ctx, state) {
  const { prompter } = ctx;
  const before = { ...state.switches };
  const mark = prompter.mark();
  let cursor = 0;
  let notice = null;
  try {
    for (;;) {
      prompter.rewind(mark);
      if (notice) prompter.warn(notice);
      notice = null;
      const switches = SWITCHES();
      const labelWidth = Math.max(...switches.map(({ label }) => width(label))) + 2;
      const choice = await prompter.select(t('其他设置（选一项就切换开关）', 'Other settings (pick one to switch it)'), [
        ...switches.map(({ key, label, hint }, index) => ({ label: padEnd(label, labelWidth), hint: `${onOff(state.switches[key])} · ${hint}`, value: index })),
        { label: t('完成', 'Done'), value: 'done' },
      ], { initial: cursor, label: t('其他设置', 'Other settings') });
      if (choice === 'done') return;
      const { key } = switches[choice];
      cursor = choice;
      notice = key === 'meetingNotes' && !state.switches[key] ? meetingNotesRefusal(ctx, state) : null;
      if (!notice) state.switches[key] = !state.switches[key];
    }
  } catch (error) {
    if (error instanceof GoBack) Object.assign(state.switches, before);
    throw error;
  }
}

const SETTINGS = [
  { key: 'displayName', label: () => t('机器人名字', 'Bot name'), ask: askName, shown: (state) => state.displayName },
  { key: 'role', label: () => t('角色', 'Role'), ask: askRole, shown: (state, { homeDir }) => roleShown(state, homeDir) },
  { key: 'workspace', label: () => t('工作目录', 'Workspace'), ask: askWorkspace, shown: (state, { homeDir }) => shortenHome(state.workspace, homeDir) },
  { key: 'runtime', label: () => t('Agent 运行时', 'Agent runtime'), ask: askRuntime,
    shown: (state) => (state.runtime.kept ? state.runtime.model : state.runtime.label) },
  { key: 'ims', label: () => t('接入的 IM', 'Connected IMs'), ask: askIms, shown: (state) => imList(state.connectors) },
  { key: 'larkApp', label: () => t('飞书应用', 'Feishu/Lark app'), ask: askLarkApp,
    shown: (state, { current, larkNeeds }) => larkAppShown(state, current, larkNeeds) },
  { key: 'channel', label: () => t('主渠道', 'Primary channel'), ask: askChannel, shown: (state) => IM_LABELS[state.channel] },
  { key: 'mailboxes', label: () => t('邮箱', 'Mailboxes'), ask: askMailboxes,
    shown: (state) => (mailboxList(state).length ? list(mailboxList(state).map(addressOf)) : t('没有接', 'none')) },
  { key: 'ovConfig', label: () => 'OpenViking', ask: (ctx) => ctx.editOV(),
    shown: (state, { homeDir }) => shortenHome(state.ovConfig, homeDir) },
  { key: 'switches', label: () => t('其他设置', 'Other settings'), ask: askSwitches, shown: switchesShown },
];

const mailChanged = (state) => state.mail.added.length + state.mail.updated.size + state.mail.removed.length > 0;

// Changing an instance: every setting with its value; the owner opens the
// ones to change, then saves. Resolves with the state to save, or null.
// The list stays in one place: before it is drawn again, the screen goes
// back to where it first was, so what a setting asked or said is gone and
// its new value shows in the list instead. `page` keeps that place, and the
// cursor, when the list is come back to from a code waiting to be scanned,
// and `page.notice`, why the scans came back, when they said.
async function editInstance(ctx, state, { describe, check, before, page }) {
  const { prompter, out, current } = ctx;
  page.mark ??= prompter.mark();
  let notice = page.notice ?? null;
  page.notice = null;
  for (;;) {
    prompter.rewind(page.mark);
    if (notice) prompter.warn(notice);
    for (const text of ctx.notes.splice(0)) prompter.info(text);
    notice = null;
    const changes = describe(state);
    const changed = (key, value) => value !== before[key] || (key === 'mailboxes' && mailChanged(state));
    const labels = SETTINGS.map(({ label }) => label());
    const labelWidth = Math.max(...labels.map((label) => width(label))) + 2;
    const name = t(`修改 ${current.displayName}（${current.id}）`, `Change ${current.displayName} (${current.id})`);
    let choice;
    try {
      choice = await prompter.select(name, [
        ...SETTINGS.map(({ key, shown }, index) => {
          const value = shown(state, ctx);
          return { label: padEnd(labels[index], labelWidth), hint: `${value}${changed(key, value) ? t('（已改）', ' (changed)') : ''}`, value: index };
        }),
        { label: changes.length ? t(`保存（${changes.length} 项改动）`, `Save (${changes.length} ${changes.length === 1 ? 'change' : 'changes'})`) : t('保存', 'Save'), value: 'save' },
        { label: t('放弃修改', 'Discard changes'), value: 'quit' },
      ], { initial: page.cursor ?? 0, label: name });
    } catch (error) {
      if (!(error instanceof GoBack)) throw error;
      // Back to 要做什么, unless that would drop changes nobody saved.
      if (changes.length === 0) throw new StartOver();
      notice = t(`有 ${changes.length} 项改动还没保存：选「保存」，或者「放弃修改」`,
        `${changes.length} unsaved ${changes.length === 1 ? 'change' : 'changes'}: choose Save, or Discard changes`);
      continue;
    }
    if (choice === 'quit') {
      out(t('\n已放弃，没有保存任何改动。\n', '\nDiscarded; nothing was saved.\n'));
      return null;
    }
    if (choice === 'save') {
      if (changes.length === 0) {
        out(t('\n没有改动。\n', '\nNothing changed.\n'));
        return null;
      }
      const problem = check(state);
      if (!problem) return state;
      notice = t(`这样改不行：${problem}`, `That will not work: ${problem}`);
      continue;
    }
    page.cursor = choice;
    // ← inside a setting leaves it as it was and comes back to the list.
    try {
      await SETTINGS[choice].ask(ctx, state);
    } catch (error) {
      if (!(error instanceof GoBack)) throw error;
    }
  }
}

// A new instance is asked step by step, in sections. ← goes back to the
// step that asked before (steps that only said something are passed over),
// and from the first question back to 要做什么 when there is one. A step
// returns DECLINED to stop the setup.
const DECLINED = Symbol('declined');

// `run` remembers the way through: the steps answered, and where on screen
// each began, so ← takes the screen back to how it was when that step was
// reached. From the scans, setup comes back in at the last step.
async function wizard(ctx, state, steps, run) {
  const { prompter } = ctx;
  run.answered ??= [];
  run.marks ??= [];
  const sectionOf = (index) => steps.slice(0, index + 1).reverse().find((candidate) => candidate.section)?.section ?? null;
  let index = run.at ?? 0;
  let shown = index > 0 ? sectionOf(index - 1) : null;
  const back = (to) => {
    prompter.rewind(run.marks[to]);
    shown = to > 0 ? sectionOf(to - 1) : null;
    index = to;
  };
  while (index < steps.length) {
    run.marks[index] = prompter.mark();
    const section = sectionOf(index);
    if (section && section !== shown) {
      prompter.section(section.title, section);
      shown = section;
    }
    const asked = prompter.asked;
    try {
      if (await steps[index].ask(ctx, state) === DECLINED) return DECLINED;
      if (prompter.asked !== asked) run.answered.push(index);
      index += 1;
    } catch (error) {
      if (!(error instanceof GoBack)) throw error;
      if (run.answered.length) back(run.answered.pop());
      else if (ctx.startOver) throw new StartOver();
      else back(index);
    }
  }
  return null;
}

// Back in at the last step (the confirmation), as it was before it was answered.
function returnToLast(prompter, steps, run) {
  run.answered.pop();
  run.at = steps.length - 1;
  prompter.rewind(run.marks[run.at], { whole: true });
}

// The App ID and App Secret (developer console → 凭证与基础信息), asked until
// the platform accepts them. Declining to type them again is GoBack.
// `created`: the app was just made by a scan whose confirmation never came.
async function enterApp({ prompter, fetchImpl = globalThis.fetch }, { previous = null, created = false } = {}) {
  prompter.write(created
    ? t('\n  在飞书开放平台打开刚创建的应用 →「凭证与基础信息」，复制下面两项：\n',
      '\n  In the Feishu/Lark developer console, open the app just created → Credentials & Basic Info, and copy these two:\n')
    : t('\n  在飞书开放平台打开要用的应用 →「凭证与基础信息」，复制下面两项：\n',
      '\n  In the Feishu/Lark developer console, open the app to use → Credentials & Basic Info, and copy these two:\n'));
  const brands = [{ label: t('飞书', 'Feishu'), value: 'feishu' }, { label: t('Lark（国际版）', 'Lark (international)'), value: 'lark' }];
  let last = previous;
  for (;;) {
    const appId = await prompter.text('App ID', { initial: last?.appId ?? '',
      validate: (value) => (/^cli_[0-9a-z]+$/.test(value) ? null : t('App ID 以 cli_ 开头', 'An App ID starts with cli_')) });
    const appSecret = await prompter.secret('App Secret');
    const brand = await prompter.select(t('租户', 'Tenant'), brands, { initial: last?.brand === 'lark' ? 1 : 0 });
    const app = { appId, appSecret, brand, ownerOpenId: null };
    const spinning = prompter.spin(t('核对 App ID 和 App Secret', 'Checking the App ID and App Secret'));
    let token;
    try {
      token = await tenantToken(app, { fetchImpl });
      spinning.succeed(t('App ID 和 App Secret 没问题', 'The App ID and App Secret check out'));
    } catch (error) {
      if (error instanceof LarkPolicyError) {
        spinning.fail(t('平台不给这个应用发访问凭证', 'The platform gives this app no token'));
        prompter.warn(policyRefusal(appId, error, { typed: true }));
      } else {
        spinning.fail(!(error instanceof LarkCredentialsError)
          ? t(`连不上飞书开放平台：${error.message}`, `Could not reach the developer platform: ${error.message}`)
          : t(`平台不认这组凭证（${error.message}）：App ID 或 App Secret 不对，或者租户选错了`,
            `The platform refused them (${error.message}): the App ID or App Secret is wrong, or the tenant is`));
      }
      last = app;
      if (!await prompter.confirm(t('重新输入？', 'Try again?'))) throw new GoBack();
      continue;
    }
    // Settings that cannot be read are checked no further here.
    const inspected = await inspectApp(app, { fetchImpl, token }).catch(() => null);
    if (inspected?.callbacks.type !== 'webhook') return app;
    prompter.warn(webhookRefusal());
    last = app;
    if (!await prompter.confirm(t('换一个应用？', 'Use another app?'))) throw new GoBack();
  }
}

const scanTitles = {
  create: (minutes) => t(`第 1 次扫码：用飞书 App 扫码，创建机器人应用（${minutes} 分钟内有效）`,
    `Scan 1 of 2: scan with the Feishu/Lark app to create the bot app (valid for ${minutes} minutes)`),
  select: (minutes) => t(`第 1 次扫码：用飞书 App 扫码，选择已有的机器人应用（${minutes} 分钟内有效）`,
    `Scan 1 of 2: scan with the Feishu/Lark app to pick an existing bot app (valid for ${minutes} minutes)`),
  update: (minutes, appId) => t(`用飞书 App 扫码，给应用 ${appId} 补上缺的权限和事件（${minutes} 分钟内有效）`,
    `Scan with the Feishu/Lark app to add what ${appId} lacks (valid for ${minutes} minutes)`),
};

// One scan for the app: `create` or `select` (see scanApp), or `update` the
// app `appId`. canGoBack: ← while the code waits stops the registration
// (nothing changes before the scan is confirmed) and rejects with GoBack.
// While a create or select waits, m switches to typing the App ID and Secret;
// while an update waits, s skips it and resolves with null.
export async function registerApp({
  plan, prompter, scopes, debug, register = null, canGoBack = false, mode = 'create', appId = null, fetchImpl = globalThis.fetch,
}) {
  const controller = new AbortController();
  let scan = null;
  let wantManual;
  const manual = new Promise((resolve) => { wantManual = resolve; });
  let wantBack;
  const back = new Promise((resolve) => { wantBack = resolve; });
  const created = scanApp({
    mode,
    appId,
    name: plan.displayName,
    description: t('Vika：把本机的 coding agent 接进飞书', 'Vika: the coding agent on your computer, in Feishu/Lark'),
    scopes,
    signal: controller.signal,
    ...(register ? { register } : {}),
    onQRCode: ({ url, expiresIn }) => {
      scan?.stop();
      scan = showScan(prompter, {
        url,
        where: IM_LABELS.lark,
        title: scanTitles[mode](Math.round(expiresIn / 60), appId),
        ...(mode === 'update' ? { stopKey: 's', stopLabel: t('跳过', 'skip') }
          : { stopKey: 'm', stopLabel: t('手动填 App ID', 'enter the App ID by hand') }),
        onBack: canGoBack ? () => wantBack() : null,
      });
      scan.pressed.then((pressed) => { if (pressed) wantManual(); });
    },
    onStatus: (status) => {
      if (debug) scan?.say(`[registration] ${status}`);
      if (status === 'domain_switched') scan?.say(t('识别到国际版 Lark 租户', 'This is a Lark (international) tenant'));
      if (status === 'polling') scan?.status(t('等待扫码确认', 'Waiting for the scan to be confirmed'));
      if (status === 'slow_down') scan?.status(t('平台要求放慢轮询，继续等待', 'Asked to poll more slowly; still waiting'));
    },
  });
  try {
    const winner = await Promise.race([
      created.then((app) => ({ app })), manual.then(() => ({ manual: true })), back.then(() => ({ back: true })),
    ]);
    if (winner.app) return winner.app;
    controller.abort();
    created.catch(() => {});
    scan?.stop();
    scan = null;
    if (winner.back) throw new GoBack();
    if (mode === 'update') return null;
    return await enterApp({ prompter, fetchImpl }, { created: mode === 'create' });
  } finally {
    scan?.stop();
  }
}

// A step with a spinner: it ends in a tick, or a cross and the error.
async function step(prompter, text, run, done = text) {
  const spinning = prompter.spin(text);
  try {
    const result = await run();
    spinning.succeed(typeof done === 'function' ? done(result) : done);
    return result;
  } catch (error) {
    spinning.fail(text);
    throw error;
  }
}

// What the app lacks of what Vika needs, said once each; resolves with it.
// `manual`: the app was not set up by a scan, so how its events arrive
// (which no API tells) is left for the owner to check.
export function reportApp(prompter, scopes, inspected, { manual = false } = {}) {
  const missing = missingConfig(scopes, inspected);
  if (!inspected.online) {
    prompter.warn(t('应用还没有上线版本，可能在等管理员审核；审核通过后机器人才能收消息，不用重新 setup。',
      'The app has no live version yet, perhaps awaiting admin approval; the bot gets messages once it is approved, with no need to run setup again.'));
  } else if (inspected.pendingReview) {
    prompter.info(t('应用有一个新版本在等管理员审核：下面缺的可能就在里面，审核通过后生效。',
      'A new version of the app awaits admin approval: what is missing below may be in it, and takes effect once approved.'));
  }
  const absent = [...missing.scopes.tenant, ...missing.scopes.user];
  if (absent.length) {
    const some = list(absent.slice(0, 8));
    prompter.warn(t(`有 ${absent.length} 个权限没开通，相关功能会不可用：${some}${absent.length > 8 ? ' 等' : ''}`,
      `${absent.length} permissions are not granted, so what needs them will not work: ${some}${absent.length > 8 ? ', …' : ''}`));
  } else {
    prompter.success(t(`权限核对通过：${scopes.tenant.length} 个机器人权限、${scopes.user.length} 个用户权限`,
      `Permissions check out: ${scopes.tenant.length} bot and ${scopes.user.length} user scopes`));
  }
  if (missing.events?.includes(MESSAGE_EVENT)) {
    prompter.warn(t(`没有订阅接收消息事件（${MESSAGE_EVENT}）：机器人收不到消息`,
      `The message event (${MESSAGE_EVENT}) is not subscribed: the bot gets no messages`));
  }
  const comments = missing.events?.filter((event) => event !== MESSAGE_EVENT) ?? [];
  if (comments.length) {
    prompter.warn(t(`没有订阅文档评论事件（${list(comments)}）：在飞书文档的评论里 @ 机器人，它收不到`,
      `The document comment event (${list(comments)}) is not subscribed: the bot misses @mentions in document comments`));
  }
  // No API tells how events arrive; callbacks on a webhook say the app has one.
  if (inspected.callbacks.type === 'webhook') {
    prompter.warn(t('应用的卡片回调走 webhook，事件多半也是：这样 Vika 收不到消息，卡片按钮也没反应。在开放平台「事件与回调」把事件和回调都改成长连接（原来用 webhook 的服务就收不到了）',
      'The app\'s card callbacks go to a webhook, and its events most likely do too: then Vika gets no messages and card buttons do nothing. Switch both to the long connection under Events & Callbacks in the developer console (the webhook service then stops getting them)'));
  } else if (missing.callbacks.length) {
    prompter.warn(t(`卡片回调（${list(missing.callbacks)}）没有用长连接订阅：卡片上的按钮点了没反应`,
      `The card callback (${list(missing.callbacks)}) is not subscribed over the long connection: card buttons do nothing`));
  }
  if (manual) {
    prompter.info(t('事件走长连接还是 webhook 读不到：在开放平台「事件与回调 → 事件配置」确认是长连接，用 webhook 的话机器人收不到消息',
      'Whether events come over the long connection or a webhook cannot be read: check Events & Callbacks → Event configuration in the developer console says long connection, or the bot gets no messages'));
  }
  return missing;
}

// Another instance on this machine receiving with the same app gets each
// message or misses it at random. Allowed after a warning (a reinstall, a
// move); declining goes back to the questions.
async function confirmUnshared({ prompter, larkCli, appId, others }) {
  const profiles = others.length ? await larkCli.profiles().catch(() => []) : [];
  const sharing = others.filter((other) => profiles.some(({ name, appId: used }) => name === other.profile && used === appId));
  if (!sharing.length) return;
  const names = list(sharing.map(({ id, displayName }) => t(`${displayName}（${id}）`, `${displayName} (${id})`)));
  prompter.warn(t(`实例 ${names}也在用应用 ${appId}：同时运行时，每条消息只会随机推给其中一个实例。`,
    `Instance ${names} uses the app ${appId} too: while both run, each message goes to just one of them, at random.`));
  if (!await prompter.confirm(t('仍然用这个应用？', 'Use this app anyway?'), false)) throw new GoBack();
}

// The app from `source`: a scan, or `app` typed in with the questions. It is
// checked against what Vika needs, written to the lark-cli profile, and the
// owner authorizes as a user (the second scan). `others` are the machine's
// other instances with Feishu. Resolves with { profile, ownerOpenId, name },
// name being the app's own, when it could be read.
export async function connectLark({
  plan, prompter, larkCli, fetchImpl, profile, source = 'create', app: typed = null, others = [], debug = false, canGoBack = false, register = null,
}) {
  const scopes = larkScopes();
  prompter.section(IM_LABELS.lark);
  // Only the first code can be gone back from: once it is confirmed the app exists.
  const app = typed ?? await registerApp({ plan, prompter, scopes, debug, canGoBack, mode: source, fetchImpl, register });
  // A scan always says who confirmed it; an app without that was typed in.
  const manual = !app.ownerOpenId;
  prompter.success(source === 'create' && !typed ? t(`已创建应用 ${app.appId}`, `Created the app ${app.appId}`)
    : t(`用应用 ${app.appId}`, `Using the app ${app.appId}`));
  let inspected = null;
  try {
    inspected = await step(prompter, t('核对应用的权限、事件和版本', 'Checking the app\'s permissions, events and version'),
      () => inspectApp(app, { fetchImpl }), t('应用信息已读取', 'App checked'));
  } catch (error) {
    // The account cannot run the bot, whatever is scanned next with it:
    // back to the questions, saying so, before anything is written.
    if (error instanceof LarkPolicyError) {
      throw new GoBack(policyRefusal(app.appId, error, { made: source === 'create' && !typed, typed: Boolean(typed) }));
    }
    if (error instanceof LarkCredentialsError) throw error;
    prompter.warn(t(`读不到应用的配置，跳过核对：${error.message}`, `The app's settings could not be read, so they are not checked: ${error.message}`));
  }
  // Refused before anything is written: back to the questions, for another bot.
  if (inspected?.callbacks.type === 'webhook') throw new GoBack(webhookRefusal());
  await confirmUnshared({ prompter, larkCli, appId: app.appId, others });
  if (inspected && lacksAny(reportApp(prompter, scopes, inspected, { manual }))) {
    prompter.info(manual
      ? t('缺的在飞书开放平台给应用补上；你是应用的所有者或管理员的话，也可以之后运行 vika setup 扫码补齐',
        'Add what is missing in the developer console; if you own or administer the app, vika setup can add it with a scan later')
      : t('之后运行 vika setup 可以扫码补齐', 'vika setup can add it with a scan later'));
  }
  await larkCli.configureProfile({ profile, ...app });
  const login = await larkCli.beginUserLogin({ profile, scopes: grantedUserScopes(scopes, inspected) });
  const scan = showScan(prompter, {
    url: login.url, where: IM_LABELS.lark, label: t('等待授权', 'Waiting for authorization'),
    title: source === 'manual'
      ? t('扫码授权 Vika 以你的身份读消息（用于搜索和读历史）', 'Scan to let Vika read messages as you (for search and history)')
      : t('第 2 次扫码：授权 Vika 以你的身份读消息（用于搜索和读历史）', 'Scan 2 of 2: let Vika read messages as you (for search and history)'),
  });
  try {
    await larkCli.finishUserLogin({ profile, deviceCode: login.deviceCode });
  } finally {
    scan.stop();
  }
  const ownerOpenId = await larkCli.userOpenId({ profile });
  if (!ownerOpenId) throw new Error('lark-cli authorization finished without a user identity');
  if (app.ownerOpenId && ownerOpenId !== app.ownerOpenId) {
    prompter.warn(t('两次扫码的不是同一个飞书账号；机器人会把第 2 次扫码的人当作主人。',
      'The two scans came from different Feishu/Lark accounts; the bot takes the second as its owner.'));
  }
  prompter.success(t('飞书已接好', 'Feishu/Lark is connected'));
  return { profile, ownerOpenId, name: inspected?.botName ?? inspected?.name ?? null };
}

// The app as its bot reads it, through the instance's profile.
const readInstanceApp = (larkCli, agent) => readApp((pathname, query) => larkCli.get({ profile: agent.profile, pathname, query }));

// The user scopes the app has that the owner's login does not carry yet: a
// release that asks for more needs the owner to authorize again. None when
// the login cannot be read (`vika login lark` is for that).
async function loginLacks(larkCli, agent, scopes, inspected) {
  const granted = await larkCli.userScopes({ profile: agent.profile }).catch(() => null);
  if (!granted || !inspected) return [];
  return grantedUserScopes(scopes, inspected).filter((scope) => !granted.includes(scope));
}

// What a Feishu/Lark instance lacks of what this release asks for: the
// app's scopes, events and card callback, and the owner's login. Rejects
// when the app cannot be read.
export async function larkNeeds({ agent, larkCli, scopes = larkScopes() }) {
  const inspected = await readInstanceApp(larkCli, agent);
  const missing = missingConfig(scopes, inspected);
  const login = await loginLacks(larkCli, agent, scopes, inspected);
  const count = missing.scopes.tenant.length + missing.scopes.user.length + (missing.events?.length ?? 0) + missing.callbacks.length;
  return { inspected, missing, login, count: count + (login.length ? 1 : 0) };
}

// The app checked against what Vika needs; one `update` scan adds what it
// lacks, the platform's confirm page showing the change. Resolves with
// { inspected, updated }: what was read last (null when the app's settings
// cannot be read) and whether the platform confirmed a change.
export async function completeLarkApp({ agent, prompter, larkCli, scopes = larkScopes(), register = null }) {
  const read = async () => {
    const spinning = prompter.spin(t('核对应用的权限和事件', 'Checking the app\'s permissions and events'));
    try {
      const app = await readInstanceApp(larkCli, agent);
      spinning.succeed(t(`应用 ${app.appId} 已读取`, `Read the app ${app.appId}`));
      return app;
    } catch (error) {
      spinning.warn(t(`读不到应用的配置，跳过核对：${error.message}`, `The app's settings could not be read, so they are not checked: ${error.message}`));
      return null;
    }
  };
  const inspected = await read();
  if (!inspected || !lacksAny(reportApp(prompter, scopes, inspected))) return { inspected, updated: false };
  // The confirm page withdraws a version under review before publishing its
  // own, and the approval starts over: not by default.
  if (inspected.pendingReview) {
    prompter.warn(t('应用有一个版本正在审核：扫码补齐时，飞书会先撤回它再提交新版本，审批要重新排队。',
      'A version of the app is under review: adding what is missing withdraws it first and submits a new one, so the approval starts over.'));
  }
  if (!await prompter.confirm(t('扫码把缺的补上？要你是这个应用的所有者或管理员', 'Scan to add what is missing? You need to own or administer the app'),
    !inspected.pendingReview)) {
    return { inspected, updated: false };
  }
  let updated;
  try {
    updated = await registerApp({ plan: agent, prompter, scopes, mode: 'update', appId: inspected.appId, ...(register ? { register } : {}) });
  } catch (error) {
    if (!(error instanceof LarkRegistrationError)) throw error;
    prompter.warn(t(`应用没有更新（${error.code}）：${error.message}`, `The app was not updated (${error.code}): ${error.message}`));
    return { inspected, updated: false };
  }
  if (!updated) {
    prompter.info(t('跳过了，应用没有改动', 'Skipped; the app is unchanged'));
    return { inspected, updated: false };
  }
  prompter.success(t('平台已确认', 'The platform confirmed it'));
  const after = await read();
  if (after) reportApp(prompter, scopes, after);
  return { inspected: after ?? inspected, updated: true };
}

// The owner authorizes Vika as a user again (a scan), for the user scopes
// the app has.
export async function reauthorizeOwner({ agent, prompter, larkCli, scopes = larkScopes(), inspected = null }) {
  const login = await larkCli.beginUserLogin({ profile: agent.profile, scopes: grantedUserScopes(scopes, inspected) });
  const scan = showScan(prompter, {
    url: login.url, where: IM_LABELS.lark, label: t('等待授权', 'Waiting for authorization'),
    title: t(`扫码重新授权 ${agent.displayName}（${agent.profile}）以你的身份读消息`, `Scan to let ${agent.displayName} (${agent.profile}) read messages as you again`),
  });
  try {
    await larkCli.finishUserLogin({ profile: agent.profile, deviceCode: login.deviceCode });
  } finally {
    scan.stop();
  }
  prompter.success(t(`${agent.profile} 已重新授权；运行中的实例会自动用上新的登录`, `${agent.profile} is authorized again; the running instance picks it up by itself`));
}

// After an upgrade: the app gets what it lacks with one scan, then the owner authorizes again if their
// login lacks scopes the app now has. Resolves with whether the app changed.
export async function updateLarkApp({ agent, prompter, larkCli, scopes = larkScopes(), register = null }) {
  const { inspected, updated } = await completeLarkApp({ agent, prompter, larkCli, scopes, register });
  const lacking = await loginLacks(larkCli, agent, scopes, inspected);
  if (lacking.length) {
    prompter.info(t(`你给 ${agent.displayName} 的授权还缺 ${lacking.length} 个权限（${list(lacking.slice(0, 5))}${lacking.length > 5 ? ' 等' : ''}），再扫码授权一次`,
      `Your authorization of ${agent.displayName} lacks ${lacking.length} ${lacking.length === 1 ? 'permission' : 'permissions'} (${list(lacking.slice(0, 5))}${lacking.length > 5 ? ', …' : ''}); scan once more to authorize`));
    await reauthorizeOwner({ agent, prompter, larkCli, scopes, inspected });
  }
  return updated;
}

// Every Feishu/Lark instance checked at once, for setup's first menu and the
// instance's 飞书应用 setting: the ones that lack something this release asks for. An app that cannot be
// read is left out.
export async function checkLarkApps({ prompter, env, manifest }) {
  const agents = (manifest?.agents ?? []).filter((agent) => connectorsOf(agent).includes('lark') && agent.profile);
  const bin = agents.length ? env.LARK_CLI_BIN || manifest.env?.LARK_CLI_BIN || resolveLarkCli(env) : null;
  if (!bin) return [];
  const larkCli = new LarkCli(bin);
  const scopes = larkScopes();
  const spinning = prompter.spin(t('核对飞书应用的权限和事件', 'Checking the Feishu/Lark apps\' permissions and events'));
  const checked = await Promise.all(agents.map((agent) => larkNeeds({ agent, larkCli, scopes }).then(
    (needs) => (needs.count ? { agent, ...needs } : null), () => null)));
  const lacking = checked.filter(Boolean);
  if (lacking.length) {
    spinning.warn(t(`${list(lacking.map(({ agent }) => `${agent.displayName}（${agent.id}）`))} 的飞书应用缺这个版本要的权限或事件：修改实例 →「飞书应用」里扫码补齐`,
      `The Feishu/Lark app of ${list(lacking.map(({ agent }) => `${agent.displayName} (${agent.id})`))} lacks permissions or events this release needs: change the instance and complete it under Feishu/Lark app`));
  } else {
    spinning.succeed(t('飞书应用的权限和事件都齐了', 'The Feishu/Lark apps have every permission and event they need'));
  }
  return lacking;
}

// canGoBack: ← while the code waits stops the login before anything is bound
// and rejects with GoBack.
// The login's errors and status lines, in this run's language.
function weixinError(error) {
  const text = {
    weixin_bad_verify_code: t('验证码格式无效', 'That verification code is malformed'),
    weixin_verify_code_blocked: t('验证码错误次数过多，请稍后重新扫码', 'Too many wrong codes; scan again later'),
    weixin_bound_elsewhere: t('微信端显示已绑定，但本地没有可用凭证', 'WeChat says this account is bound, but there are no credentials here'),
    weixin_login_timeout: t('微信扫码登录超时，请重新运行', 'The WeChat login timed out; run it again'),
  }[error?.code];
  return text ? Object.assign(new Error(text), { code: error.code }) : error;
}

export async function connectWeixin({ plan, prompter, canGoBack = false }) {
  prompter.section(IM_LABELS.weixin);
  fs.mkdirSync(plan.dataDir, { recursive: true, mode: 0o700 });
  const state = new WeixinState(plan.dataDir);
  const controller = new AbortController();
  let wantBack;
  const back = new Promise((resolve) => { wantBack = resolve; });
  let scan = null;
  const login = loginWeixin({
    state,
    signal: controller.signal,
    showQr: (value, { refreshed }) => {
      scan?.stop();
      scan = showScan(prompter, {
        url: value,
        where: IM_LABELS.weixin,
        title: refreshed ? t('二维码过期了，换了一张：用手机微信扫码', 'The code expired, here is a new one: scan it with WeChat on your phone')
          : t('用手机微信扫码，并在手机上确认', 'Scan with WeChat on your phone, then confirm there'),
        onBack: canGoBack ? () => wantBack() : null,
      });
    },
    // `connected` is left to the line that follows the scan.
    say: (text, { kind, accountId } = {}) => {
      if (kind === 'connected') return;
      const said = {
        scanned: t('已扫码，请在手机上确认', 'Scanned; confirm on your phone'),
        'already-bound': t(`本地已有微信账号 ${accountId} 的凭证，无需重复绑定`, `WeChat account ${accountId} is bound here already`),
      }[kind] ?? text;
      if (!scan) { prompter.info(said); return; }
      scan.say(said);
      if (kind === 'scanned') scan.status(t('等待在手机上确认', 'Waiting for you to confirm on the phone'));
    },
    readVerifyCode: () => {
      scan?.stop();
      scan = null;
      return prompter.text(t('微信显示的验证码', 'The code WeChat shows'));
    },
  }).catch((error) => { throw weixinError(error); });
  try {
    if (await Promise.race([login.then(() => false), back.then(() => true)])) {
      // Let the login see it is stopped before its state closes.
      controller.abort();
      await login.catch(() => {});
      throw new GoBack();
    }
  } finally {
    scan?.stop();
    state.close();
  }
  prompter.success(t('微信已接好', 'WeChat is connected'));
}

// No runtime can run yet: the built-in Pi logs in to a model right here,
// in Pi's own screen, and setup carries on when it quits.
export async function loginPi({ prompter, env }) {
  prompter.info(t('打开内置的 Pi：输入 /login 选模型提供方并登录，完成后输入 /quit 回到这里。',
    'Opening the built-in Pi: type /login to pick a model provider and log in, then /quit to come back here.'));
  prompter.input.pause?.();
  await new Promise((resolve) => {
    const child = spawn(process.execPath, [managedPiBin()], { stdio: 'inherit', env });
    child.once('exit', resolve);
    child.once('error', resolve);
  });
}

export function greet({ larkCliBin, profile, userId, text }) {
  return new LarkCli(larkCliBin).sendText({ profile, userId, text });
}

// Weixin delivers a bot's message only after the owner has written to it
// since the scan, so the bot cannot say hello first: the owner does.
export const weixinFirstMessage = () => t(
  '在微信里先给机器人发一句话（比如「你好」）：在那之前它没法主动找你，新邮件提醒也发不到微信。',
  'Send the bot a message on WeChat first (say "hi"): until then it cannot reach you there, not even with new-mail alerts.',
);

// How messages reach the bot is the one thing no API tells (the long
// connection, the message event, a published version, no other receiver),
// so the owner writes to it and the instance's own activity feed (loopback
// /api/activity) shows whether that arrived. The listener notes where the
// feed stands when made; wait(signal) resolves true once a Feishu/Lark
// message arrives after that, false when the signal aborts.
export async function larkMessageListener({ port, fetchImpl = globalThis.fetch, intervalMs = 2_000 }) {
  const feed = async (query) => {
    const response = await fetchImpl(`http://127.0.0.1:${port}/api/activity?${query}`, { signal: AbortSignal.timeout(5_000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return (await response.json()).activities ?? [];
  };
  const latest = async () => (await feed('limit=1'))[0]?.id ?? 0;
  let cursor = await latest().catch(() => null);
  const pause = (signal) => new Promise((resolve) => {
    const timer = setTimeout(resolve, intervalMs);
    signal.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, { once: true });
  });
  return {
    async wait(signal) {
      while (!signal.aborted) {
        try {
          // Without a starting point, what is there already does not count.
          if (cursor === null) cursor = await latest();
          else {
            const activities = await feed(`after=${cursor}&limit=100`);
            if (activities.some(({ kind, metadata }) => ['message.received', 'message.ignored'].includes(kind) && metadata?.source === 'lark')) return true;
            cursor = Math.max(cursor, ...activities.map(({ id }) => id));
          }
        } catch { /* Not answering yet; ask again. */ }
        await pause(signal);
      }
      return false;
    },
  };
}

const receivingChecklist = (id, vika) => [
  t('开放平台「事件与回调」里，事件订阅方式是长连接，并订阅了接收消息（im.message.receive_v1）',
    'Under Events & Callbacks in the developer console, events come over the long connection and the message event (im.message.receive_v1) is subscribed'),
  t('改过配置后创建了版本并发布（企业可能在等管理员审核）', 'After a change, a version was created and published (an organization may be awaiting admin approval)'),
  t('这个应用没有同时被别的程序（OpenClaw、botmux、另一台机器上的 Vika）用长连接收消息', 'No other program (OpenClaw, botmux, Vika on another machine) receives with this app over the long connection'),
  t(`运行 ${vika} logs ${id} 看飞书连接有没有报错`, `Run ${vika} logs ${id} for errors in the Feishu/Lark connection`),
];

// The owner replies on Feishu/Lark and setup waits to see it arrive, a few
// minutes at most; s skips. Resolves true when it arrived.
async function confirmReceiving({ prompter, listener, greeted, name, id, vika, timeoutMs = 180_000 }) {
  prompter.info(greeted
    ? t('在飞书里回它一句（说什么都行），确认它收得到你的消息', 'Reply to it on Feishu/Lark (anything will do) to check it gets your messages')
    : t(`在飞书里搜「${name}」给它发一句，确认它收得到你的消息`, `Search for "${name}" on Feishu/Lark and message it, to check it gets your messages`));
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let skipped = false;
  const spinning = prompter.spin(t('等你的消息（按 s 跳过）', 'Waiting for your message (s skips)'));
  const watch = prompter.watchKey('s');
  watch.pressed.then((pressed) => { if (pressed) { skipped = true; controller.abort(); } });
  let arrived;
  try {
    arrived = await listener.wait(controller.signal);
  } finally {
    clearTimeout(timer);
    watch.stop();
  }
  if (arrived) {
    spinning.succeed(t('收到你的消息了，飞书这条路是通的', 'Your message arrived: Feishu/Lark gets through'));
    return true;
  }
  spinning.stop();
  if (skipped) prompter.info(t('跳过了；之后它要是收不到你的消息，按下面查：', 'Skipped; if it does not get your messages later, check:'));
  else prompter.warn(t(`${Math.round(timeoutMs / 60_000)} 分钟没等到你的消息。它发得出、收不到，按下面查：`, `No message in ${Math.round(timeoutMs / 60_000)} minutes. It can send but not receive; check:`));
  receivingChecklist(id, vika).forEach((line, index) => prompter.write(`    ${index + 1}. ${line}\n`));
  prompter.write(t('    修好后不用重新 setup，直接给它发消息就行。\n', '    Once fixed, no need to run setup again: just message it.\n'));
  return false;
}

// What the bot says on Feishu when it is newly connected; on the primary IM,
// also that it reaches the owner there.
function greeting({ name, newInstance, primary }) {
  const hello = newInstance
    ? t(`你好，我是 ${name}，已经在你的电脑上跑起来了。直接给我发消息就行；把我拉进群，我也能参与群聊。`,
      `Hi, I'm ${name}, now running on your computer. Just message me here; add me to a group and I can join in there too.`)
    : t(`你好，我是 ${name}，现在也能在飞书里找到我了。`, `Hi, I'm ${name}; you can reach me here too now.`);
  return primary
    ? `${hello}${t('以后我主动找你的消息（比如新邮件提醒、待你确认的邮件回复）会发到这里。',
      ' When I need to reach you on my own (new-mail alerts, replies waiting for your OK), it will be here.')}`
    : hello;
}

const found = (labels) => t(`找到 ${list(labels)}`, `Found ${list(labels)}`);

// What the machine offers, in one line, once the check is done.
function foundLine(runtimes) {
  const ready = runtimes.filter((runtime) => runtime.available).map((runtime) => runtime.label);
  return ready.length ? found(ready) : t('还没有能用的 agent', 'No usable agent yet');
}

// Until some runtime can run: say why each cannot, and offer to log the
// built-in Pi in to a model here. Resolves with the runtimes, or null to stop.
async function ensureRuntime({ prompter, env, detect, connect, live }, runtimes) {
  let detected = runtimes;
  while (!detected.some((runtime) => runtime.available)) {
    prompter.warn(t('没有可用的 agent 运行时：', 'No agent runtime can run yet:'));
    const labelWidth = Math.max(...detected.map((runtime) => width(runtime.label)));
    for (const runtime of detected) prompter.write(`      ${padEnd(runtime.label, labelWidth)}  ${runtime.note}\n`);
    const later = t(`登录好其中一个后，重新运行 ${vikaCommand(env)} setup。`, `Log in to one of them, then run ${vikaCommand(env)} setup again.`);
    if (!prompter.tty || !connect.pi) {
      prompter.info(later);
      return null;
    }
    const next = await prompter.select(t('现在怎么办', 'What now'), [
      { label: t('用内置的 Pi，现在登录一个模型', 'Use the built-in Pi and log in to a model now'), value: 'pi',
        hint: t('在 Pi 里输入 /login，登录后 /quit 回到这里', 'type /login in Pi, then /quit to come back') },
      { label: t('先退出，我去登录 Codex 或 Claude Code', 'Quit; I will log in to Codex or Claude Code'), value: 'quit' },
    ], { back: false });
    if (next === 'quit') {
      prompter.info(later);
      return null;
    }
    // Pi takes the whole screen while it runs.
    live?.release();
    await connect.pi({ prompter, env });
    live?.resume();
    prompter.redraw();
    detected = await step(prompter, t('再看一次有哪些 agent', 'Looking for agents again'), () => detect.runtimes({ env }),
      (again) => (again.some((runtime) => runtime.available)
        ? found(again.filter((runtime) => runtime.available).map((runtime) => runtime.label)) : t('还是没有能用的 agent', 'Still no usable agent')));
  }
  return detected;
}

// Label/value rows: a box on a colour terminal, indented lines otherwise.
function table(prompter, title, rows) {
  const keyWidth = Math.max(...rows.map(([key]) => width(key)));
  if (prompter.fancy) {
    const lines = rows.map(([key, value]) => `${prompter.s.gray(padEnd(key, keyWidth))}  ${key ? value : prompter.s.gray(value)}`);
    prompter.write(`\n${box(lines, { output: prompter.output, env: prompter.env, title })}\n`);
    return;
  }
  prompter.write(`\n${title}${t('：', ':')}\n${rows.map(([key, value]) => `  ${padEnd(key, keyWidth)}  ${value}`).join('\n')}\n`);
}

// Feishu/Lark is one scan when the app is typed in: only the owner's authorization.
const scansOf = (im, larkSource) => (im === 'lark' ? (larkSource === 'manual' ? 1 : 2) : 1);

function connectionSteps(prompter, connectors, { saving = false, larkSource = 'create' } = {}) {
  if (!connectors.length) return;
  const telegram = connectors.some((im) => TOKEN_IMS.includes(im));
  const steps = connectors.map((im) => {
    if (im === 'telegram') return t('Telegram Bot token + Start 绑定', 'Telegram bot token + Start');
    if (im === 'qq') return t('QQ AppID / AppSecret + 私聊验证码绑定', 'QQ AppID / AppSecret + private-chat verification code');
    if (im === 'slack') return t('Slack 新建或选已有 App + 两个 token + 私聊口令绑定', 'Slack app (new or existing) + two tokens + a code in a direct message');
    const scans = scansOf(im, larkSource);
    return t(`${IM_LABELS[im]} ${scans} 次`, `${IM_LABELS[im]} ${scans} ${scans === 1 ? 'scan' : 'scans'}`);
  });
  prompter.info(t(
    `接下来${telegram ? '接入' : '扫码'}：${steps.join('，')}${saving ? `；${telegram ? '全部验证成功' : '扫完'}才保存实例` : ''}`,
    `Next, ${telegram ? 'connecting' : 'scanning'}: ${steps.join(', ')}${saving ? `; the instance is saved after ${telegram ? 'all of it checks out' : 'the scans are done'}` : ''}`,
  ));
}

// The language comes first, before the boat even, as everything after it
// is said in it. Chinese unless chosen; on a terminal whose locale is not
// Chinese it is asked (see i18n.mjs), and kept for later commands when the
// owner chose it here.
async function chooseLanguage({ prompter, env, flag }) {
  const choice = languageChoice({ env, flag });
  setLanguage(choice.language);
  if (choice.settled ? !choice.explicit : !prompter.tty) return;
  const codes = Object.keys(LANGUAGES);
  const chosen = choice.settled ? choice.language : await prompter.select('Language / 语言',
    codes.map((code) => ({ label: LANGUAGES[code], value: code })), { initial: codes.indexOf(choice.language), back: false });
  setLanguage(chosen);
  saveLanguage(chosen, env);
}

// The boat sails above the whole setup on a colour terminal; whatever ends
// the setup, the terminal gets its whole screen back.
export async function setupCommand(options = {}) {
  const { prompter, env = process.env, banner = openingBanner, lang = null } = options;
  await chooseLanguage({ prompter, env, flag: lang });
  const live = prompter.fancy ? await banner({ output: prompter.output, input: prompter.input, env, version: releaseInfo().version }) : null;
  // What the first pass learned (the detected agents), kept when ← goes all
  // the way back to 要做什么 and setup starts over.
  const memo = {};
  prompter.pageTop = () => live?.top() ?? null;
  try {
    for (;;) {
      const pendingOV = new Set();
      try {
        return await setupFlow({ ...options, live, memo, pendingOV });
      } catch (error) {
        if (!(error instanceof StartOver)) throw error;
      } finally {
        for (const file of pendingOV) fs.rmSync(file, { force: true });
      }
    }
  } finally {
    prompter.navigable = false;
    prompter.pageTop = null;
    live?.release();
  }
}

async function setupFlow({
  prompter,
  env = process.env,
  homeDir = os.homedir(),
  fetchImpl = globalThis.fetch,
  detect = { runtimes: detectRuntimes, openViking: detectOpenViking, larkApps: checkLarkApps },
  configureOV = configureOpenViking,
  connect = { lark: connectLark, weixin: connectWeixin, telegram: connectTelegram, slack: connectSlack, qq: connectQQ, email: askEmail, greet, pi: loginPi },
  control = { start: startCommand, restart: restartCommand, autostart: autostartCommand },
  listen = larkMessageListener,
  allocate = allocatePort,
  live = null,
  memo = {},
  pendingOV = new Set(),
}) {
  const out = (text) => prompter.write(text);
  const vika = vikaCommand(env);
  if (!memo.runtimes) {
    if (!prompter.fancy) out(t('Vika setup：添加、修改或删除这台机器上的 agent 实例。\n\n', 'Vika setup: add, change or remove the agent instances on this machine.\n\n'));
    memo.runtimes = await step(prompter, t('看看这台机器上有哪些 agent', 'Looking for agents on this machine'),
      () => detect.runtimes({ env }), foundLine);
    memo.mark = prompter.mark();
  } else {
    prompter.rewind(memo.mark);
  }
  let runtimes = memo.runtimes;
  // The connection this run chose (a new instance's, or a changed one), kept
  // for the next instance once the manifest is saved.
  let ov = null;
  const { path: configPath, value: manifest, ignored } = readManifest(env);
  const notice = ignoredFieldsNotice(ignored, configPath);
  if (notice) prompter.warn(notice);
  // Before anything is asked: which Feishu/Lark apps lack what this release
  // needs (after an upgrade, say), to say so and complete them.
  if (detect.larkApps && manifest?.agents?.length) memo.lacking ??= await detect.larkApps({ prompter, env, manifest });
  // ← and → move between the questions, until the scans begin.
  prompter.navigable = prompter.tty;
  const current = await chooseTarget(prompter, manifest, memo.lacking ?? []);
  if (current === 'remove') {
    try {
      return await removeInstance({ prompter, manifest, configPath, env, control });
    } catch (error) {
      if (error instanceof GoBack) throw new StartOver();
      throw error;
    }
  }
  if (!current) {
    runtimes = await ensureRuntime({ prompter, env, detect, connect, live }, runtimes);
    if (!runtimes) return 1;
    memo.runtimes = runtimes;
  }
  const kept = keptRuntime(current, runtimes);
  const others = manifest?.agents?.length > 0;
  const ctx = {
    prompter, out, env, homeDir, fetchImpl, runtimes, current, connect, kept, menu: Boolean(current), startOver: others, notes: [],
    agents: manifest?.agents ?? [],
    larkNeeds: (memo.lacking ?? []).find((needs) => needs.agent.id === current?.id) ?? null,
  };
  const state = initialState({ id: current?.id ?? suggestInstanceId(manifest), env, homeDir, current, runtimes, kept });
  const port = current ? current.port : await allocate(manifest);
  ctx.editOV = async () => {
    const detected = await configureOV({ prompter, env, homeDir, fetchImpl, detect: detect.openViking,
      preferredConfigPath: state.ovConfig, title: `OpenViking · ${current.id}`, canGoBack: true,
      leaveTo: t('返回实例设置', 'Back to instance settings') });
    const pending = new Set();
    try {
      const target = await prepareInstanceOpenViking({
        agent: { ...current, ovConfig: state.ovConfig }, detected, pending, env, homeDir, fetchImpl,
        detect: detect.openViking, reserved: new Set(manifest.agents.map(({ ovConfig }) => ovConfig)),
      });
      if (target !== state.ovConfig && pendingOV.has(state.ovConfig)) {
        fs.rmSync(state.ovConfig, { force: true });
        pendingOV.delete(state.ovConfig);
      }
      for (const file of pending) pendingOV.add(file);
      pending.clear();
      if (target !== state.ovConfig) ov = detected;
      state.ovConfig = target;
    } finally {
      for (const file of pending) fs.rmSync(file, { force: true });
    }
  };
  // The instance's own OV config, written for this name and this connection;
  // going back to change either writes it again.
  let prepared = null;
  const checkOV = async () => {
    const checked = await detect.openViking({ env, homeDir, fetchImpl, configPath: state.ovConfig });
    if (!checked?.usable) {
      throw new Error(t(`OpenViking（${state.id}）不可用：${checked?.reason ?? '检查失败'}。请恢复服务后再接入 IM。`,
        `OpenViking (${state.id}) is unavailable: ${checked?.reason ?? 'check failed'}. Restore the service before connecting IMs.`));
    }
    return checked;
  };
  ctx.checkOV = checkOV;
  const prepareOV = () => step(prompter, t('准备并验证实例的 OpenViking 配置', 'Preparing and checking this instance’s OpenViking config'), async () => {
    if (prepared?.id !== state.id || prepared?.ov !== ov) {
      for (const file of pendingOV) fs.rmSync(file, { force: true });
      pendingOV.clear();
      state.ovConfig = availableInstanceOvConfig(env, state.id, new Set(manifest?.agents.map(({ ovConfig }) => ovConfig)));
      writeInstanceOvConfig({ detected: ov, instanceId: state.id, target: state.ovConfig, exclusive: true });
      pendingOV.add(state.ovConfig);
      prepared = { id: state.id, ov };
    }
    await checkOV();
  }, t('OpenViking 已就绪，接下来接入 IM', 'OpenViking is ready; next, connect your IMs'));
  // The section heading comes from the wizard; ← goes back to 要做什么, if there was a choice.
  const chooseOV = async () => {
    ov = await configureOV({ prompter, env, homeDir, fetchImpl, detect: detect.openViking, title: null, canGoBack: others, previous: ov });
  };

  // The instance as the manifest will hold it.
  const build = (emailAccounts = mailPlan(state).list) => {
    const agent = {
      ...(current ?? {}),
      id: state.id,
      displayName: state.displayName,
      channel: state.channel,
      connectors: state.connectors,
      dataDir: state.dataDir,
      workspace: state.workspace,
      model: state.runtime.model,
    };
    if (state.role) agent.role = state.role;
    else delete agent.role;
    if (state.connectors.includes('lark')) agent.profile = current?.profile ?? `vika-${state.id}`;
    else delete agent.profile;
    agent.ovConfig = state.ovConfig;
    Object.assign(agent, state.switches);
    if (emailAccounts.length) agent.emailAccounts = emailAccounts;
    else delete agent.emailAccounts;
    return withDefaultTempDir(agent, { VIKA_TEMP_DIR: env.VIKA_TEMP_DIR ?? manifest?.env?.VIKA_TEMP_DIR });
  };
  // The IMs this run connects: the new ones, and Feishu again for another app.
  const toConnect = () => {
    const before = current ? connectorsOf(current) : [];
    return ims(state).filter((im) => !before.includes(im) || (im === 'lark' && larkSource(state, current) !== 'keep'));
  };
  const pinned = () => (state.runtime.binEnv && state.runtime.bin ? { [state.runtime.binEnv]: state.runtime.bin } : {});
  const manifestWith = (agent) => (current
    ? withChangedInstance(manifest, agent, pinned()) : withInstance(manifest, { ...agent, port }, pinned()));
  // The whole instance is known before any scan, so a manifest the daemon
  // would reject fails here rather than after two QR codes.
  const problem = (agent) => {
    try { validateManifest(configPath, manifestWith(agent)); return null; } catch (error) { return error.message; }
  };

  // The first instance is simply `primary`; a second one needs a name, and
  // its directories follow the name until the workspace is changed.
  const askId = async () => {
    if (!others) return;
    const before = state.id;
    state.id = await prompter.text(t('实例名称（用在命令和目录里）', 'Instance name (used in commands and folders)'), {
      label: t('实例名称', 'Instance name'), initial: state.id, validate: (value) => instanceIdProblem(value, manifest),
    });
    state.dataDir = path.join(vikaHome(env), 'data', state.id);
    if (state.workspace === path.join(homeDir, 'vika-workspace', before)) state.workspace = path.join(homeDir, 'vika-workspace', state.id);
  };
  // The mailboxes once the rest is known: checked first with a stand-in,
  // since their names come from the addresses.
  const askMail = async () => {
    const wantsEmail = state.connectors.includes('email');
    const early = problem(build(wantsEmail ? [{ id: 'email-check', config: emailAccountPath(env, state.id, 'check') }] : []));
    if (early) throw new Error(early);
    if (wantsEmail) await askMailboxes(ctx, state);
    const late = problem(build());
    if (late) throw new Error(late);
  };
  const confirmNew = async () => {
    state.switches.meetingNotes = defaultMeetingNotes(state, ctx.agents);
    const addresses = mailPlan(state).added;
    const channels = ims(state).length > 1
      ? t(`${imList(ims(state))}，主渠道：${IM_LABELS[state.channel]}`, `${imList(ims(state))}; primary: ${IM_LABELS[state.channel]}`)
      : imList(ims(state));
    table(prompter, t(`将添加实例 ${state.id}`, `New instance ${state.id}`), [
      [t('机器人', 'Bot'), `${state.displayName} · ${state.runtime.label}`],
      [t('角色', 'Role'), roleShown(state, homeDir)],
      [t('消息渠道', 'Channels'), channels],
      ...(larkSource(state, null) ? [[t('飞书机器人', 'Feishu/Lark bot'), larkAppShown(state, null)]] : []),
      ...(addresses.length ? [[t('邮箱', 'Email'), list(addresses)]] : []),
      [t('记忆', 'Memory'), 'OpenViking'],
      [t('其他设置', 'Other settings'), t(`${switchesShown(state)}（以后在修改实例里调）`, `${switchesShown(state)} (change them later by changing the instance)`)],
      [t('工作目录', 'Workspace'), shortenHome(state.workspace, homeDir)],
      [t('数据目录', 'Data'), shortenHome(state.dataDir, homeDir)],
    ]);
    connectionSteps(prompter, ims(state), { saving: true, larkSource: larkSource(state, null) });
    const go = ims(state).some((im) => TOKEN_IMS.includes(im)) ? t('开始接入？', 'Start connecting?')
      : ims(state).length ? t('开始扫码？', 'Start scanning?') : t('保存并启动？', 'Save and start?');
    if (await prompter.confirm(go)) return null;
    prompter.info(t(`什么都没保存；准备好了再运行 ${vika} setup。`, `Nothing was saved; run ${vika} setup when you are ready.`));
    return DECLINED;
  };

  const steps = current ? [] : [
    { section: { title: t('OpenViking 记忆', 'OpenViking memory'), step: 1, total: 4 }, ask: chooseOV },
    { section: { title: t('机器人', 'Bot'), step: 2, total: 4 }, ask: askId }, { ask: askName }, { ask: askRole }, { ask: askWorkspace }, { ask: askRuntime },
    { ask: prepareOV },
    { section: { title: t('消息渠道', 'Channels'), step: 3, total: 4 }, ask: askIms }, { ask: askChannel }, { ask: askLarkApp }, { ask: askMail },
    { section: { title: t('确认', 'Confirm'), step: 4, total: 4 }, ask: confirmNew },
  ];
  const run = {};
  const page = {};
  const original = current ? Object.fromEntries(SETTINGS.map(({ key, shown }) => [key, shown(state, ctx)])) : null;
  let newIms;
  let larkCliBin;
  let agent;
  let plan;
  let lark = null;
  // The questions, then the codes to scan. ← while a code waits to be
  // scanned comes back to the questions; its pending OV config can still be
  // removed. Once a scan is confirmed (an app made, an account bound) it cannot.
  for (;;) {
    prompter.navigable = prompter.tty;
    if (current) {
      const describe = () => describeChanges(current, build(), mailPlan(state), homeDir, state);
      if (!await editInstance(ctx, state, { describe, check: () => problem(build()), before: original, page })) return 0;
      out(`\n${t(`将修改 ${current.displayName}（${current.id}）：`, `Changing ${current.displayName} (${current.id}):`)}\n${describe().map((change) => `  ${change}\n`).join('')}`);
      connectionSteps(prompter, toConnect(), { larkSource: larkSource(state, current) });
    } else if (await wizard(ctx, state, steps, run) === DECLINED) {
      return 1;
    }
    prompter.navigable = false;
    newIms = toConnect();

    // The release's own lark-cli, unless the manifest or environment pins another.
    larkCliBin = state.connectors.includes('lark')
      ? (env.LARK_CLI_BIN || manifest?.env?.LARK_CLI_BIN || resolveLarkCli(env))
      : null;
    if (newIms.includes('lark') && !larkCliBin) {
      out(t('\n找不到 lark-cli：这个安装不完整，重新运行安装脚本。\n', '\nlark-cli is missing: this install is incomplete, run the installer again.\n'));
      return 1;
    }
    agent = build();
    plan = { id: state.id, displayName: state.displayName, dataDir: state.dataDir };
    // Answers and mailbox setup can take a while: check again before the
    // first QR scan or bot token, using the actual instance identity.
    if (newIms.length) await checkOV();
    // A code to scan needs the whole screen; the boat comes back afterwards.
    if (newIms.length) live?.release();
    try {
      lark = newIms.includes('lark')
        ? await connect.lark({
          plan, prompter, larkCli: new LarkCli(larkCliBin), fetchImpl, profile: agent.profile, debug: Boolean(env.VIKA_DEBUG), canGoBack: prompter.tty,
          source: larkSource(state, current), app: state.larkApp?.app ?? null,
          others: (manifest?.agents ?? []).filter((other) => other.id !== state.id && other.profile),
        })
        : null;
      // People @ the bot by its app's name, so that is the default; an owner
      // who has called it something else for long may keep that.
      if (lark?.name && lark.name !== state.displayName && lark.name.length <= 40
        && await prompter.confirm(t(`这个机器人在飞书里叫「${lark.name}」，Vika 里也这么叫它？（选否就还叫「${state.displayName}」）`,
          `This bot is called "${lark.name}" on Feishu/Lark; call it that in Vika too? (No keeps "${state.displayName}")`))) {
        state.displayName = lark.name;
        plan.displayName = lark.name;
        agent = build();
      }
      let bound = Boolean(lark);
      if (newIms.includes('weixin')) {
        await connect.weixin({ plan, prompter, canGoBack: prompter.tty && !bound });
        bound = true;
      }
      if (newIms.includes('telegram')) {
        await connect.telegram({ plan, prompter, fetchImpl, canGoBack: prompter.tty && !bound });
        bound = true;
      }
      if (newIms.includes('slack')) {
        await connect.slack({ plan, prompter, fetchImpl, canGoBack: prompter.tty && !bound });
        bound = true;
      }
      if (newIms.includes('qq')) await connect.qq({ plan, prompter, fetchImpl, canGoBack: prompter.tty && !bound });
      break;
    } catch (error) {
      if (!(error instanceof GoBack)) throw error;
      live?.resume();
      // What sent the scans back is said where they come back to, after the
      // screen is taken back there.
      if (current) {
        page.notice = error.notice;
        prompter.rewind(page.mark, { whole: true });
      } else {
        returnToLast(prompter, steps, run);
        if (error.notice) prompter.warn(error.notice);
      }
    }
  }
  // The app the instance keeps gets what it lacks (a scan) before the
  // instance restarts, so it starts with it.
  if (current && state.larkRepair && !newIms.includes('lark')) {
    live?.release();
    await (connect.larkApp ?? updateLarkApp)({ agent, prompter, larkCli: new LarkCli(larkCliBin), register: connect.register });
  }
  live?.resume();
  const id = state.id;
  const mail = mailPlan(state);
  for (const { config, account } of mail.write) writeEmailAccount(config, account);
  writeRoleFile(state, id);

  fs.mkdirSync(state.workspace, { recursive: true, mode: 0o700 });
  writeManifest(configPath, manifestWith(agent), { expected: manifest });
  pendingOV.delete(agent.ovConfig);
  if (ov && agent.ovConfig !== current?.ovConfig) saveOVConnection(ov, { env, homeDir });
  for (const mailbox of mail.removed) fs.rmSync(mailbox.config, { force: true });

  prompter.section(current ? t('重启', 'Restart') : t('启动', 'Start'));
  prompter.success(t(`配置已写入 ${shortenHome(configPath, homeDir)}`, `Saved ${shortenHome(configPath, homeDir)}`));
  // pm2's own progress lines stay out of the way; the spinners say it.
  const quiet = { write() {} };
  const command = current ? 'restart' : 'start';
  try {
    await step(prompter, t(`${current ? '重启' : '启动'} ${state.displayName}（${id}）`, `${current ? 'Restarting' : 'Starting'} ${state.displayName} (${id})`),
      () => control[command](id, { env, stdout: quiet }),
      t(`${state.displayName}（${id}）在后台跑起来了`, `${state.displayName} (${id}) is running in the background`));
  } catch (error) {
    out(t(`\n配置已保存，但${current ? '重启' : '启动'}没有完成：${error.message}\n运行 ${vika} logs ${id} 查看具体错误；修好后运行 ${vika} ${command} ${id}\n`,
      `\nSaved, but the ${command} did not finish: ${error.message}\nRun ${vika} logs ${id} for the exact error; once fixed, run ${vika} ${command} ${id}\n`));
    return 1;
  }
  if (!current) {
    // The instance is up. The shared Web (it routes to every instance, so it
    // restarts to pick this one up) and the updater are helpers: report, go on.
    for (const [action, target, label] of [
      ['restart', 'web', t(`控制台 http://127.0.0.1:${WEB_PORT}`, `Console http://127.0.0.1:${WEB_PORT}`)],
      ['start', 'updater', t('自动升级服务', 'Auto-upgrade service')],
    ]) {
      const spinning = prompter.spin(t(`启动${label}`, `Starting the ${label.toLowerCase()}`));
      await control[action](target, { env, stdout: quiet }).then(
        () => spinning.succeed(label),
        (error) => spinning.warn(t(`vika-${target} 没有起来：${error.message}；之后运行 ${vika} ${action} ${target}`,
          `vika-${target} did not start: ${error.message}; later, run ${vika} ${action} ${target}`)),
      );
    }
  }
  let greeted = false;
  // Only someone at a terminal can write to the bot while setup waits; where
  // the feed stood before the hello, so a quick reply still counts.
  const listener = lark && prompter.tty ? await listen({ port, fetchImpl }) : null;
  if (lark) {
    const text = greeting({ name: state.displayName, newInstance: !current, primary: state.channel === 'lark' });
    const spinning = prompter.spin(t('在飞书里跟你打个招呼', 'Saying hello on Feishu/Lark'));
    await connect.greet({ larkCliBin, profile: lark.profile, userId: lark.ownerOpenId, text }).then(
      () => { greeted = true; spinning.succeed(t('已在飞书私聊里跟你打了招呼', 'Said hello in your Feishu/Lark chat')); },
      (error) => spinning.warn(t(`飞书打招呼的消息没发出去：${error.message}`, `The hello on Feishu/Lark did not go out: ${error.message}`)),
    );
  }
  const received = listener
    ? await confirmReceiving({ prompter, listener, greeted, name: state.displayName, id, vika })
    : false;
  if (!current && prompter.tty && control.autostart && !autostartEnabled({ homeDir })) await offerAutostart({ prompter, env, homeDir, control, vika });

  // What to do next, one line per thing; on Weixin the owner has to write first.
  const next = [];
  if (lark) {
    next.push([IM_LABELS.lark, received ? t('已经收到你的消息，接着聊就行', 'Your message got through; carry on there')
      : greeted ? t('私聊里它已经跟你打了招呼，直接回它', 'It said hello in your chat; just reply')
        : t(`在飞书里搜「${state.displayName}」，给它发消息`, `Search for "${state.displayName}" and message it`)]);
  }
  if (newIms.includes('weixin')) {
    next.push([IM_LABELS.weixin, t('先给它发一句「你好」，在那之前它没法主动找你', 'Say "hi" to it first; until then it cannot reach you')]);
  }
  if (newIms.includes('telegram')) next.push(['Telegram', t('在刚绑定的 Bot 私聊中发一条任务', 'Message the bot you just bound')]);
  if (newIms.includes('qq')) next.push(['QQ', t('在刚绑定的机器人私聊中发一条任务', 'Message the bot you just bound')]);
  if (newIms.includes('slack')) {
    next.push(['Slack', t('私聊机器人发任务；要它参与频道，在频道里 /invite 它；命令前加个空格（ /new）',
      'Message the bot directly; /invite it to channels it should join; put a space before commands ( /new)')]);
  }
  if (!current) {
    next.push([t('控制台', 'Console'), t(`http://127.0.0.1:${WEB_PORT}  每一轮在做什么都在这里`, `http://127.0.0.1:${WEB_PORT}  what every turn did`)]);
    if (env.SSH_CONNECTION) {
      next.push(['', t(`远程机器：本地 ssh -L ${WEB_PORT}:127.0.0.1:${WEB_PORT} 再打开`, `Remote machine: ssh -L ${WEB_PORT}:127.0.0.1:${WEB_PORT} first, then open it`)]);
    }
    next.push([t('常用命令', 'Commands'), 'vika status · vika logs · vika setup']);
  }
  const title = t(`${state.displayName}（${id}）${current ? '已更新' : '已就绪'}`, `${state.displayName} (${id}) is ${current ? 'updated' : 'ready'}`);
  if (prompter.fancy) {
    table(prompter, title, next);
  } else {
    out(`\n${title}${t('。', '.')}\n${next.map(([key, value]) => `  ${key ? `${key}${t('：', ': ')}` : ''}${value}\n`).join('')}`);
  }
  if (vika !== 'vika' && !current) {
    prompter.info(t(`这个终端还找不到 vika：新开一个终端就行（安装时已加进 shell 配置），或先用 ${vika}`,
      `This terminal does not know vika yet: open a new one (the installer added it to your shell setup), or use ${vika} for now`));
  }
  return 0;
}

// Starting at login is one question at the end of a first setup. A machine
// without a user service manager says so, and setup still succeeds.
async function offerAutostart({ prompter, env, homeDir, control, vika }) {
  if (!await prompter.confirm(t('开机后自动启动 Vika？', 'Start Vika when this computer starts?'), true)) {
    prompter.info(t(`之后想开：${vika} autostart enable`, `To turn it on later: ${vika} autostart enable`));
    return;
  }
  let said = '';
  await control.autostart(['enable'], { env, homeDir, output: { write: (text) => { said += text; } } }).then(
    () => {
      prompter.success(t('开机自启已开启', 'Autostart is on'));
      if (/loginctl/.test(said)) {
        prompter.info(t('Linux 服务器上还要运行一次 loginctl enable-linger，否则退出登录后 Vika 会停。',
          'On a Linux server, also run loginctl enable-linger once, or Vika stops when you log out.'));
      }
    },
    (error) => prompter.warn(t(`这台机器开不了开机自启：${String(error.message).split('\n')[0]}`, `Autostart is not available here: ${String(error.message).split('\n')[0]}`)),
  );
}

// What a change to an instance does, one line each, for the confirmation.
function describeChanges(before, after, mailboxes, homeDir, state) {
  const changes = [];
  const was = connectorsOf(before);
  if (before.ovConfig !== after.ovConfig) changes.push(t('更新 OpenViking 连接', 'Update the OpenViking connection'));
  if (before.displayName !== after.displayName) changes.push(`${t('机器人名字：', 'Bot name: ')}${before.displayName} → ${after.displayName}`);
  if (before.role !== after.role) {
    const shown = (agent) => roleShown(agent, homeDir);
    changes.push(t(`角色：${shown(before)} → ${shown(after)}（模型会开一次新的会话）`, `Role: ${shown(before)} → ${shown(after)} (the model starts a new session)`));
  }
  if (before.workspace !== after.workspace) {
    const move = `${shortenHome(before.workspace, homeDir)} → ${shortenHome(after.workspace, homeDir)}`;
    changes.push(t(`工作目录：${move}（模型会开一次新的会话）`, `Workspace: ${move} (the model starts a new session)`));
  }
  if (before.model !== after.model) changes.push(`${t('运行时：', 'Runtime: ')}${before.model} → ${after.model}`);
  for (const { key, label } of SWITCHES()) {
    const was = before[key] ?? SWITCH_DEFAULTS[key];
    if (was === after[key]) continue;
    const arrow = `${onOff(was)} → ${onOff(after[key])}`;
    const policy = POLICY_SWITCHES.has(key);
    changes.push(t(`${label}：${arrow}${policy ? '（模型会开一次新的会话）' : ''}`, `${label}: ${arrow}${policy ? ' (the model starts a new session)' : ''}`));
  }
  for (const im of ['lark', 'weixin', 'telegram', 'slack', 'qq']) {
    if (!was.includes(im) && after.connectors.includes(im)) {
      changes.push(im === 'qq' ? t('接入QQ（需要 AppID / AppSecret 和私聊验证码）', 'Connect QQ (needs AppID / AppSecret and a private-chat verification code)')
        : im === 'telegram' ? t(`接入${IM_LABELS[im]}（需要 Bot token 和 Start 绑定）`, `Connect ${IM_LABELS[im]} (needs a bot token and Start)`)
        : im === 'slack' ? t(`接入${IM_LABELS[im]}（需要新建或选已有 App、两个 token 和私聊口令绑定）`, `Connect ${IM_LABELS[im]} (needs a new or existing app, two tokens and a code)`)
          : t(`接入${IM_LABELS[im]}（需要扫码）`, `Connect ${IM_LABELS[im]} (needs a scan)`));
    }
    if (was.includes(im) && !after.connectors.includes(im)) changes.push(t(`不再接${IM_LABELS[im]}`, `Disconnect ${IM_LABELS[im]}`));
  }
  if (state.larkRepair && larkSource(state, before) === 'keep') {
    changes.push(t('补齐飞书应用缺的权限和事件（需要扫码）', 'Complete the Feishu/Lark app (needs a scan)'));
  }
  if (was.includes('lark') && !['keep', null].includes(larkSource(state, before))) {
    changes.push(t(`换飞书应用：${larkAppShown(state, before)}（需要扫码）`, `Another Feishu/Lark app: ${larkAppShown(state, before)} (needs a scan)`));
  }
  if ((before.channel ?? was[0]) !== after.channel) {
    changes.push(`${t('主渠道：', 'Primary channel: ')}${IM_LABELS[before.channel ?? was[0]]} → ${IM_LABELS[after.channel]}`);
  }
  for (const address of mailboxes.added) changes.push(t(`接入邮箱 ${address}`, `Add mailbox ${address}`));
  for (const address of mailboxes.updated) changes.push(t(`更新邮箱 ${address} 的密码`, `New password for ${address}`));
  for (const mailbox of mailboxes.removed) {
    changes.push(t(`移除邮箱 ${addressOf(mailbox)}（删掉保存的密码）`, `Remove mailbox ${addressOf(mailbox)} (its saved password is deleted)`));
  }
  return changes;
}
