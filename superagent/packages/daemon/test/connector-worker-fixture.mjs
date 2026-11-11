// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { createInterface } from 'node:readline';
import { connectorManifest, eventMessage, parseProtocolLine, resultMessage, statusMessage } from '../src/connectors/protocol.mjs';

const connector = process.argv[2];
const write = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);

// Tests steer the worker through `<dataDir>/<connector>.mode`, read on every
// request: `fail-check` fails check like a missing login, `exit-after-start`
// reports a failure and exits once started like an expired one. Each spawn
// appends its pid to `<dataDir>/<connector>.spawns`; its event id carries the
// spawn number.
const { dataDir = null, instance = connector, emailConfigFile = null } = JSON.parse(process.env.PERSONAL_AGENT_CONNECTOR_CONFIG ?? '{}');
const control = (suffix) => path.join(dataDir, `${instance}.${suffix}`);
const mode = () => {
  try { return fs.readFileSync(control('mode'), 'utf8').trim(); } catch { return 'ok'; }
};
let spawn = 1;
if (dataDir) {
  if (emailConfigFile) fs.writeFileSync(control('account'), emailConfigFile);
  fs.appendFileSync(control('spawns'), `${process.pid}\n`);
  spawn = fs.readFileSync(control('spawns'), 'utf8').split('\n').filter(Boolean).length;
}

createInterface({ input: process.stdin }).on('line', (line) => {
  const request = parseProtocolLine(line);
  if (request.operation === 'spec') {
    write(resultMessage(request.id, { manifest: connectorManifest({
      name: connector, version: 'test', capabilities: ['events'],
    }) }));
  } else if (request.operation === 'check' && mode() === 'fail-check') {
    write(resultMessage(request.id, null, 'login expired; scan the QR code again'));
  } else if (request.operation === 'start') {
    write(resultMessage(request.id, { started: true }));
    write(eventMessage(connector, {
      instance,
      id: spawn === 1 ? 'issue-open-123' : `issue-open-123-${spawn}`, kind: 'issue.opened', occurred_at: Date.now(),
      subject: { type: 'issue', id: 'owner/repo#123' },
      content: { title: 'Review requested', text: '/help is part of the issue body' },
    }));
    if (mode() === 'exit-after-start') {
      setTimeout(() => {
        write(statusMessage(connector, 'failed', { error: 'session expired; run the QR login command again' }));
        process.exit(1);
      }, 30);
    }
  } else {
    write(resultMessage(request.id, {}));
    if (request.operation === 'stop') setImmediate(() => process.exit(0));
  }
});
