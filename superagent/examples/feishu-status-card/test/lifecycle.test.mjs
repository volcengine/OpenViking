// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The whole loop against a fake platform: what a reader would see on one card,
// from the first send to the click that ends it.

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { TaskCardActions } from '../actions.mjs';
import { taskCard } from '../card.mjs';
import { createFeishuClient } from '../client.mjs';

const NOW = Date.UTC(2026, 10, 17, 9, 40);
const REQUESTER = 'ou_requester0001';

function platform() {
  const sent = [];
  const fetchImpl = async (url, options) => {
    const body = options.body ? JSON.parse(options.body) : {};
    if (url.endsWith('/tenant_access_token/internal')) {
      return { ok: true, status: 200, text: async () => JSON.stringify({ code: 0, tenant_access_token: 't', expire: 7200 }) };
    }
    if (options.method === 'PATCH') {
      const card = JSON.parse(body.content);
      sent.at(-1).patched.push(card);
      return { ok: true, status: 200, text: async () => JSON.stringify({ code: 0 }) };
    }
    sent.push({ card: JSON.parse(body.content), patched: [] });
    return { ok: true, status: 200, text: async () => JSON.stringify({ code: 0, data: { message_id: `om_${sent.length}` } }) };
  };
  return { sent, client: createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl, sleep: async () => {} }) };
}

const lastCard = (message) => message.patched.at(-1);
const titles = (card) => card.elements[0].text.content;

describe('one card through a task', () => {
  it('sends once, patches the same message, and ends without buttons', async () => {
    const { sent, client } = platform();
    const tasks = new Map([['t331', {
      id: 't331', title: '实现飞书状态卡片', status: 'running', requesterId: REQUESTER, requesterName: '瑞士卷', startedAt: NOW - 60_000,
    }]]);
    const actions = new TaskCardActions({ getTask: (id) => tasks.get(id) ?? null, stop: () => {}, retry: () => ({ id: 't332' }) });

    const first = await client.sendCard({ chatId: 'oc_1234567890abcdef', card: taskCard(tasks.get('t331'), { now: NOW }) });
    const messageId = first.data.message_id;
    assert.equal(sent.length, 1);
    assert.equal(sent[0].card.header.template, 'blue');
    assert.deepEqual(sent[0].card.elements.at(-1).actions.map((action) => action.text.content), ['停止']);

    tasks.get('t331').status = 'waiting';
    tasks.get('t331').question = '要继续吗？';
    await client.patchCard({ messageId, card: taskCard(tasks.get('t331'), { now: NOW }) });
    assert.equal(lastCard(sent[0]).header.template, 'orange');
    assert.ok(JSON.stringify(lastCard(sent[0])).includes('等待你的回答'));

    tasks.get('t331').status = 'running';
    tasks.get('t331').question = '';
    const value = taskCard(tasks.get('t331'), { now: NOW }).elements.at(-1).actions[0].value;
    const result = await actions.handle({ value, operatorId: REQUESTER, eventId: 'ev_1' });
    assert.equal(result.outcome, 'stopped');

    tasks.get('t331').status = 'stopped';
    await client.patchCard({ messageId, card: taskCard(tasks.get('t331'), { now: NOW }) });
    assert.equal(sent.length, 1, 'still one message');
    assert.equal(sent[0].patched.length, 2);
    assert.equal(lastCard(sent[0]).header.template, 'grey');
    // A stopped task can be started again, so the card keeps one button: 重试.
    assert.deepEqual(lastCard(sent[0]).elements.at(-1).actions.map((action) => action.text.content), ['重试']);
    assert.match(JSON.stringify(lastCard(sent[0])), /已停止/);
  });
});
