// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { capabilities } from '../../daemon/src/capabilities.mjs';

import http from 'node:http';
import { assertUpgradeTarget, defaultSocketPath, upgradeTargetNames } from '../../updater/src/targets.mjs';

// `ov-superagent upgrade` only asks the updater process; the updater fetches, publishes
// and rolls the apps, so the upgrade survives the caller disconnecting.

function requestUpgrade(socketPath, target) {
  const body = JSON.stringify({ target });
  return new Promise((resolve, reject) => {
    const request = http.request({
      socketPath,
      path: '/v1/upgrade',
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'content-length': Buffer.byteLength(body),
      },
    }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => {
        let payload;
        try {
          payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        } catch {
          reject(new Error(`updater returned invalid JSON (${response.statusCode})`));
          return;
        }
        if (response.statusCode !== 200) {
          reject(new Error(payload.error || `updater returned ${response.statusCode}`));
          return;
        }
        resolve(payload);
      });
    });
    request.setTimeout(600_000, () => request.destroy(new Error('updater request timed out')));
    request.once('error', reject);
    request.end(body);
  });
}

export async function upgradeCommand(args, { stdout = process.stdout, stderr = process.stderr, env = process.env } = {}) {
  if (!capabilities.updater) {
    stderr.write('Windows source installations do not support automatic release upgrades. Stop OpenViking SuperAgent and install a new checkout manually.\n');
    return 1;
  }
  const [target, ...extra] = args;
  if (!target || extra.length > 0) {
    stderr.write(`usage: ov-superagent upgrade <${upgradeTargetNames().join('|')}>\n`);
    return 2;
  }
  assertUpgradeTarget(target);
  const result = await requestUpgrade(env.OV_SUPERAGENT_UPDATER_SOCKET || defaultSocketPath(), target);
  const apps = Array.isArray(result.apps) && result.apps.length > 0
    ? result.apps
    : [result.app].filter(Boolean);
  const commit = typeof result.commit === 'string' ? ` at ${result.commit.slice(0, 12)}` : '';
  const publication = result.published ? 'published and ' : '';
  stdout.write(`${publication}${apps.join(', ')} ${apps.length === 1 ? 'is' : 'are'} ready${commit} (${result.release})\n`);
  return 0;
}
