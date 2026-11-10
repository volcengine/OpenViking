// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { verifyAccount } from '../../daemon/src/email/client.mjs';
import { t } from './i18n.mjs';
import { vikaHome } from './manifest.mjs';
import { PromptAborted } from './prompt.mjs';

// The mailbox steps of `vika setup` and `vika login email`: an address and the
// provider's client password, checked by logging in to IMAP and SMTP before
// anything is saved. A known provider needs no server names. An instance may
// watch several mailboxes; each is its own Connector instance, `email-<name>`.

// What to call each provider and its client password, and where to get one.
const TEXT = {
  gmail: () => ({ label: 'Gmail', password: t('应用专用密码', 'App password'),
    where: t('在 https://myaccount.google.com/apppasswords 生成，要先开启两步验证',
      'use an app password, made at https://myaccount.google.com/apppasswords once 2-Step Verification is on') }),
  qq: () => ({ label: t('QQ 邮箱', 'QQ Mail'), password: t('授权码', 'Authorization code'),
    where: t('网页版 QQ 邮箱的设置里开启「IMAP/SMTP 服务」后生成',
      'use an authorization code, generated once IMAP/SMTP is turned on in the QQ Mail web settings') }),
  netease: () => ({ label: t('网易邮箱', 'NetEase Mail'), password: t('授权码', 'Authorization code'),
    where: t('网页版邮箱 → 设置 → POP3/SMTP/IMAP，开启「IMAP/SMTP 服务」后生成',
      'use an authorization code: web mail → Settings → POP3/SMTP/IMAP, turn on IMAP/SMTP, then generate one') }),
};

const PROVIDERS = [
  { kind: 'gmail', domains: ['gmail.com', 'googlemail.com'], imap: 'imap.gmail.com', smtp: 'smtp.gmail.com' },
  { kind: 'qq', domains: ['qq.com'], imap: 'imap.qq.com', smtp: 'smtp.qq.com' },
  ...['163.com', '126.com', 'yeah.net'].map((domain) => ({ kind: 'netease', domains: [domain], imap: `imap.${domain}`, smtp: `smtp.${domain}` })),
];

// The owner gave up on a mailbox: setup's menu goes back to its list, any
// other caller stops as on any cancel.
export class EmailCancelled extends PromptAborted {}

// Mailboxes that take only OAuth, which the connector does not speak.
const OAUTH_ONLY = ['outlook.com', 'hotmail.com', 'live.com', 'msn.com'];

export function emailAccountPath(env, agentId, mailboxId) {
  return path.join(vikaHome(env), 'email', agentId, `${mailboxId}.json`);
}

function slug(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

// A name the model and the logs can read: the provider (email-gmail), then
// the mailbox's own name when the instance already has one there.
export function emailInstanceId(address, taken = []) {
  const [local, domain = ''] = address.split('@');
  const provider = slug(domain.split('.')[0]) || 'mail';
  const candidates = [`email-${provider}`, `email-${provider}-${slug(local)}`.slice(0, 46).replace(/-+$/, '')];
  for (const candidate of candidates) if (candidate.length > 6 && !taken.includes(candidate)) return candidate;
  for (let index = 2; ; index += 1) {
    const candidate = `email-${provider}-${index}`;
    if (!taken.includes(candidate)) return candidate;
  }
}

export function providerFor(address) {
  const domain = address.split('@')[1] ?? '';
  const provider = PROVIDERS.find((candidate) => candidate.domains.includes(domain));
  return provider ? { ...provider, ...TEXT[provider.kind]() } : null;
}

function addressProblem(value, taken) {
  if (!/^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(value)) return t('邮箱地址不对', 'That is not an email address');
  if (OAUTH_ONLY.includes(value.split('@')[1].toLowerCase())) {
    return t('Outlook / Hotmail 只能用 OAuth 登录，暂时接不了，换一个邮箱', 'Outlook / Hotmail only sign in with OAuth, which Vika cannot do yet; use another mailbox');
  }
  if (taken.includes(value.toLowerCase())) return t('这个邮箱已经接入了', 'This mailbox is connected already');
  return null;
}

function portProblem(value) {
  const port = Number(value);
  return Number.isInteger(port) && port > 0 && port < 65_536 ? null : t('端口是 1-65535 的整数', 'A port is a whole number from 1 to 65535');
}

async function servers(prompter, domain, previous) {
  const ask = async (kind, host, port) => ({
    host: await prompter.text(t(`${kind.toUpperCase()} 服务器`, `${kind.toUpperCase()} server`), { initial: host, validate: (value) => (value ? null : t('不能为空', 'Required')) }),
    port: Number(await prompter.text(t(`${kind.toUpperCase()} 端口`, `${kind.toUpperCase()} port`), { initial: String(port), validate: portProblem })),
  });
  return {
    imap: await ask('imap', previous?.imap.host ?? `imap.${domain}`, previous?.imap.port ?? 993),
    smtp: await ask('smtp', previous?.smtp.host ?? `smtp.${domain}`, previous?.smtp.port ?? 465),
  };
}

// Why a login failed, in words that point at the usual cause.
function explain(error, provider, password) {
  const message = String(error?.message ?? error);
  if (/IMAP LOGIN failed|SMTP AUTH/.test(message)) {
    return `${message}\n  ${t(
      `多半是密码不对（这次收到 ${password.length} 个字符）：这里要填${provider?.password ?? '邮箱给第三方客户端用的专用密码'}，不是网页登录密码。`,
      `Most likely the password is wrong (${password.length} characters came through): this takes ${provider ? `the ${provider.password.toLowerCase()}` : 'the password the mailbox gives to other mail apps'}, not your web login password.`,
    )}`;
  }
  return message;
}

// Asks until both logins succeed. Resolves with the account file's content.
// `fixed` is a saved account whose password changes: only the password is asked.
// `taken` lists addresses the instance already watches.
export async function askEmail({ prompter, previous = null, fixed = null, taken = [], verify = verifyAccount }) {
  const out = (text) => prompter.write(text);
  out(fixed ? `\n── ${t('邮箱', 'Email')} ${fixed.address} ──\n` : t(
    '\n── 邮箱 ──\n  只读收件箱（不会标已读、不移动、不删除）；回信先发到主渠道给你确认，你同意了才发出。\n',
    '\n── Email ──\n  Vika only reads the inbox (never marks, moves or deletes); a reply goes to your primary channel and is sent once you OK it.\n',
  ));
  let last = fixed ?? previous;
  for (;;) {
    const address = fixed?.address
      ?? (await prompter.text(t('邮箱地址', 'Email address'), { initial: last?.address ?? '', validate: (value) => addressProblem(value, taken) })).toLowerCase();
    const provider = providerFor(address);
    const known = provider ? { imap: { host: provider.imap, port: 993 }, smtp: { host: provider.smtp, port: 465 } } : null;
    if (provider) out(t(`  ${provider.label}：密码处填${provider.password}，${provider.where}。\n`, `  ${provider.label}: ${provider.where}.\n`));
    else if (!fixed) {
      out(t('  不认识这个邮箱的服务器，需要手动填写；密码处填邮箱给第三方客户端用的专用密码（授权码），没有的话才是登录密码。\n',
        '  Unknown provider: enter its servers. For the password, use the one the mailbox gives to other mail apps, or the login password if it has none.\n'));
    }
    const hosts = fixed ? { imap: fixed.imap, smtp: fixed.smtp }
      : known ?? await servers(prompter, address.split('@')[1], last?.address === address ? last : null);
    // A known provider's client password never has spaces; Gmail shows it in groups of four.
    const typed = await prompter.secret(provider?.password ?? t('密码', 'Password'));
    const password = provider ? typed.replace(/\s+/g, '') : typed;
    const account = { address, password, ...hosts };
    out(t(`  正在登录 ${hosts.imap.host} 和 ${hosts.smtp.host}…\n`, `  Logging in to ${hosts.imap.host} and ${hosts.smtp.host}…\n`));
    try {
      const { idle } = await verify({ ...account, user: address });
      out(t(`  收信和发信都登录成功${idle ? '' : '（这个邮箱不推送新邮件，每分钟查一次）'}\n`,
        `  Receiving and sending both work${idle ? '' : ' (this mailbox does not push new mail, so Vika checks every minute)'}\n`));
      return account;
    } catch (error) {
      out(`  ${t('登录失败：', 'Login failed: ')}${explain(error, provider, password)}\n`);
      last = account;
      if (!await prompter.confirm(t('重新输入？', 'Try again?'))) throw new EmailCancelled();
    }
  }
}

// The instance's mailboxes with their addresses, read from the account files.
export function mailboxesOf(agent) {
  return (agent.emailAccounts ?? []).map((mailbox) => {
    const account = readEmailAccount(mailbox.config);
    return { ...mailbox, account, address: account?.address ?? null };
  });
}

// The account file is the only place the password lives: 0600, written whole.
export function writeEmailAccount(file, account) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(account, null, 2)}\n`, { mode: 0o600 });
  fs.chmodSync(temporary, 0o600);
  fs.renameSync(temporary, file);
}

export function readEmailAccount(file) {
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    return value?.imap && value?.smtp ? value : null;
  } catch {
    return null;
  }
}
