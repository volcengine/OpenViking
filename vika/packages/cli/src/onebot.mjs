// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import { OneBotError, validToken, validUrl } from '../../daemon/src/onebot/api.mjs';
import { loginOneBot } from '../../daemon/src/onebot/auth.mjs';
import { PolicyFile } from '../../daemon/src/onebot/policy.mjs';
import { OneBotState } from '../../daemon/src/onebot/state.mjs';
import { t } from './i18n.mjs';
import { IM_LABELS } from './instances.mjs';
import { GoBack, PromptAborted } from './prompt.mjs';

// A QQ account that an OneBot v11 implementation (NapCat, LLOneBot,
// Lagrange.OneBot) keeps logged in. Vika connects to that implementation's
// WebSocket server; the owner is whoever sends the one-time code to the
// account in a private friend message. Every group the account is in is
// followed unless the policy file sets it to "mention" or "off".

const STARTER_POLICY = '{\n  "groups": {\n    "*": "ambient"\n  }\n}\n';

export const policyPath = (dataDir) => new PolicyFile(dataDir).path;

// What the policy file puts in force for the groups it does not name, read
// as the daemon reads it: null when there is no file or it is the starter
// file below (every group "ambient"), "invalid" for a file the daemon would
// refuse, else the mode of "*".
export function groupDefault(dataDir) {
  const file = new PolicyFile(dataDir);
  if (!fs.existsSync(file.path)) return null;
  try {
    const policy = file.current();
    return fs.readFileSync(file.path, 'utf8') === STARTER_POLICY ? null : policy.otherGroups;
  } catch { return 'invalid'; }
}

// An existing policy file is the owner's: say what it decides instead of the default.
function keptPolicy(file, mode) {
  if (mode === 'invalid') {
    return t(`已有的 ${file} 保留不动；它读不成有效的策略文件。`, `The existing ${file} is kept as it is; it could not be read as a valid policy.`);
  }
  return t(`已有的 ${file} 保留不动：没写在里面的群按 "${mode}"，写在里面的群各按自己那一项。`,
    `The existing ${file} is kept as it is: groups it does not name are "${mode}", and a group it names follows its own entry.`);
}

function explain(error) {
  if (error instanceof OneBotError && error.auth) {
    return t('OneBot 拒绝了这个 access token：确认它和 WebSocket 服务器里设置的完全一样。',
      'OneBot refused the access token; check it matches the one set on the WebSocket server exactly.');
  }
  if (error.name === 'TimeoutError') return t('绑定超时：请重新输入，并在 5 分钟内用你自己的 QQ 私聊这个账号发送新的口令。',
    'Binding timed out; enter them again and send the new code to the account in a private QQ message within 5 minutes.');
  if (/Could not connect/.test(error.message)) {
    return t('连不上 OneBot 的 WebSocket 服务器：检查地址和端口、它是否在运行；Lagrange 遇到错误的 token 也会这样拒绝连接。',
      'Could not reach the OneBot WebSocket server; check the address and port and that it is running. Lagrange also refuses the connection this way when the token is wrong.');
  }
  if (/invalid login account/.test(error.message)) return t('OneBot 没有返回登录的 QQ 号：确认这个账号已经在 NapCat、LLOneBot 或 Lagrange 里登录好了。',
    'OneBot named no logged-in QQ account; check the account is logged in to NapCat, LLOneBot or Lagrange.');
  if (/connection closed/.test(error.message)) return t('绑定完成前 OneBot 连接断开了，请重试。', 'The OneBot connection closed before binding finished; try again.');
  return t('OneBot 连接失败，请检查地址和 token 后重试。', 'Could not connect to OneBot; check the address and the token, then try again.');
}

// The address saved before is offered first, and the one typed last on a
// retry: renewing a login usually changes only the token.
function savedUrl(dataDir) {
  const state = new OneBotState(dataDir);
  try { return state.account()?.url ?? ''; } finally { state.close(); }
}

export async function connectOneBot({ plan, prompter, WebSocketImpl = globalThis.WebSocket, canGoBack = false }) {
  prompter.section(IM_LABELS.onebot);
  prompter.info(t('1. 用 NapCat、LLOneBot 或 Lagrange.OneBot 登录一个给 Vika 用的 QQ 号，在里面开一个 WebSocket 服务器（正向 WebSocket），消息格式选 array，并设置 access token。',
    '1. Log a QQ account for Vika in to NapCat, LLOneBot or Lagrange.OneBot, and open a WebSocket server (forward WebSocket) there with the array message format and an access token.'));
  prompter.info(t('2. 用你自己的 QQ 加这个号为好友：绑定主人要在好友私聊里发口令。',
    '2. Add that account as a friend from your own QQ: binding the owner takes a code in a private friend message.'));
  prompter.info(t('这个号可以同时接着别的 OneBot 程序；token 不能省，因为能连上这个服务器的程序就能操作这个 QQ 号。',
    'Other OneBot programs may use the same account. The token is required: any program that reaches the server can act as the account.'));
  const file = policyPath(plan.dataDir);
  const kept = groupDefault(plan.dataDir);
  prompter.info(kept === null ? t(`绑定后，这个号会跟进它所在的每个群；不想让它进群，先在 ${file} 里写上 {"groups": {"*": "off"}}。`,
    `Once bound, the account follows every group it is in; to keep it out, write {"groups": {"*": "off"}} to ${file} first.`) : keptPolicy(file, kept));
  if (canGoBack) prompter.info(t('输入地址、token 或等待绑定时，按 ← / Esc 可以返回。', 'While entering the address or token, or waiting, press ← / Esc to go back.'));
  let url = savedUrl(plan.dataDir);
  const navigable = prompter.navigable;
  try {
    for (;;) {
      prompter.navigable = canGoBack;
      url = await prompter.text(t('WebSocket 地址', 'WebSocket address'), { initial: url,
        validate: (value) => (validUrl(value) ? null : t('以 ws:// 或 wss:// 开头，例如 ws://127.0.0.1:3001；token 单独输入，不要写进地址。',
          'It starts with ws:// or wss://, e.g. ws://127.0.0.1:3001; enter the token on its own, not in the address.')) });
      const token = await askToken(prompter);
      prompter.navigable = false;
      try {
        const bound = await verify({ plan, prompter, url, token, WebSocketImpl, canGoBack });
        const account = [bound.selfName, bound.selfId].filter(Boolean);
        prompter.success(t(`${IM_LABELS.onebot} 已接好（${account.join('，')}）`, `${IM_LABELS.onebot} is connected (${account.join(', ')})`));
        showGroups(prompter, plan.dataDir, bound.groups);
        return;
      } catch (error) {
        if (error instanceof GoBack || error instanceof PromptAborted) throw error;
        prompter.warn(explain(error));
        prompter.navigable = canGoBack;
        const next = await prompter.select(t('接下来怎么办', 'What now'), [
          { label: t('重新输入地址和 token 并绑定', 'Enter the address and token again'), value: 'retry' },
          { label: canGoBack ? t('返回上一步', 'Go back') : t('退出接入', 'Stop'), value: 'leave' },
        ]);
        if (next === 'leave') throw canGoBack ? new GoBack() : new PromptAborted();
      }
    }
  } finally { prompter.navigable = navigable; }
}

async function askToken(prompter) {
  for (;;) {
    const token = (await prompter.secret('OneBot access token')).trim();
    if (token && validToken(token)) return token;
    prompter.warn(t('access token 不能为空，也不能有空格：完整复制 WebSocket 服务器里设置的 token。',
      'The access token cannot be empty or contain spaces; copy all of the token set on the WebSocket server.'));
  }
}

async function verify({ plan, prompter, url, token, WebSocketImpl, canGoBack }) {
  const state = new OneBotState(plan.dataDir);
  const controller = new AbortController();
  const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(300_000)]);
  const keys = prompter.watchKey(null, { onBack: canGoBack ? () => controller.abort(new GoBack()) : null });
  try {
    return await loginOneBot({ state, url, token, WebSocketImpl, signal,
      onChallenge: ({ code, selfId, selfName }) => {
        const account = selfName ? t(`${selfName}（${selfId}）`, `${selfName} (${selfId})`) : selfId;
        prompter.info(t(`绑定主人：5 分钟内，用你自己的 QQ 私聊 ${account}，发送下面这一行（不要发到群里）：`,
          `Bind the owner: within 5 minutes, send the line below to ${account} in a private QQ message from your own account (not in a group):`));
        prompter.write(`\n${code}\n\n`);
        prompter.info(t('等待你发送口令…', 'Waiting for the code…'));
      },
    });
  } catch (error) { throw signal.aborted ? signal.reason : error; }
  finally { keys.stop(); state.close(); }
}

// Every group is followed from the start, so the ids to change are shown
// here. An existing policy file is the owner's and is left as it is.
function showGroups(prompter, dataDir, groups) {
  const file = policyPath(dataDir);
  const kept = groupDefault(dataDir);
  try { fs.writeFileSync(file, STARTER_POLICY, { flag: 'wx', mode: 0o600 }); } catch (error) {
    if (error.code !== 'EEXIST') throw error;
  }
  prompter.info(kept === null ? t(`这个账号所在的每个群默认都接入，按 "ambient"：看群里所有消息，自己决定要不要说话。在 ${file} 的 "groups" 里可以把某个群号改成 "mention"（只在有人 @ 它、回复它，或说到 "names" 里列出的某个名字时回应）或 "off"（不接）；"*" 改的是没写出来的群。保存后自动生效，不用重启。`,
    `Every group the account is in is followed by default as "ambient": it reads every message and decides when to speak. Under "groups" in ${file}, set a group's number to "mention" (answers only when someone @mentions it, replies to it or uses one of the names listed under "names") or "off" (not connected); "*" sets the groups not listed. Saved changes apply without a restart.`)
    : `${keptPolicy(file, kept)}${t('在 "groups" 里可以把某个群号改成 "ambient"（看群里所有消息，自己决定要不要说话）、"mention"（只在有人 @ 它、回复它，或说到 "names" 里列出的某个名字时回应）或 "off"（不接）；"*" 改的是没写出来的群。保存后自动生效，不用重启。',
      ' Under "groups", set a group\'s number to "ambient" (reads every message and decides when to speak), "mention" (answers only when someone @mentions it, replies to it or uses one of the names listed under "names") or "off" (not connected); "*" sets the groups not listed. Saved changes apply without a restart.')}`);
  if (!groups.length) {
    prompter.info(t('没有读到这个账号所在的群；群号可以在 QQ 的群资料里看到。', 'Could not list the account\'s groups; a group\'s number is on its profile in QQ.'));
    return;
  }
  prompter.info(t('这个账号所在的群：', 'The account\'s groups:'));
  const width = Math.max(...groups.map((group) => group.id.length));
  prompter.write(`${groups.map((group) => `   ${group.id.padEnd(width)}  ${group.name}`.trimEnd()).join('\n')}\n`);
  prompter.info(t(`例如：{ "groups": { "${groups[0].id}": "mention" } }；只接写出来的群：{ "groups": { "*": "off", "${groups[0].id}": "ambient" } }`,
    `For example: { "groups": { "${groups[0].id}": "mention" } }; to follow only the groups listed: { "groups": { "*": "off", "${groups[0].id}": "ambient" } }`));
}
