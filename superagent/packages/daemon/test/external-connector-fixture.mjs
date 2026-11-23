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

function chat(id, text) {
  write({
    protocol_version: '0.2',
    type: 'EVENT',
    connector: name,
    instance: name,
    id: `message:${id}`,
    kind: 'message.created',
    occurred_at: Date.now(),
    subject: { type: 'conversation', id: 'direct:1' },
    actor: { type: 'user', id: 'owner-1' },
    content: { text },
    conversation: { id: 'direct:1', type: 'p2p' },
    message: { id, type: 'text' },
    reply_route: { action: 'message.reply', input: { message_id: id } },
  });
}

createInterface({ input: process.stdin }).on('line', (line) => {
  const request = parseProtocolLine(line);
  if (request.operation === 'spec') {
    write(resultMessage(request.id, { manifest: connectorManifest({
      name, version: 'external-test', capabilities: ['events', 'actions'],
      actions: {
        'message.send': { content: ['markdown'] },
        'message.reply': { content: ['markdown'] },
      },
    }) }));
  } else if (request.operation === 'check') {
    write(resultMessage(request.id, { owner: { id: 'owner-1', target: { chat_id: 'direct:1' } }, self: { id: 'bot-1' } }));
  } else if (request.operation === 'start') {
    write(resultMessage(request.id, {}));
    chat('dm-1', 'hello');
  } else if (request.operation === 'action') {
    const file = config.dataDir ? path.join(config.dataDir, 'actions.json') : null;
    const actions = file && fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : [];
    actions.push({ name: request.payload.name, input: request.payload.input });
    if (file) fs.writeFileSync(file, JSON.stringify(actions));
    write(resultMessage(request.id, { message_id: `sent-${actions.length}` }));
    // The first delivery is the model's reply. The private /help comes after it,
    // so the reply is not stale by the time it is sent.
    if (actions.length === 1) chat('help-1', '/help');
  } else {
    write(resultMessage(request.id, {}));
    if (request.operation === 'stop') setImmediate(() => process.exit(0));
  }
});
