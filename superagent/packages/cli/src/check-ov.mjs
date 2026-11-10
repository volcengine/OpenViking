// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { pathToFileURL } from 'node:url';
import os from 'node:os';
import { readManifest } from './manifest.mjs';
import { detectOpenViking } from './ov.mjs';
import { languageChoice, setLanguage, t } from './i18n.mjs';

// The installer's check before it activates a release, asking nothing:
// every instance must reach OV with its own config. Without instances the
// default connection must work, unless setup runs next and asks for one.
export async function checkInstallationOV({ env = process.env, detect = detectOpenViking } = {}) {
  const { value } = readManifest(env);
  const instances = value?.agents ?? [{ id: '', ovConfig: undefined }];
  for (const agent of instances) {
    const result = await detect({ env, homeDir: env.HOME || os.homedir(), configPath: agent.ovConfig });
    if (!result?.usable) {
      throw new Error(t(
        `需要可用的 OpenViking${agent.id ? `（${agent.id}）` : ''}：${result?.reason ?? '未检测到服务'}。请配置并启动 OpenViking 后重试。`,
        `OpenViking is required${agent.id ? ` (${agent.id})` : ''}: ${result?.reason ?? 'no service found'}. Configure and start OpenViking, then retry.`,
      ));
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  setLanguage(languageChoice().language);
  try {
    await checkInstallationOV();
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
