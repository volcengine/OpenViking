// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';

// Real Pi loads these synthetic extensions and stdio MCP servers. Every
// marker and config lives in the test directory, with no external services.
export function configurePiIsolation(root, agentDir, workspace) {
  const write = (file, text) => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, typeof text === 'string' ? text : JSON.stringify(text));
    return file;
  };
  const markers = [];
  const extension = (file, name, blocked = false) => {
    const marker = path.join(root, `${name}-loaded`);
    if (blocked) markers.push(marker);
    return write(file, `import fs from 'node:fs';
      export default function(pi) {
        fs.writeFileSync(${JSON.stringify(marker)}, 'loaded');
        pi.registerTool({ name: ${JSON.stringify(name)}, label: 'Fixture tool', description: 'Return a fixture marker',
          parameters: { type: 'object', properties: {}, additionalProperties: false },
          async execute() { return { content: [{ type: 'text', text: ${JSON.stringify(name + ' worked')} }] }; }
        });
      }`);
  };
  extension(path.join(agentDir, 'custom', 'configured.js'), 'configured_tool');
  extension(path.join(agentDir, 'extensions', 'disabled.js'), 'disabled_tool', true);
  extension(path.join(agentDir, 'extensions', 'openviking', 'index.js'), 'openviking_auto', true);
  extension(path.join(agentDir, 'custom', 'openviking.js'), 'openviking_configured', true);
  extension(path.join(agentDir, 'custom', 'neutral', 'index.js'), 'openviking_manifest', true);
  write(path.join(agentDir, 'custom', 'neutral', 'package.json'), { name: 'openviking-pi-extension' });
  extension(path.join(agentDir, 'packages', 'tools', 'index.js'), 'packaged_tool');
  write(path.join(agentDir, 'packages', 'tools', 'package.json'), { name: '@fixture/tools', pi: { extensions: ['./index.js'] } });
  extension(path.join(agentDir, 'packages', 'neutral', 'index.js'), 'openviking_package', true);
  write(path.join(agentDir, 'packages', 'neutral', 'package.json'), { name: '@openviking/pi-extension', pi: { extensions: ['./index.js'] } });
  extension(path.join(workspace, '.pi', 'extensions', 'local.js'), 'project_tool');
  extension(path.join(workspace, '.pi', 'custom', 'configured.js'), 'project_configured_tool');
  extension(path.join(workspace, '.pi', 'extensions', 'openviking.js'), 'openviking_project', true);
  extension(path.join(workspace, '.pi', 'packages', 'tools', 'index.js'), 'project_packaged_tool');
  write(path.join(workspace, '.pi', 'packages', 'tools', 'package.json'), { name: '@fixture/project-tools', pi: { extensions: ['./index.js'] } });
  const files = [
    write(path.join(agentDir, 'settings.json'), {
      extensions: ['./custom/configured.js', './custom/openviking.js', './custom/neutral/index.js', '-extensions/disabled.js'],
      packages: ['./packages/tools', './packages/neutral'],
    }),
    write(path.join(workspace, '.pi', 'settings.json'), {
      extensions: ['./custom/configured.js'], packages: ['./packages/tools'],
    }),
  ];
  const server = write(path.join(root, 'other-mcp.mjs'), `
    import { createInterface } from 'node:readline';
    for await (const line of createInterface({ input: process.stdin })) {
      const request = JSON.parse(line);
      if (request.id === undefined) continue;
      let result = {};
      if (request.method === 'initialize') result = {
        protocolVersion: request.params.protocolVersion, capabilities: { tools: {} },
        serverInfo: { name: process.argv[2], version: '1.0.0' }
      };
      if (request.method === 'tools/list') result = { tools: [{
        name: 'ping', description: process.argv[2] + ' fixture ping',
        inputSchema: { type: 'object', properties: {}, additionalProperties: false }
      }] };
      if (request.method === 'tools/call') result = { content: [{ type: 'text', text: process.argv[2] + ' worked' }] };
      process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result }) + '\\n');
    }
  `);
  const mcp = (name, exposure) => ({ command: process.execPath, args: [server, name], exposure });
  files.push(write(path.join(agentDir, 'mcp.json'), { mcpServers: {
    global_tools: mcp('global_tools', 'direct'), indirect: mcp('indirect', 'codemode'), deferred: mcp('deferred', 'deferred'),
  } }));
  files.push(write(path.join(workspace, '.pi', 'mcp.json'), { mcpServers: { project_tools: mcp('project_tools', 'direct') } }));
  return { markers, originals: files.map((file) => [file, fs.readFileSync(file, 'utf8')]) };
}
