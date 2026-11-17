// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and the OpenViking contributors.
// SPDX-License-Identifier: AGPL-3.0
import fs from 'node:fs';
import path from 'node:path';
import { pm2Defaults } from './control.mjs';
import { defaultReleaseLink } from '../../updater/src/targets.mjs';

const quote = value => `'${String(value).replaceAll("'", "'\\''")}'`;
export function serviceDirectory(env) {
  const root = env.SVDIR || (env.PREFIX && path.join(env.PREFIX, 'var', 'service'));
  if (!root || !path.isAbsolute(root)) throw new Error('Termux requires absolute PREFIX or SVDIR.');
  return path.join(root, 'ov-superagent');
}

function releaseLink(homeDir, env) {
  const release = env.OV_SUPERAGENT_UPDATER_RELEASE_LINK || defaultReleaseLink(homeDir);
  if (!path.isAbsolute(release)) throw new Error('The release link must be absolute.');
  return release;
}

export function runitScript({ homeDir, env, node = process.execPath }) {
  if (!path.isAbsolute(env.PREFIX || '')) throw new Error('Termux requires an absolute PREFIX.');
  const release = releaseLink(homeDir, env);
  const values = { HOME: homeDir, PREFIX: env.PREFIX,
    PATH: env.PATH || `${env.PREFIX}/bin`, TMPDIR: env.TMPDIR || `${env.PREFIX}/tmp`,
    PM2_HOME: pm2Defaults({ ...env, HOME: homeDir }).pm2Env.PM2_HOME };
  if (!path.isAbsolute(values.PM2_HOME)) throw new Error('PM2_HOME must be absolute for runit.');
  for (const key of ['LD_PRELOAD', 'LD_LIBRARY_PATH', 'LARK_CLI_BIN', 'SLACK_CLI_BIN',
    'OV_SUPERAGENT_CONFIG', 'OV_SUPERAGENT_SECRETS_FILE']) {
    if (env[key]) values[key] = env[key];
  }
  // runit must supervise a live PM2 process, not a short-lived CLI command.
  return `#!${env.PREFIX}/bin/sh\n` +
    Object.entries(values).map(([key, value]) => `export ${key}=${quote(value)}\n`).join('') +
    `cd ${quote(release)} || exit 1\n` +
    // Transfer the CLI's PM2 home to foreground supervision, including after CLI stop.
    `if [ -f "$PM2_HOME/pm2.pid" ]; then\n` +
    `  ${quote(node)} ${quote(path.join(release, 'node_modules/pm2/bin/pm2'))} kill || exit 1\nfi\n` +
    `exec ${quote(node)} ${quote(path.join(release, 'node_modules/pm2/bin/pm2-runtime'))}` +
    ` start ${quote(path.join(release, 'ecosystem.config.cjs'))} --no-auto-exit\n`;
}

export async function termuxAutostart(action, { homeDir, env, execFileImpl, output }) {
  const directory = serviceDirectory(env);
  const run = path.join(directory, 'run');
  const supervised = fs.existsSync(path.join(directory, 'supervise', 'ok'));
  if (action === 'disable') {
    if (fs.existsSync(run)) {
      // Persist disabled state even if termux-services is not running.
      fs.writeFileSync(path.join(directory, 'down'), '');
      if (supervised) await execFileImpl('sv', ['-w', '45', 'down', directory], { env });
    }
    output.write('Termux runit autostart is off.\n');
    return 0;
  }
  const release = releaseLink(homeDir, env);
  fs.accessSync(path.join(release, 'ecosystem.config.cjs'), fs.constants.R_OK);
  const script = runitScript({ homeDir, env });
  // Write a disabled service first; runsv may discover it during setup.
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(directory, 'down'), '');
  fs.writeFileSync(run, script, { mode: 0o700 });
  fs.chmodSync(run, 0o700);
  fs.rmSync(path.join(directory, 'down'));
  if (supervised) await execFileImpl('sv', ['up', directory], { env });
  output.write(`Termux runit service enabled at ${directory}.\nStart termux-services (restart your Termux shell if needed).\nDevice boot additionally requires Termux:Boot; Android battery restrictions still apply.\n`);
  return 0;
}
