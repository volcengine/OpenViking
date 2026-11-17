#!/usr/bin/env node
// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Runs the whole card lifecycle without a Feishu app.
//
//   node demo.mjs                       # dry run: prints the cards and the calls that would be made
//   node demo.mjs --send --chat oc_xxx  # one real card, patched through its states
//
// `--send` needs FEISHU_APP_ID and FEISHU_APP_SECRET and a chat the app is in.
// Buttons work only once a callback handler (see README) is wired to actions.mjs.

import { createFeishuClient } from './client.mjs';
import { TaskCardActions } from './actions.mjs';
import { fitCard } from './card.mjs';

const NOW = Date.UTC(2026, 10, 17, 9, 40);
const send = process.argv.includes('--send');
const chatIndex = process.argv.indexOf('--chat');
const chatId = chatIndex > 0 ? process.argv[chatIndex + 1] : null;
if (send && !chatId) {
  console.error('--send needs --chat oc_xxx');
  process.exit(2);
}

const tasks = new Map([['t331', {
  id: 't331',
  title: '实现飞书状态卡片原地更新与中断操作',
  status: 'running',
  requesterId: 'ou_requester0001',
  requesterName: '瑞士卷',
  startedAt: NOW - 7 * 60_000,
  progress: ['读取 cc-connect 的卡片实现', '写出 Card 模型与两个按钮', '补测试'],
}]]);

// The scheduler these handlers stand in for: stop ends the task, retry starts a
// follow-up that follows the ended one.
const stop = (taskId, reason) => {
  const task = tasks.get(taskId);
  Object.assign(task, { status: 'stopped', endedAt: NOW, progress: [...task.progress, `已停止：${reason}`] });
  return task;
};
const retry = (taskId) => {
  const parent = tasks.get(taskId);
  const next = { ...parent, id: 't332', status: 'queued', startedAt: NOW, question: '', progress: [`由 ${taskId} 重试`] };
  tasks.set(next.id, next);
  return next;
};

const actions = new TaskCardActions({
  getTask: (taskId) => tasks.get(taskId) ?? null, stop, retry, now: () => NOW,
});

const client = createFeishuClient({
  dryRun: !send, appId: process.env.FEISHU_APP_ID, appSecret: process.env.FEISHU_APP_SECRET,
});

const cardFor = (task) => fitCard(task, { now: NOW, footer: `最后更新 ${new Date(NOW).toISOString().slice(11, 16)}Z` });
const task = tasks.get('t331');
const message = { id: null };

async function render() {
  const card = cardFor(task);
  if (message.id) await client.patchCard({ messageId: message.id, card });
  else message.id = (await client.sendCard({ chatId: chatId ?? 'oc_dryrun0001', card })).data?.message_id;
  return card;
}

// The click a button delivers: `value` from the card, `operator` from the
// callback, `eventId` from the callback envelope (what makes it replay-safe).
async function click({ value, operatorId, eventId }) {
  const result = await actions.handle({ value, operatorId, eventId, ownerId: 'ou_owner0001' });
  if (result.outcome === 'stopped') await render();
  if (result.outcome === 'retried') {
    await client.patchCard({ messageId: message.id, card: fitCard(task, { now: NOW, settled: true, note: `已重新派发为 ${result.newTaskId}` }) });
  }
  const shown = result.outcome === 'stopped' || result.outcome === 'retried'
    ? `${result.outcome}${result.newTaskId ? ` -> ${result.newTaskId}` : ''}`
    : `${result.outcome} (${result.reason})`;
  console.log(`  ${operatorId} -> ${value.superagent_task}: ${shown}`);
}

const first = await render();
console.log(`card ${message.id} sent as "${first.header.title.content}"`);
console.log('body:');
for (const element of first.elements) if (element.tag === 'div') console.log(`  | ${element.text.content.replace(/\n/g, '\n  | ')}`);
console.log('buttons:', first.elements.at(-1).actions.map((action) => `${action.text.content}(${action.type})`).join(', '));

task.status = 'waiting';
task.question = '要不要顺带把 /tasks 列表也换成卡片？';
await render();
console.log('\n-> waiting: header turns orange, the question leads the body');

task.status = 'running';
task.question = '';
task.progress.push('卡片在飞书里更新到第 4 行');
await render();

const stopValue = cardFor(task).elements.at(-1).actions[0].value;
console.log('\nclicks:');
await click({ value: stopValue, operatorId: 'ou_bystander0001', eventId: 'ev_0' });
await click({ value: stopValue, operatorId: 'ou_requester0001', eventId: 'ev_1' });
await click({ value: stopValue, operatorId: 'ou_requester0001', eventId: 'ev_1' });
await click({ value: stopValue, operatorId: 'ou_requester0001', eventId: 'ev_2' });

const retryValue = cardFor(task).elements.at(-1).actions[0].value;
await click({ value: retryValue, operatorId: 'ou_requester0001', eventId: 'ev_3' });
await click({ value: retryValue, operatorId: 'ou_requester0001', eventId: 'ev_4' });
console.log(`\nfinal card on screen: ${cardFor(task).header.title.content}`);

console.log('\ncalls:');
for (const call of client.calls) {
  const body = call.options.body ?? {};
  console.log(`  ${call.name.padEnd(6)} ${call.path}${call.options.query ?? ''} ${JSON.stringify(body.content ?? body).slice(0, 60)}…`);
}
