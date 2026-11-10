// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { assertSha256, download } from '../../packages/updater/src/artifacts.mjs';
import { SLACK_CLI_VERSION } from '../../packages/daemon/src/slack/cli.mjs';

// Digests published by slackapi/slack-cli for the pinned v4.10.0 release.
export const SLACK_CLI_ASSETS = Object.freeze({
  'linux-x64': ['linux_amd64', '9772b17f70c4ec522ea6ae25545220dfe17d6afb5babe28fa1cd55a24b2abffa'],
  'linux-arm64': ['linux_arm64', 'a930e089977e1138320efbe3bc49dc18f40d187bbbf4a9c7ff419c443bc52d30'],
  'darwin-x64': ['macOS_amd64', '1ae1db7c4da5e861e24ee4c0f09965829d0f7508a080f87692193678549b5895'],
  'darwin-arm64': ['macOS_arm64', '0f897eee17ce549ba1f34c389c2591c32c191ce94eea15cd4b92053d8f35c80a'],
});
const LICENSE_SHA256 = 'a65d087cf010a52f8736da6b387df1feb7089e2c0636ef21cb86bef05940e0a4';

// Linux archives name bin/slack; macOS archives name ./bin/slack and omit
// LICENSE. Extract only the expected executable from a verified archive.
export function extractSlackCliBinary(archive, directory) {
  const entries = execFileSync('tar', ['-tzf', archive], { encoding: 'utf8' }).split('\n');
  const binary = entries.find(entry => entry === 'bin/slack' || entry === './bin/slack');
  if (!binary) throw new Error('Slack CLI archive has no bin/slack executable');
  execFileSync('tar', ['-xzf', archive, '-C', directory, binary]);
}

export async function installSlackCliBinary({ root, platformId, fetchImpl = globalThis.fetch }) {
  const asset = SLACK_CLI_ASSETS[platformId];
  if (!asset) throw new Error(`Unsupported Slack CLI platform: ${platformId}`);
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-slack-install-'));
  try {
    const name = `slack_cli_${SLACK_CLI_VERSION}_${asset[0]}.tar.gz`;
    const archive = path.join(work, name);
    await download(`https://github.com/slackapi/slack-cli/releases/download/v${SLACK_CLI_VERSION}/${name}`, archive, { fetchImpl });
    assertSha256(archive, asset[1]);
    extractSlackCliBinary(archive, work);
    const license = path.join(work, 'LICENSE');
    await download(`https://raw.githubusercontent.com/slackapi/slack-cli/v${SLACK_CLI_VERSION}/LICENSE`, license, { fetchImpl });
    assertSha256(license, LICENSE_SHA256);
    const bin = path.join(root, 'bin', 'slack');
    fs.mkdirSync(path.dirname(bin), { recursive: true });
    fs.copyFileSync(path.join(work, 'bin', 'slack'), bin);
    fs.chmodSync(bin, 0o755);
    fs.copyFileSync(license, path.join(root, 'bin', 'slack.LICENSE'));
    return { bin, version: SLACK_CLI_VERSION };
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
}
