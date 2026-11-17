// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { superagentHome } from './manifest.mjs';
import { which } from './runtimes.mjs';
import { t } from './i18n.mjs';

// Only paths and an opt-out of system Git config cross the process boundary.
// In particular, GH_TOKEN/GITHUB_TOKEN never go into the deployment manifest.
export function githubConfig(env = process.env) {
  const root = path.join(superagentHome(env), 'github');
  return {
    root,
    ghDir: path.join(root, 'gh'),
    gitConfig: path.join(root, 'gitconfig'),
    env: {
      GH_CONFIG_DIR: path.join(root, 'gh'),
      GIT_CONFIG_GLOBAL: path.join(root, 'gitconfig'),
      GIT_CONFIG_NOSYSTEM: '1',
    },
  };
}

function isolatedEnvironment(env, config) {
  const isolated = { ...env, ...config.env };
  // Environment tokens take precedence over gh's config file. Do not let the
  // operator's own account silently become OVSA's account.
  for (const key of ['GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN',
    'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'GIT_ASKPASS']) delete isolated[key];
  return isolated;
}

function run(command, args, options) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, options);
    child.once('error', reject);
    child.once('exit', (code) => code === 0 ? resolve() : reject(new Error(`gh exited with code ${code}`)));
  });
}

// A private global config makes HTTPS Git operations use the same isolated gh
// account. GIT_CONFIG_NOSYSTEM keeps a host/system credential helper out too.
export function writeGitIsolation(config) {
  fs.mkdirSync(config.root, { recursive: true, mode: 0o700 });
  fs.mkdirSync(config.ghDir, { recursive: true, mode: 0o700 });
  fs.writeFileSync(config.gitConfig, `[credential]\n\thelper =\n[credential "https://github.com"]\n\thelper = !gh auth git-credential\n`, { mode: 0o600 });
  if (process.platform !== 'win32') {
    fs.chmodSync(config.root, 0o700);
    fs.chmodSync(config.ghDir, 0o700);
    fs.chmodSync(config.gitConfig, 0o600);
  }
}

async function status(gh, env, runImpl) {
  await runImpl(gh, ['auth', 'status', '--hostname', 'github.com'], { env, stdio: 'ignore' });
}

export async function githubAccount(gh, env, execFileImpl) {
  const { stdout } = await execFileImpl(gh, ['api', 'user', '--jq', '.login'], { env, timeout: 20_000 });
  return String(stdout).trim();
}

// Interactive setup offers GitHub once per run. Missing gh, headless setup, a
// declined login and a failed/cancelled device flow all leave the rest of setup
// usable. An already authenticated isolated account needs no browser again.
export async function prepareGitHub({
  prompter,
  env = process.env,
  runImpl = run,
  execFileImpl = null,
  whichImpl = which,
} = {}) {
  const config = githubConfig(env);
  const gh = whichImpl('gh', env);
  if (!gh) {
    if (prompter.tty) prompter.warn(t('没有找到 gh，跳过 GitHub 登录。安装 GitHub CLI 后重新运行 setup 即可配置；其他设置不受影响。',
      'GitHub CLI (gh) was not found, so GitHub sign-in was skipped. Install gh and run setup again to configure it; the rest of setup is unaffected.'));
    return null;
  }
  const isolatedEnv = isolatedEnvironment(env, config);
  try {
    await status(gh, isolatedEnv, runImpl);
    writeGitIsolation(config);
    let account = '';
    if (execFileImpl) {
      try { account = await githubAccount(gh, isolatedEnv, execFileImpl); } catch {}
    }
    prompter.info(t(`OVSA 使用独立的 GitHub 账号${account ? ` ${account}` : ''}，凭据只保存在 ${config.ghDir}`,
      `OVSA uses its isolated GitHub account${account ? ` ${account}` : ''}; credentials are stored only in ${config.ghDir}`));
    return config.env;
  } catch {}
  if (!prompter.tty) return null;
  prompter.info(t(`可以给 OVSA 配一个独立的 GitHub 账号。登录用 GitHub 设备授权；凭据只写入 ${config.ghDir}，不会读取或改动本机的 gh、git 配置。`,
    `You can give OVSA its own GitHub account. Sign in with GitHub device authorization; credentials go only to ${config.ghDir}, without reading or changing this machine's gh or git configuration.`));
  if (!await prompter.confirm(t('现在登录 OVSA 的 GitHub 账号？', 'Sign in to OVSA’s GitHub account now?'), true)) return null;
  // Create the private directory before gh writes hosts.yml. --web uses the
  // device flow and prints a one-time code when no browser can be opened.
  writeGitIsolation(config);
  try {
    await runImpl(gh, ['auth', 'login', '--hostname', 'github.com', '--git-protocol', 'https', '--web', '--skip-ssh-key'], {
      env: isolatedEnv,
      stdio: 'inherit',
    });
    await status(gh, isolatedEnv, runImpl);
    writeGitIsolation(config);
    prompter.success(t('GitHub 登录完成，仅对 OVSA 生效。', 'GitHub sign-in is complete and applies only to OVSA.'));
    return config.env;
  } catch (error) {
    prompter.warn(t(`GitHub 登录未完成：${error.message}。跳过它，其他设置继续；之后可重新运行 setup。`,
      `GitHub sign-in did not finish: ${error.message}. Skipping it and continuing setup; run setup again later.`));
    return null;
  }
}
