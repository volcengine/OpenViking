// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { verifyAccount } from '../../daemon/src/email/client.mjs';
import { OAuthRequiredError } from '../../daemon/src/email/imap.mjs';
import { requestDeviceCode, waitForDeviceCode, writePrivateJson } from '../../daemon/src/email/oauth2.mjs';
import { t } from './i18n.mjs';
import { vikaHome } from './manifest.mjs';
import { PromptAborted } from './prompt.mjs';

// The mailbox steps of `vika setup` and `vika login email`: an address and the
// provider's credential — a client password, or a browser sign-in for the
// mailboxes that no longer take passwords (Microsoft 365, Outlook.com) —
// checked by logging in to IMAP and SMTP before anything is saved. A known
// provider needs no server names. An instance may watch several mailboxes;
// each is its own Connector instance, `email-<name>`.

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
  microsoft: () => ({ label: t('Microsoft 365 / Outlook', 'Microsoft 365 / Outlook') }),
};

const PROVIDERS = [
  { kind: 'gmail', domains: ['gmail.com', 'googlemail.com'], imap: 'imap.gmail.com', smtp: 'smtp.gmail.com' },
  { kind: 'qq', domains: ['qq.com'], imap: 'imap.qq.com', smtp: 'smtp.qq.com' },
  ...['163.com', '126.com', 'yeah.net'].map((domain) => ({ kind: 'netease', domains: [domain], imap: `imap.${domain}`, smtp: `smtp.${domain}` })),
  { kind: 'microsoft', oauth: true, domains: ['outlook.com', 'hotmail.com', 'live.com', 'msn.com'],
    imap: 'outlook.office365.com', smtp: 'smtp-mail.outlook.com', smtpPort: 587 },
];

// The owner gave up on a mailbox: setup's menu goes back to its list, any
// other caller stops as on any cancel.
export class EmailCancelled extends PromptAborted {}

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

// The device-code sign-in the mailbox Connector speaks; tests pass their own.
const deviceLogin = {
  start: (oauth2) => requestDeviceCode(oauth2),
  wait: (oauth2, code) => waitForDeviceCode(oauth2, code),
};

// A Microsoft mailbox signs in in the browser once; what the Connector keeps
// afterwards is the refresh token from that sign-in. The account it returns is
// already checked against IMAP and SMTP with the access token just obtained.
// `previous` is a saved `oauth2` block, whose app and tenant are reused on a
// re-authorization so only the browser step is asked again.
async function microsoftMailbox({ prompter, address, hosts, previous, verify, login = deviceLogin }) {
  const out = (text) => prompter.write(text);
  const clientId = previous?.clientId ?? (await prompter.text(
    t('Microsoft 应用（客户端）ID', 'Microsoft app (client) ID'),
    { validate: (value) => (/^[A-Za-z0-9._-]+$/.test(value) ? null : t('这个 ID 不对', 'That is not an app id')) },
  )).trim();
  const tenant = previous?.tenant ?? ((await prompter.text(
    t('租户 ID 或域名（直接回车用 common）', 'Tenant id or domain (Enter for common)'),
    { initial: 'common' },
  )).trim() || 'common');
  const oauth2 = { tenant, clientId };
  const code = await login.start(oauth2);
  out(t(
    `\n  打开 ${code.verificationUri}，输入代码 ${code.userCode}，用这个邮箱的 Microsoft 账号登录。\n  授权完成后会自动继续。\n\n`,
    `\n  Open ${code.verificationUri}, enter the code ${code.userCode}, then sign in with this mailbox's Microsoft account.\n  Vika continues by itself once that is done.\n\n`,
  ));
  const tokens = await login.wait(oauth2, code);
  out(t(`  正在登录 ${hosts.imap.host} 和 ${hosts.smtp.host}…\n`, `  Logging in to ${hosts.imap.host} and ${hosts.smtp.host}…\n`));
  const account = { address, imap: hosts.imap, smtp: hosts.smtp,
    oauth2: { ...oauth2, refreshToken: tokens.refreshToken } };
  const { idle } = await verify({ ...account, user: address }, { credential: { accessToken: tokens.accessToken } });
  out(t(`  收信和发信都登录成功${idle ? '' : '（这个邮箱不推送新邮件，每分钟查一次）'}\n`,
    `  Receiving and sending both work${idle ? '' : ' (this mailbox does not push new mail, so Vika checks every minute)'}\n`));
  return account;
}

// Asks until both logins succeed. Resolves with the account file's content.
// `fixed` is a saved account whose credential is renewed: only that credential
// is asked for. `taken` lists addresses the instance already watches.
export async function askEmail({ prompter, previous = null, fixed = null, taken = [], verify = verifyAccount, login = deviceLogin }) {
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
    const known = provider
      ? { imap: { host: provider.imap, port: 993 }, smtp: { host: provider.smtp, port: provider.smtpPort ?? 465 } }
      : null;
    if (provider?.oauth) {
      out(t('  Microsoft 365 / Outlook：密码登录已关闭，下面用浏览器授权一次。\n',
        '  Microsoft 365 / Outlook: password sign-in is off; authorize once in the browser below.\n'));
    } else if (provider) {
      out(t(`  ${provider.label}：密码处填${provider.password}，${provider.where}。\n`, `  ${provider.label}: ${provider.where}.\n`));
    } else if (!fixed) {
      out(t('  不认识这个邮箱的服务器，需要手动填写；密码处填邮箱给第三方客户端用的专用密码（授权码），没有的话才是登录密码。\n',
        '  Unknown provider: enter its servers. For the password, use the one the mailbox gives to other mail apps, or the login password if it has none.\n'));
    }
    // A known provider brings its own servers and sign-in. Anything else is
    // the owner's own mail system: a Microsoft 365 mailbox there has no server
    // names to guess, so they say so, and the answer decides the sign-in. A
    // server that turns out to refuse passwords still switches (below).
    const microsoft = Boolean(provider?.oauth || fixed?.oauth2)
      || (!provider && !fixed && await prompter.confirm(t(
        '这个邮箱是 Microsoft 365 / Outlook 吗（浏览器登录，不需要密码）？',
        'Is this a Microsoft 365 / Outlook mailbox (browser sign-in, no password)?',
      ), false));
    const oauth = microsoft;
    const hosts = fixed ? { imap: fixed.imap, smtp: fixed.smtp }
      : oauth ? (known ?? { imap: { host: 'outlook.office365.com', port: 993 }, smtp: { host: 'smtp.office365.com', port: 587 } })
        : known ?? await servers(prompter, address.split('@')[1], last?.address === address ? last : null);
    let password = '';
    let usedOauth = oauth;
    try {
      if (oauth) return await microsoftMailbox({ prompter, address, hosts, previous: fixed?.oauth2 ?? null, verify, login });
      const typed = await prompter.secret(provider?.password ?? t('密码', 'Password'));
      // A known provider's client password never has spaces; Gmail shows it in groups of four.
      password = provider ? typed.replace(/\s+/g, '') : typed;
      const account = { address, password, ...hosts };
      out(t(`  正在登录 ${hosts.imap.host} 和 ${hosts.smtp.host}…\n`, `  Logging in to ${hosts.imap.host} and ${hosts.smtp.host}…\n`));
      try {
        const { idle } = await verify({ ...account, user: address });
        out(t(`  收信和发信都登录成功${idle ? '' : '（这个邮箱不推送新邮件，每分钟查一次）'}\n`,
          `  Receiving and sending both work${idle ? '' : ' (this mailbox does not push new mail, so Vika checks every minute)'}\n`));
        return account;
      } catch (error) {
        if (!(error instanceof OAuthRequiredError)) throw error;
        // The server said, before any password was taken, that it signs in only
        // through Microsoft; the servers just typed are the ones that said it.
        out(t('  这个邮箱不接受密码登录，改用 Microsoft 账号授权。\n',
          '  This mailbox does not take passwords; signing in with a Microsoft account instead.\n'));
        usedOauth = true;
        return await microsoftMailbox({ prompter, address, hosts, previous: null, verify, login });
      }
    } catch (error) {
      out(`  ${t('登录失败：', 'Login failed: ')}${usedOauth ? error.message : explain(error, provider, password)}\n`);
      last = { address, ...hosts };
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

// The account file is the only place the credential lives: 0600, written whole.
export function writeEmailAccount(file, account) {
  writePrivateJson(file, account);
}

export function readEmailAccount(file) {
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    return value?.imap && value?.smtp ? value : null;
  } catch {
    return null;
  }
}
