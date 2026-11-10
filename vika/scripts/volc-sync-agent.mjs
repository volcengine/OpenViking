#!/usr/bin/env node
// Provision a NEW dedicated MA Agent by default; never reuse another product's
// agent. Re-running with VIKA_VOLC_AGENT_ID updates only vika-channel-* agents.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createRequire } from 'node:module';
import { randomUUID } from 'node:crypto';
import { getConfig } from '../packages/daemon/src/config.mjs';
import { ActionService } from '../packages/daemon/src/actions/service.mjs';
import { getConnectorManifest } from '../packages/daemon/src/connectors/catalog.mjs';
import { maTools, maToolsHash, canonicalJson } from '../packages/daemon/src/volc-tools.mjs';
import { assembleVolcCloudConversationPolicy } from '../packages/daemon/src/conversation-policy.mjs';

if (process.env.VIKA_CONFIG) {
  const require = createRequire(import.meta.url);
  const { loadDeploymentManifest, applyDeploymentAgentEnv, applyDeploymentDaemonEnv } = require('../deployment-config.cjs');
  const deployment = loadDeploymentManifest(process.env);
  applyDeploymentDaemonEnv(process.env, deployment); applyDeploymentAgentEnv(process.env, deployment);
}
const config = getConfig();
if (!config.volcApiKey) throw new Error('ARK_MA_API_KEY or ARK_API_KEY is required');
const quiet = { info() {}, warn() {} };
// Offline bootstrap constructs the same ActionService capability filter. A live
// socket is preferred when available so custom connector manifests are exact.
let actions;
if (process.argv.includes('--socket')) {
  const tools = await new Promise((resolve, reject) => {
    const request = http.get({ socketPath: config.actionSocketPath, path: '/v1/tools' }, (response) => {
      let body = ''; response.on('data', (chunk) => { body += chunk; });
      response.on('end', () => { try { resolve(JSON.parse(body).tools); } catch (error) { reject(error); } });
    }); request.on('error', reject);
  });
  actions = { tools: () => tools };
} else {
  actions = new ActionService({ config, store: null, logger: quiet,
    connectors: { manifests: () => config.connectors.map((id) => [id, getConnectorManifest(id)]) },
    meetingEnabled: config.connectors.includes('lark'), timers: {},
  });
}
const custom = maTools(actions, { viaGateway: config.ovViaGateway });
async function api(resource, body) {
  const response = await fetch(`${config.volcBaseUrl}${resource}`, { method: body ? 'POST' : 'GET',
    headers: { Authorization: `Bearer ${config.volcApiKey}`, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(60000) });
  if (!response.ok) throw new Error(`MA ${resource.split('?')[0]} failed (${response.status}); no credentials logged`);
  return response.json();
}
const statePath = process.env.VIKA_VOLC_PROVISION_FILE || path.join(config.dataDir, 'ma-provision.json');
fs.mkdirSync(path.dirname(statePath), { recursive: true, mode: 0o700 });
let state = fs.existsSync(statePath) ? JSON.parse(fs.readFileSync(statePath, 'utf8')) : {};
const save = () => fs.writeFileSync(statePath, `${JSON.stringify(state, null, 2)}\n`, { mode: 0o600 });
state.name ||= `vika-channel-${randomUUID().slice(0, 8)}`;
save();
const builtin = { type: 'agent_toolset_20260916', default_config: { enabled: true, permission_policy: { type: 'always_allow' } } };
const body = { model: { id: process.env.VIKA_VOLC_MODEL || 'doubao-seed-2-1-turbo-260628' },
  system: assembleVolcCloudConversationPolicy(config), tools: [builtin, ...custom] };
let agent;
const agentId = process.env.VIKA_VOLC_AGENT_ID || state.agentId;
if (agentId) {
  agent = await api(`/agents/${encodeURIComponent(agentId)}`);
  if (!agent.name?.startsWith('vika-channel-')) throw new Error('Refusing to update a non-Vika Agent');
  if (maToolsHash(agent.tools ?? []) !== maToolsHash(custom) || agent.system !== body.system || canonicalJson(agent.tools?.filter(t => t.type !== 'custom')) !== canonicalJson([builtin])) {
    agent = await api(`/agents/${encodeURIComponent(agentId)}`, { ...body, version: agent.version });
  }
} else {
  agent = await api('/agents', { name: state.name, ...body });
}
state.agentId = agent.id; state.agentVersion = agent.version; state.toolsHash = maToolsHash(custom); save();
if (!state.environmentId) {
  const environment = await api('/environments', { name: `${state.name}-cloud`, config: { type: 'cloud',
    networking: { type: 'unrestricted' }, setup_script: '#!/bin/sh\nset -eu\nmkdir -p /mnt/session/outputs\n' } });
  state.environmentId = environment.id; save();
}
if (!state.sessionId || state.templateVersion !== agent.version) {
  const session = await api('/sessions', { agent: { type: 'agent', id: agent.id, version: agent.version },
    environment_id: state.environmentId, title: `${state.name} template` });
  state.sessionId = session.id; state.templateVersion = agent.version; save();
}
console.log(JSON.stringify({ provisionFile: statePath, toolCount: custom.length, toolsHash: state.toolsHash,
  VIKA_VOLC_ENVIRONMENT_ID: state.environmentId, VIKA_VOLC_SESSION_ID: state.sessionId }, null, 2));
