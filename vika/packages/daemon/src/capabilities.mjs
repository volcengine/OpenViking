// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const MAX_BYTES = 128 * 1024;
const ID = /^[a-z][a-z0-9_-]{0,63}$/;
const object = value => value && typeof value === 'object' && !Array.isArray(value);
function bounded(file) {
  if (!fs.statSync(file).isFile()) throw new Error('Capability resource must be a regular file');
  const fd = fs.openSync(file, 'r');
  try {
    if (!fs.fstatSync(fd).isFile()) throw new Error('Capability resource must be a regular file');
    const buffer = Buffer.alloc(MAX_BYTES + 1);
    const bytes = fs.readSync(fd, buffer, 0, buffer.length, 0);
    if (bytes > MAX_BYTES) throw new Error('Capability resource exceeds 128 KiB');
    return buffer.subarray(0, bytes).toString('utf8');
  } finally { fs.closeSync(fd); }
}
function keys(value, allowed) {
  if (!object(value) || Object.keys(value).some(key => !allowed.includes(key))) throw new Error('Invalid capability configuration fields');
}

// Operator-owned configuration, read once. Never imports a user's runtime plugins.
export class CoreCapabilities {
  constructor(file, workspace) {
    if (!path.isAbsolute(file)) throw new Error('capabilitiesFile must be absolute');
    const config = JSON.parse(bounded(file));
    keys(config, ['skills', 'mcpServers']);
    this.skills = config.skills ?? {};
    this.servers = config.mcpServers ?? {};
    for (const group of [this.skills, this.servers]) {
      if (!object(group) || Object.keys(group).length > 32 || Object.keys(group).some(id => !ID.test(id))) throw new Error('Invalid capability catalogue');
    }
    for (const skill of Object.values(this.skills)) {
      keys(skill, ['directory', 'description']);
      if (typeof skill.directory !== 'string' || !path.isAbsolute(skill.directory)
        || typeof skill.description !== 'string' || skill.description.length > 2000) throw new Error('Invalid skill configuration');
      skill.directory = fs.realpathSync(skill.directory);
    }
    for (const server of Object.values(this.servers)) {
      keys(server, ['command', 'args', 'env', 'tools']);
      if (typeof server.command !== 'string' || !path.isAbsolute(server.command)
        || !Array.isArray(server.tools) || !server.tools.length || server.tools.length > 128
        || server.tools.some(name => typeof name !== 'string' || !name || name.length > 128)
        || (server.args !== undefined && (!Array.isArray(server.args) || server.args.some(arg => typeof arg !== 'string')))
        || (server.env !== undefined && (!object(server.env) || Object.values(server.env).some(value => typeof value !== 'string')))) throw new Error('Invalid MCP server configuration');
    }
    this.workspace = workspace;
    this.connections = new Map();
    this.closed = false;
  }

  list() {
    return {
      skills: Object.entries(this.skills).map(([id, skill]) => ({ id, description: skill.description })),
      mcpServers: Object.entries(this.servers).map(([id, server]) => ({ id, tools: server.tools })),
    };
  }

  readSkill(id, resource = 'SKILL.md') {
    const skill = this.skills[id];
    if (!Object.hasOwn(this.skills, id) || !skill) throw new Error('Unknown skill');
    if (!resource || path.isAbsolute(resource) || resource.includes('\\') || resource.split('/').some(part => part === '..' || part === '.')) throw new Error('Invalid skill resource path');
    const file = fs.realpathSync(path.join(skill.directory, resource));
    const relative = path.relative(skill.directory, file);
    if (!relative || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) throw new Error('Skill resource escapes its directory');
    const content = bounded(file);
    return { id, resource, path: file, sha256: createHash('sha256').update(content).digest('hex'), content };
  }

  async connection(id) {
    if (this.closed) throw new Error('Capabilities are closed');
    if (!Object.hasOwn(this.servers, id)) throw new Error('Unknown MCP server');
    if (!this.connections.has(id)) {
      const server = this.servers[id];
      const pending = (async () => {
        const client = new Client({ name: 'vika-core', version: '0.4.0' }, { capabilities: {} });
        const transport = new StdioClientTransport({ command: server.command, args: server.args ?? [], env: server.env ?? {}, cwd: this.workspace, stderr: 'ignore' });
        try {
          await client.connect(transport, { timeout: 10_000 });
          const tools = [];
          let cursor;
          for (let page = 0; page < 16; page++) {
            const result = await client.listTools(cursor ? { cursor } : {}, { timeout: 10_000 });
            tools.push(...result.tools.filter(tool => server.tools.includes(tool.name)));
            if (tools.length > 128 || Buffer.byteLength(JSON.stringify(tools)) > MAX_BYTES) throw new Error('MCP tool catalogue is too large');
            cursor = result.nextCursor;
            if (!cursor) break;
          }
          if (cursor || server.tools.some(name => !tools.some(tool => tool.name === name))) throw new Error('MCP tool catalogue is incomplete');
          return { client, tools };
        } catch {
          await client.close().catch(() => {});
          throw new Error(`MCP server ${id} could not initialize; check its configuration`);
        }
      })();
      // Failed connections remain failed until restart: never respawn on every model call.
      this.connections.set(id, pending);
    }
    return this.connections.get(id);
  }

  async listMcp(id) { return (await this.connection(id)).tools; }

  async callMcp(id, name, args, assertAuthority) {
    if (!Object.hasOwn(this.servers, id) || !this.servers[id].tools.includes(name)) throw new Error('MCP tool is not allowed');
    const { client } = await this.connection(id);
    assertAuthority(); // Initialization may have outlived the invoking runtime.
    try {
      const result = await client.callTool({ name, arguments: args }, undefined, { timeout: 30_000 });
      assertAuthority();
      if (Buffer.byteLength(JSON.stringify(result)) > MAX_BYTES) throw new Error('oversized');
      return result;
    } catch {
      // Do not expose transport errors containing credentials, or retry uncertain writes.
      throw new Error(`MCP call to ${id}/${name} failed or its result is unavailable; side effects may have occurred. Do not automatically retry.`);
    }
  }

  async close() {
    this.closed = true;
    await Promise.allSettled([...this.connections.values()].map(async pending => (await pending).client.close()));
  }
}
