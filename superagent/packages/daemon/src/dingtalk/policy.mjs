// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { readFile } from 'node:fs/promises';
import path from 'node:path';

const DEFAULT = Object.freeze({ channel: 'stream', strategy: 'mention' });
const strategies = new Map([['stream', ['mention']], ['dws', ['mention', 'ambient']]]);
const id = (value) => typeof value === 'string' && value.length > 0 && value === value.trim();

export async function loadPolicy(dataDir) {
  let data;
  try {
    data = JSON.parse(await readFile(path.join(dataDir, 'dingtalk-policy.json'), 'utf8'));
  } catch (error) {
    if (error.code !== 'ENOENT') throw new Error('Invalid dingtalk-policy.json');
    data = {};
  }
  return parsePolicy(data);
}

export function parsePolicy(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Expected a DingTalk policy object');
  const { groups = {}, dws, defaultChannel } = value;
  if (defaultChannel != null && !['stream', 'off'].includes(defaultChannel)) {
    throw new Error('DingTalk defaultChannel must be stream or off');
  }
  if (!groups || typeof groups !== 'object' || Array.isArray(groups)) throw new Error('Expected DingTalk groups keyed by openConversationId');
  const routes = new Map();
  for (const [group, route] of Object.entries(groups)) {
    const valid = route?.channel === 'off' ? !Object.hasOwn(route, 'strategy')
      : strategies.get(route?.channel)?.includes(route.strategy);
    if (!id(group) || !valid) {
      throw new Error('Invalid DingTalk group channel or strategy');
    }
    routes.set(group, route);
  }
  const dwsGroups = [...routes].filter(([, route]) => route.channel === 'dws').map(([group]) => group);
  const allGroups = dws?.allGroups === true;
  const enabled = allGroups || dwsGroups.length > 0;
  const sendAs = dws?.sendAs ?? 'robot';
  const selfOpenId = sendAs === 'user' ? dws?.userOpenId : dws?.robotOpenId;
  if (enabled && (!['user', 'robot'].includes(sendAs) || !id(dws?.profile) || !id(selfOpenId) ||
      !id(dws?.tenantId) || (dws.cliBin != null && !id(dws.cliBin)))) {
    throw new Error('DingTalk DWS requires a profile, tenantId and the sender open ID for sendAs');
  }
  const fallback = allGroups ? { channel: 'dws', strategy: 'ambient' }
    : defaultChannel === 'off' ? { channel: 'off' } : DEFAULT;
  const stream = fallback.channel === 'stream' || [...routes.values()].some((route) => route.channel === 'stream');
  if (!stream && !enabled) throw new Error('DingTalk requires an enabled Stream or DWS receive channel');
  return {
    route: (group) => routes.get(group) ?? fallback,
    dwsGroups,
    stream,
    dws: enabled ? { ...dws, allGroups, sendAs, selfOpenId, cliBin: dws.cliBin ?? 'dws' } : null,
  };
}

export function classifyDws(event, strategy, selfOpenId) {
  if (event.data?.quoted_sender_id === selfOpenId) return 'direct';
  return strategy === 'ambient' ? 'ambient' : 'context';
}
