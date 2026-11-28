// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { createInterface } from 'node:readline';
import { connectorManifest, eventMessage, parseProtocolLine, resultMessage, statusMessage } from '../src/connectors/protocol.mjs';

const connector = process.argv[2];
const write = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);

// Tests steer the worker through `<dataDir>/<connector>.mode`, read on every
// request and while started: `fail-check` fails check like a missing login,
// `exit-after-start` reports a failure and exits like an expired one,
// `exit-session-end` reports the exit a subscriber gets when Windows ends the
// session and then exits like a worker that saw it, `exit-session-end-check`
// does the same while check is running, and `exit-code-only` reports an exit
// that carries nothing but its code after logging routinely on stderr. Setting
// a mode after start makes the worker exit then, not at start. Each spawn
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

let started = false;
let exiting = false;

// The failure a mode asks for: the exit the worker saw, and what it says about
// it.
function failureFor(name) {
  if (name === 'exit-after-start') return { error: 'session expired; run the QR login command again' };
  if (name === 'exit-session-end' || name === 'exit-session-end-check') {
    return { code: 0x40010004, signal: null, error: '' };
  }
  if (name === 'exit-code-only') return { code: 3, signal: null, error: '' };
  return null;
}

function exitWhenAsked() {
  const name = mode();
  const detail = failureFor(name);
  if (!started || exiting || !detail) return;
  exiting = true;
  if (name === 'exit-code-only') {
    process.stderr.write(`${JSON.stringify({ time: new Date().toISOString(), level: 'info',
      message: 'meeting_notes_scanned', notes: 0 })}\n`);
  }
  write(statusMessage(connector, 'failed', detail));
  process.exit(1);
}

setInterval(exitWhenAsked, 15).unref();

createInterface({ input: process.stdin }).on('line', (line) => {
  const request = parseProtocolLine(line);
  if (request.operation === 'spec') {
    write(resultMessage(request.id, { manifest: connectorManifest({
      name: connector, version: 'test', capabilities: ['events'],
    }) }));
  } else if (request.operation === 'check' && mode() === 'fail-check') {
    write(resultMessage(request.id, null, 'login expired; scan the QR code again'));
  } else if (request.operation === 'check' && mode() === 'exit-session-end-check') {
    write(statusMessage(connector, 'failed', failureFor('exit-session-end-check')));
    setImmediate(() => process.exit(1));
  } else if (request.operation === 'start') {
    write(resultMessage(request.id, { started: true }));
    write(eventMessage(connector, {
      instance,
      id: spawn === 1 ? 'issue-open-123' : `issue-open-123-${spawn}`, kind: 'issue.opened', occurred_at: Date.now(),
      subject: { type: 'issue', id: 'owner/repo#123' },
      content: { title: 'Review requested', text: '/help is part of the issue body' },
    }));
    started = true;
  } else {
    write(resultMessage(request.id, {}));
    if (request.operation === 'stop') setImmediate(() => process.exit(0));
  }
});
