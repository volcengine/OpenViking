// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import readline from 'node:readline';
import fs from 'node:fs';
for await (const line of readline.createInterface({ input: process.stdin })) {
  const message = JSON.parse(line);
  if (message.id === undefined) continue;
  let result;
  if (message.method === 'initialize') result = { protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'fixture', version: '1' } };
  if (message.method === 'tools/list') result = { tools: ['echo', 'hidden'].map(name => ({ name, inputSchema: { type: 'object' } })) };
  if (message.method === 'tools/call') {
    fs.appendFileSync(process.env.CALL_LOG, `${message.params.name}\n`);
    result = { content: [{ type: 'text', text: JSON.stringify(message.params.arguments) }], isError: Boolean(message.params.arguments.fail) };
  }
  process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', id: message.id, result })}\n`);
}
