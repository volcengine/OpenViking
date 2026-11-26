// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { randomBytes } from 'node:crypto';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { updateManifest } from '../../daemon/src/deployment-manifest.mjs';
import { manifestPath } from './manifest.mjs';
import { t } from './i18n.mjs';

// `ov-superagent web auth`: saves the Web's API key to secrets.env and its bind
// host to config.json (`web.host`), then restarts the Web.

const require = createRequire(import.meta.url);
const { loadDeploymentManifest, loadSecretsFile, parseWebSettings, resolveSecretsFilePath } = require('../../../deployment-config.cjs');

const KEY_NAME = 'OV_SUPERAGENT_WEB_API_KEY';
const USAGE = 'ov-superagent web auth [--key <key>] [--host <address>] | --show';

function writeWebApiKey(env, key) {
  const file = resolveSecretsFilePath(env);
  let text = '';
  try { text = fs.readFileSync(file, 'utf8'); } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const lines = text.split(/\r?\n/).filter((line) => line.trim() && !line.trim().startsWith(`${KEY_NAME}=`));
  lines.push(`${KEY_NAME}=${key}`);
  fs.writeFileSync(file, `${lines.join('\n')}\n`, { mode: 0o600 });
  fs.chmodSync(file, 0o600);
  return file;
}

export async function webAuthCommand(args, { env = process.env, stdout = process.stdout, restart } = {}) {
  const options = {};
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === '--show') options.show = true;
    else if ((arg === '--key' || arg === '--host') && args[index + 1] !== undefined) options[arg.slice(2)] = args[++index];
    else throw new Error(t(`用法：${USAGE}`, `Usage: ${USAGE}`));
  }

  const deployment = loadDeploymentManifest(env);
  if (options.show) {
    const key = loadSecretsFile(env, [KEY_NAME])[KEY_NAME];
    stdout.write(`host: ${deployment.web.host}\n${KEY_NAME}: ${key ?? t('（未设置）', '(not set)')}\n`);
    return 0;
  }

  const key = options.key ?? randomBytes(32).toString('base64url');
  if (key.length < 16 || !/^[\x21-\x7e]+$/.test(key)) {
    throw new Error(t('API key 至少 16 个可打印字符，不能有空格', 'The API key must be at least 16 printable characters without spaces'));
  }
  const { host } = parseWebSettings({ host: options.host ?? deployment.web.host }, []);

  // The key first: a non-loopback host without a key would stop the Web from starting.
  const secretsFile = writeWebApiKey(env, key);
  updateManifest(manifestPath(env), (value) => { value.web = { host }; });
  stdout.write(t(`已保存到 ${secretsFile}\n`, `Saved to ${secretsFile}\n`));
  stdout.write(`${KEY_NAME}=${key}\nhost: ${host}\n`);
  return (restart ?? (async () => (await import('./control.mjs')).restartCommand('web', { env, stdout })))();
}
