// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { bundledLarkCli } from '../../daemon/src/lark-cli-bin.mjs';
import { restartCommand, stopCommand } from './control.mjs';
import { connectTelegram } from './telegram.mjs';
import { connectQQ } from './qq.mjs';
import { connectSlack } from './slack.mjs';
import { askEmail, mailboxesOf, writeEmailAccount } from './email.mjs';
import { t } from './i18n.mjs';
import { IM_LABELS, chooseInstance, loadInstances } from './instances.mjs';
import { LarkCli } from './lark-cli.mjs';
import { which } from './runtimes.mjs';
import { completeLarkApp, connectWeixin, larkScopes, reauthorizeOwner, weixinFirstMessage } from './setup.mjs';

// `vika login <lark|weixin|email|telegram|slack|qq> [instance]`: authorize again what an
// instance already has, after a login expired or a password changed. For
// Feishu/Lark it first checks the app has what Vika needs, and an owner or
// administrator of the app can add what it lacks with one scan.
// Connecting something new is `vika setup` (change the instance). The
// instance may be left out when there is only one, and is chosen otherwise.

const usage = () => t(
  '用法：vika login <lark|weixin|email|telegram|slack|qq> [实例]\n'
  + '  登录过期或改了密码后重新授权；要接入新的 IM，运行 vika setup 修改实例。只有一个实例时可以不写实例名。\n',
  'Usage: vika login <lark|weixin|email|telegram|slack|qq> [instance]\n'
  + '  Authorize again after a login expired or a password changed; to connect a new IM, change the instance with vika setup. With one instance the name can be left out.\n',
);

function larkCliBin(env, deployment) {
  const bundled = bundledLarkCli();
  return env.LARK_CLI_BIN || deployment.env?.LARK_CLI_BIN || (bundled === 'lark-cli' ? which('lark-cli', env) : bundled);
}

async function restart({ agent, prompter, env, control }) {
  try {
    await control.restart(agent.id, { env });
    return true;
  } catch (error) {
    prompter.write(t(`\n配置已更新，但实例没能重启：${error.message}\n修好后运行 vika restart ${agent.id}\n`,
      `\nSaved, but the instance did not restart: ${error.message}\nOnce that is fixed, run vika restart ${agent.id}\n`));
    return false;
  }
}

export async function loginCommand([connector, requested, ...extra], {
  prompter,
  env = process.env,
  connect = { weixin: connectWeixin, telegram: connectTelegram, slack: connectSlack, qq: connectQQ, email: askEmail },
  control = { restart: restartCommand, stop: stopCommand },
} = {}) {
  if (!['lark', 'weixin', 'email', 'telegram', 'slack', 'qq'].includes(connector) || extra.length) {
    prompter.write(usage());
    return 2;
  }
  const label = IM_LABELS[connector];
  const deployment = loadInstances(env);
  if (!deployment) throw new Error(t('还没有任何实例：先运行 vika setup', 'No instance yet: run vika setup first'));
  const named = requested && deployment.agents.find((agent) => agent.id === requested);
  if (named && !named.connectors.includes(connector)) {
    throw new Error(t(`实例 ${requested} 没有接${label}；要接入，运行 vika setup，选择修改 ${requested}`,
      `Instance ${requested} has no ${label}; to connect it, run vika setup and change ${requested}`));
  }
  // Only the instances that have it can renew it.
  const having = { ...deployment, agents: deployment.agents.filter((agent) => agent.connectors.includes(connector)) };
  if (having.agents.length === 0) throw new Error(t(`还没有实例接${label}；要接入，运行 vika setup 修改实例`, `No instance has ${label}; to connect it, change an instance with vika setup`));
  const agent = await chooseInstance({ deployment: having, requested, prompter, question: t(`重新授权哪个实例的${label}`, `Which instance's ${label} to authorize again`) });

  // Only one receiver at a time: the instance stops while the new tokens are checked.
  if (['telegram', 'slack', 'qq'].includes(connector)) {
    prompter.write(t(`验证新密钥时会暂时停止这个实例，避免同时接收 ${label} 消息；完成后重新启动。\n`,
      'The instance stops while the new credentials are checked, so nothing else receives its messages; it starts again afterwards.\n'));
    await control.stop(agent.id, { env });
    try { await connect[connector]({ plan: agent, prompter }); } catch (error) {
      await control.restart(agent.id, { env }).catch(() => {
        prompter.write(t(`实例没有恢复启动；请运行 vika restart ${agent.id}\n`, `The instance did not start again; run vika restart ${agent.id}\n`));
      });
      throw error;
    }
    if (!await restart({ agent, prompter, env, control })) return 1;
    prompter.write(t(`\n${agent.displayName}（${agent.id}）的 ${label} 已更新。\n`, `\n${label} is updated for ${agent.displayName} (${agent.id}).\n`));
    return 0;
  }

  if (connector === 'weixin') {
    await connect.weixin({ plan: agent, prompter });
    // A new login starts a new conversation: the owner writes first again.
    prompter.write(t(`运行中的实例会在下次重连时用上新的登录，不用重启。\n\n最后一步：${weixinFirstMessage()}\n`,
      `The running instance picks up the new login when it reconnects; no restart needed.\n\nLast step: ${weixinFirstMessage()}\n`));
    return 0;
  }
  if (connector === 'email') {
    // Each mailbox's Connector reads its account file when it starts.
    const mailboxes = mailboxesOf(agent);
    const shown = (mailbox) => mailbox.address ?? mailbox.id;
    const mailbox = mailboxes.length === 1 ? mailboxes[0] : await prompter.select(t('更新哪个邮箱的密码', 'Which mailbox has a new password'),
      mailboxes.map((candidate) => ({ label: shown(candidate), value: candidate })));
    const account = await connect.email({ prompter, fixed: mailbox.account,
      taken: mailboxes.filter((other) => other !== mailbox).map((other) => other.address).filter(Boolean) });
    writeEmailAccount(mailbox.config, account);
    if (!await restart({ agent, prompter, env, control })) return 1;
    prompter.write(t(`\n${agent.displayName}（${agent.id}）的邮箱 ${account.address} 已更新。\n`, `\nMailbox ${account.address} is updated for ${agent.displayName} (${agent.id}).\n`));
    return 0;
  }
  const bin = larkCliBin(env, deployment);
  if (!bin) throw new Error(t('找不到 lark-cli：这个安装不完整，重新运行安装脚本', 'lark-cli is missing: this install is incomplete, run the installer again'));
  const larkCli = new LarkCli(bin);
  const scopes = larkScopes();
  const { inspected } = await completeLarkApp({ agent, prompter, larkCli, scopes, register: connect.register });
  await reauthorizeOwner({ agent, prompter, larkCli, scopes, inspected });
  return 0;
}
