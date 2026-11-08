#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import { isLanguage, languageChoice, saveLanguage, setLanguage, t } from '../src/i18n.mjs';
import { PALETTE, padEnd, styler, width } from '../src/ui.mjs';

// The `vika` command. Each subcommand loads only what it needs, so `setup`
// and `help` work before any manifest exists. --lang zh|en anywhere on the
// line (but in `vika pi`, whose arguments are Pi's) picks the language and
// keeps it for later commands; alone, it only does that.

// [command, arguments, what it does], in groups.
const commands = () => {
  const target = t('[实例|web|updater]', '[instance|web|updater]');
  return [
    [t('配置', 'Set up'), [
      ['setup', '', t('添加、修改或删除实例：接入消息渠道、设主渠道等',
        'Add, change or remove an instance: connect messaging channels, pick the primary channel')],
      ['login', '<lark|weixin|email|telegram|slack|qq>', t('登录过期或改了密码后重新授权；后面可加实例名',
        'Authorize again after a login expired or a password changed; an instance name may follow')],
    ]],
    [t('运行', 'Run'), [
      ['start', target, t('启动本机已安装的版本；不写实例时启动全部', 'Start the installed release; everything when no instance is named')],
      ['stop', target, t('停止', 'Stop')],
      ['restart', target, t('重启', 'Restart')],
      ['status', '', t('查看各进程状态和接入的 IM', 'Show each process and its IMs')],
      ['logs', t('[实例]', '[instance]'), t('查看日志', 'Show the logs')],
    ]],
    [t('维护', 'Maintain'), [
      ['upgrade', t('[实例|all]', '[instance|all]'), t('升级到最新版本；不写时升级全部', 'Upgrade to the latest release; everything when no instance is named')],
      ['autostart', '<enable|disable>', t('开机自动启动（不需要 sudo）', 'Start at login (no sudo needed)')],
      ['pi', t('[参数...]', '[arguments...]'), t('打开内置的 Pi（例如用 /login 登录模型）', 'Open the built-in Pi (e.g. /login to a model)')],
    ]],
    [t('选项', 'Options'), [
      ['--lang', '<zh|en>', t('切换界面语言，之后的命令都用它', 'Switch the interface language, for later commands too')],
    ]],
  ];
};

async function usage(stream) {
  const groups = commands();
  const commandWidth = Math.max(...groups.flatMap(([, rows]) => rows).map(([name, args]) => width(`${name} ${args}`.trim()))) + 2;
  const s = styler(stream);
  if (!s.enabled) {
    const rows = groups.flatMap(([, entries]) => entries).map(([name, args, text]) => `  ${padEnd(`${name} ${args}`.trim(), commandWidth)}${text}`);
    stream.write(`${t('用法：vika <命令>', 'Usage: vika <command>')}\n\n${rows.join('\n')}\n`);
    return;
  }
  const { releaseInfo } = await import('../src/version.mjs');
  const tagline = t('把本机的 coding agent 带上船', 'Bring your coding agent aboard');
  const lines = [`\n  ${s.bold(s.gradient('VIKA', PALETTE.sail))}  ${s.gray(`${releaseInfo().version} · ${tagline}`)}\n`,
    `  ${t('用法', 'Usage')}  ${s.accent('vika')} ${s.gray(t('<命令>', '<command>'))}`];
  for (const [group, rows] of groups) {
    lines.push('', `  ${s.violet(group)}`);
    for (const [name, args, text] of rows) {
      lines.push(`    ${padEnd(`${s.accent(name)} ${s.gray(args)}`, commandWidth)}${text}`);
    }
  }
  stream.write(`${lines.join('\n')}\n`);
}

// The instances on this machine, so their names need not be remembered.
async function listInstances() {
  try {
    const { describeInstances, loadInstances } = await import('../src/instances.mjs');
    const deployment = loadInstances();
    process.stdout.write(deployment
      ? `\n${t('本机的实例（实例名  机器人名字  接入的 IM）：', 'Instances here (name  bot name  IMs):')}\n${describeInstances(deployment)}\n`
      : t('\n还没有实例：运行 vika setup 添加一个。\n', '\nNo instance yet: run vika setup to add one.\n'));
  } catch {}
}

// Commands that act on instances need the manifest that lists them.
async function needInstances() {
  const { manifestPath } = await import('../src/manifest.mjs');
  if (!fs.existsSync(manifestPath())) throw new Error(t('还没有任何实例：先运行 vika setup', 'No instance yet: run vika setup first'));
}

async function main([command, ...args], lang) {
  switch (command) {
    case 'setup': {
      const [{ setupCommand }, { Prompter }] = await Promise.all([import('../src/setup.mjs'), import('../src/prompt.mjs')]);
      const prompter = new Prompter();
      try {
        return await setupCommand({ prompter, lang });
      } finally {
        prompter.close();
      }
    }
    case 'start':
    case 'stop':
    case 'restart': {
      if (args.length > 1) break;
      await needInstances();
      const control = await import('../src/control.mjs');
      return control[`${command}Command`](args[0]);
    }
    case 'status': {
      if (args.length) break;
      await needInstances();
      const { statusCommand } = await import('../src/control.mjs');
      return statusCommand();
    }
    case 'logs': {
      if (args.length > 1) break;
      const [{ logsCommand }, { chooseInstance, loadInstances }, { Prompter }] = await Promise.all([
        import('../src/control.mjs'), import('../src/instances.mjs'), import('../src/prompt.mjs')]);
      const prompter = new Prompter();
      const agent = await chooseInstance({ deployment: loadInstances(), requested: args[0], prompter, question: t('看哪个实例的日志', 'Whose logs') })
        .finally(() => prompter.close());
      return logsCommand(agent.id);
    }
    case 'login': {
      const [{ loginCommand }, { Prompter }] = await Promise.all([import('../src/login.mjs'), import('../src/prompt.mjs')]);
      const prompter = new Prompter();
      try {
        return await loginCommand(args, { prompter });
      } finally {
        prompter.close();
      }
    }
    case 'upgrade': {
      const { upgradeCommand } = await import('../src/upgrade.mjs');
      return upgradeCommand(args.length ? args : ['all']);
    }
    case 'autostart': {
      const { autostartCommand } = await import('../src/autostart.mjs');
      return autostartCommand(args);
    }
    case 'sail': {
      // The boat from setup, until a key is pressed.
      const [{ playBanner }, { releaseInfo }] = await Promise.all([import('../src/banner.mjs'), import('../src/version.mjs')]);
      await playBanner({ loop: true, version: releaseInfo().version });
      return 0;
    }
    case 'pi': {
      const { managedPiBin } = await import('../../daemon/src/pi-runtime.mjs');
      const child = spawn(process.execPath, [managedPiBin(), ...args], { stdio: 'inherit' });
      return new Promise((resolve) => child.once('exit', (code) => resolve(code ?? 0)));
    }
    case '--version':
    case 'version': {
      const { releaseInfo } = await import('../src/version.mjs');
      const release = releaseInfo();
      process.stdout.write(`vika ${release.version}${release.platform ? ` (${release.platform})` : ''}\n`);
      return 0;
    }
    case undefined:
    case 'help':
    case '--help':
    case '-h':
      await usage(process.stdout);
      await listInstances();
      return 0;
    default:
      process.stderr.write(t(`vika：没有「${command}」这个命令\n\n`, `vika: there is no command "${command}"\n\n`));
      break;
  }
  await usage(process.stderr);
  return 2;
}

// --lang zh|en wherever it is, and the rest of the line.
function takeLanguage(argv) {
  if (argv[0] === 'pi') return { args: argv, lang: null };
  const args = [];
  let lang = null;
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--lang') lang = argv[++index] ?? '';
    else if (argv[index].startsWith('--lang=')) lang = argv[index].slice('--lang='.length);
    else args.push(argv[index]);
  }
  return { args, lang };
}

try {
  const { args, lang } = takeLanguage(process.argv.slice(2));
  setLanguage(languageChoice({ flag: lang }).language);
  if (lang !== null && !isLanguage(lang)) {
    process.stderr.write(t('--lang 只能是 zh 或 en\n', '--lang takes zh or en\n'));
    process.exitCode = 2;
  } else if (lang !== null && args.length === 0) {
    saveLanguage(lang);
    process.stdout.write(t('界面语言已切换为中文\n', 'The interface language is now English\n'));
    process.exitCode = 0;
  } else {
    if (lang !== null) saveLanguage(lang);
    process.exitCode = await main(args, lang);
  }
} catch (error) {
  process.stderr.write(`${error.name === 'PromptAborted' ? t('已取消', 'Cancelled') : error.message}\n`);
  process.exitCode = 1;
}
