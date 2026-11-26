// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { capabilities } from '../../daemon/src/capabilities.mjs';

import http from 'node:http';
import { assertUpgradeTarget, defaultUpdaterEndpoint, readUpdaterToken, upgradeTargetNames } from '../../updater/src/targets.mjs';

// `ov-superagent upgrade` only asks the updater process; the updater fetches, publishes
// and rolls the apps, so the upgrade survives the caller disconnecting.

function requestUpgrade(socketPath, target, token = '') {
  const body = JSON.stringify({ target });
  return new Promise((resolve, reject) => {
    const request = http.request({
      socketPath,
      path: '/v1/upgrade',
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'content-length': Buffer.byteLength(body),
        // The Windows endpoint is a named pipe whose name cannot prove who answers.
        ...(token ? { 'x-superagent-token': token } : {}),
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
    // A POSIX upgrade downloads a whole archive, and a Windows one runs the
    // packaged installer, which downloads and unpacks a ZIP; neither is a short
    // request on a slow link. The updater keeps working if this client gives up.
    request.setTimeout(1_800_000, () => request.destroy(new Error('updater request timed out')));
    request.once('error', reject);
    request.end(body);
  });
}

export async function upgradeCommand(args, { stdout = process.stdout, stderr = process.stderr, env = process.env } = {}) {
  if (!capabilities.updater) {
    stderr.write('Automatic release upgrades are not available on this platform. Stop OpenViking SuperAgent and update the installation manually.\n');
    return 1;
  }
  const [target, ...extra] = args;
  if (!target || extra.length > 0) {
    stderr.write(`usage: ov-superagent upgrade <${upgradeTargetNames().join('|')}>\n`);
    return 2;
  }
  assertUpgradeTarget(target);
  const endpoint = env.OV_SUPERAGENT_UPDATER_SOCKET || defaultUpdaterEndpoint();
  const result = await requestUpgrade(endpoint, target, await readUpdaterToken().catch(() => ''));
  const apps = Array.isArray(result.apps) && result.apps.length > 0
    ? result.apps
    : [result.app].filter(Boolean);
  const commit = typeof result.commit === 'string' ? ` at ${result.commit.slice(0, 12)}` : '';
  const publication = result.published ? 'published and ' : '';
  const skipped = new Set(Array.isArray(result.skipped) ? result.skipped : []);
  const restarted = apps.filter((app) => !skipped.has(app));
  const unchanged = apps.filter((app) => skipped.has(app));
  // The updater leaves an app that already runs this release alone, so it is
  // reported apart from the apps it restarted.
  const ready = restarted.length > 0
    ? `${restarted.join(', ')} ${restarted.length === 1 ? 'is' : 'are'} ready`
    : 'nothing was restarted';
  const kept = unchanged.length > 0
    ? `; ${unchanged.join(', ')} already ${unchanged.length === 1 ? 'runs' : 'run'} it and ${unchanged.length === 1 ? 'was' : 'were'} not restarted`
    : '';
  stdout.write(`${publication}${ready}${commit} (${result.release})${kept}\n`);
  return 0;
}
