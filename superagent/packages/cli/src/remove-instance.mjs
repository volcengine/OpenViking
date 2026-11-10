// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { removeManagedApps, restartCommand } from './control.mjs';
import { readManifest, validateManifest, vikaCommand, vikaHome, writeManifest } from './manifest.mjs';
import { t } from './i18n.mjs';

// No data or credentials are deleted. The last instance returns the machine
// to first setup by backing up and removing its manifest, rather than writing
// an empty deployment that the daemon, Web and updater cannot load.
export async function removeInstance({ prompter, manifest, configPath, env, control = {} }) {
  const agent = await prompter.select(t('删除哪个实例', 'Which instance to remove'), manifest.agents.map((agent) => ({
    label: `${agent.displayName} (${agent.id})`, value: agent,
  })));
  const next = structuredClone(manifest);
  next.agents = next.agents.filter(({ id }) => id !== agent.id);
  if (next.agents.length && next.defaultInstance === agent.id) {
    next.defaultInstance = next.agents.length === 1 ? next.agents[0].id
      : await prompter.select(t('新的默认实例', 'New default instance'), next.agents.map(({ id, displayName }) => ({
        label: `${displayName} (${id})`, value: id,
      })));
  }
  const last = next.agents.length === 0;
  prompter.info(t(`将删除 ${agent.displayName}（${agent.id}）的实例配置并停止其后台进程。`,
    `Remove the configuration for ${agent.displayName} (${agent.id}) and stop its background process.`));
  prompter.info(t(`保留工作目录 ${agent.workspace}、数据目录 ${agent.dataDir}、登录凭据和 OpenViking 记忆；各平台的机器人和账号不会删除。`,
    `Keep workspace ${agent.workspace}, data ${agent.dataDir}, login credentials and OpenViking memories; bots and accounts on connected platforms are kept.`));
  prompter.info(last
    ? t('这是最后一个实例，也会停止控制台和自动升级服务；之后可以运行 vika setup 新建实例。',
      'This is the last instance; the console and auto-upgrade service will also stop. Run vika setup afterwards to add an instance.')
    : t(`删除后默认实例：${next.defaultInstance}。`, `Default instance after removal: ${next.defaultInstance}.`));
  if (!await prompter.confirm(t('确认删除这个实例？', 'Remove this instance?'), false)) {
    prompter.info(t('已取消，没有删除实例。', 'Cancelled; no instance was removed.'));
    return 0;
  }
  prompter.navigable = false;
  const releaseLink = env.VIKA_UPDATER_RELEASE_LINK || path.join(vikaHome(env), 'current');
  const lock = path.join(path.dirname(releaseLink), 'deploy.lock');
  fs.mkdirSync(path.dirname(lock), { recursive: true, mode: 0o700 });
  try {
    fs.mkdirSync(lock, { mode: 0o700 });
  } catch (error) {
    if (error.code === 'EEXIST') throw new Error(t('正在部署、升级或删除实例，完成后再试。', 'A deployment, upgrade or instance removal is running; try again when it finishes.'));
    throw error;
  }
  let saved = false;
  const vika = vikaCommand(env);
  const unchanged = () => {
    if (JSON.stringify(readManifest(env).value) !== JSON.stringify(manifest)) {
      throw new Error(t('实例配置已被其他操作修改，请重新运行 setup。', 'The instance configuration changed elsewhere; run setup again.'));
    }
  };
  try {
    unchanged();
    if (!last) validateManifest(configPath, next);
    const options = { env, releaseLink, stdout: { write() {} } };
    await (control.remove ?? removeManagedApps)(last ? ['updater', agent.id, 'web'] : [agent.id], {
      ...options, checkTargets: last ? [] : ['web'],
    });
    unchanged();
    if (last) {
      fs.copyFileSync(configPath, `${configPath}.bak`);
      fs.unlinkSync(configPath);
    } else {
      writeManifest(configPath, next, { expected: manifest });
    }
    saved = true;
    prompter.success(t(`实例 ${agent.id} 已删除，原配置备份在 ${configPath}.bak；数据和凭据已保留。`,
      `Instance ${agent.id} removed; previous configuration backed up at ${configPath}.bak. Data and credentials are kept.`));
    if (!last) await (control.restart ?? restartCommand)('web', options);
    return 0;
  } catch (error) {
    prompter.info(saved
      ? t(`实例已删除，但控制台更新失败：${error.message}\n修好后运行 ${vika} restart web。`,
        `The instance was removed, but the console update failed: ${error.message}\nOnce fixed, run ${vika} restart web.`)
      : t(`删除未完成，实例配置保留；部分进程可能已停止：${error.message}\n修好后重新运行 ${vika} setup 删除，或运行 ${vika} start 恢复。`,
        `Removal did not finish; the instance configuration is kept, but some processes may have stopped: ${error.message}\nOnce fixed, retry removal with ${vika} setup, or run ${vika} start to restore them.`));
    return 1;
  } finally {
    fs.rmdirSync(lock);
  }
}
