#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { createInterface } from 'node:readline';
import { connectorManifest, parseProtocolLine, resultMessage } from '../src/connectors/protocol.mjs';

const config = JSON.parse(process.env.PERSONAL_AGENT_CONNECTOR_CONFIG ?? '{}');
const name = config.channel || 'external';
if (config.dataDir) {
  fs.writeFileSync(path.join(config.dataDir, 'received.json'), JSON.stringify({
    argv: process.argv.slice(1),
    config,
  }));
}

const write = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);
createInterface({ input: process.stdin }).on('line', (line) => {
  const request = parseProtocolLine(line);
  if (request.operation === 'spec') {
    write(resultMessage(request.id, { manifest: connectorManifest({
      name, version: 'external-test', capabilities: ['events', 'actions'],
      actions: { 'message.send': { content: ['markdown'] } },
    }) }));
  } else if (request.operation === 'check') {
    write(resultMessage(request.id, { owner: { id: 'owner-1', target: { chat_id: 'direct:1' } }, self: { id: 'bot-1' } }));
  } else {
    write(resultMessage(request.id, {}));
    if (request.operation === 'stop') setImmediate(() => process.exit(0));
  }
});
