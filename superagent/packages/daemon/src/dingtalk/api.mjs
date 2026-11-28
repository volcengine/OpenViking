// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

const API = 'https://api.dingtalk.com/v1.0';

export async function requestDingTalk(route, body, timeoutMs, token) {
  let response;
  try {
    response = await fetch(API + route, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { 'x-acs-dingtalk-access-token': token } : {}) },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
      redirect: 'error',
    });
  } catch {
    throw new Error(`DingTalk ${route} request failed or timed out`);
  }
  if (!response.ok) throw new Error(`DingTalk ${route} rejected the request (HTTP ${response.status})`);
  const data = await response.json().catch(() => null);
  if (!data || typeof data !== 'object') throw new Error(`DingTalk ${route} returned an invalid response`);
  return data;
}

export async function sendGroup({ group, robotCode, text, timeoutMs, token }) {
  const title = Array.from(text.trim().split(/\r?\n/, 1)[0]).slice(0, 20).join('');
  const { processQueryKey } = await requestDingTalk('/robot/groupMessages/send', {
    robotCode, openConversationId: group, msgKey: 'sampleMarkdown', msgParam: JSON.stringify({ title, text }),
  }, timeoutMs, token);
  if (!processQueryKey) throw new Error('DingTalk did not return a group message process query key');
  return processQueryKey;
}
