// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The durable runtime reads models the way Pi does: pi-ai's built-in providers
// plus Pi's agent directory (~/.pi/agent, or PI_CODING_AGENT_DIR) — custom
// providers in models.json (an OpenAI-compatible Volcengine Ark endpoint, for
// instance), keys and logins in auth.json, the default model in settings.json.
// A model that works in `vika pi` therefore works here, with no second config.

import fs from 'node:fs';
import path from 'node:path';
import { ModelRuntime, getAgentDir } from '@earendil-works/pi-coding-agent';

// Offline: model lists come from the static catalogs and Pi's cached store,
// never a network refresh at startup.
export async function loadDurableModels() {
  const runtime = await ModelRuntime.create();
  return { models: runtime.models, error: runtime.getError() };
}

export function piDefaultModel(agentDir = getAgentDir()) {
  try {
    const settings = JSON.parse(fs.readFileSync(path.join(agentDir, 'settings.json'), 'utf8'));
    if (settings?.defaultProvider && settings?.defaultModel) return `${settings.defaultProvider}/${settings.defaultModel}`;
  } catch {}
  return '';
}

// Chat models that have credentials, Pi's default model first when it is one.
export async function availableChatModels(models, preferred = piDefaultModel()) {
  let available;
  try {
    available = await models.getAvailable();
  } catch (error) {
    // A read-only or wrongly owned agent directory fails here, not at load
    // time; name the directory instead of reporting "no model".
    throw new Error(`cannot read Pi's model credentials (${getAgentDir() || 'Pi agent directory'}): ${error?.message ?? error}`);
  }
  const chat = available.filter((model) => (model.type ?? 'chat') === 'chat');
  const index = chat.findIndex((model) => `${model.provider}/${model.id}` === preferred);
  return index > 0 ? [chat[index], ...chat.slice(0, index), ...chat.slice(index + 1)] : chat;
}
