// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';

const testDir = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.resolve(testDir, '..', 'public');
const [html, css, js] = await Promise.all([
  readFile(path.join(publicDir, 'index.html'), 'utf8'),
  readFile(path.join(publicDir, 'activity.css'), 'utf8'),
  readFile(path.join(publicDir, 'activity.js'), 'utf8'),
]);

test('页面骨架：顶栏 + 压缩时间带 + 上下文流', () => {
  for (const id of ['strip', 'strip-track', 'feed', 'entries', 'live', 'load-older', 'follow-live']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(css, /\.strip-track \{[^}]*height: 36px/s);
});

test('保留实例切换、会话选择与隐私开关', () => {
  for (const id of ['instance-select', 'session-select', 'session-prev', 'session-next', 'privacy-toggle']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(js, /const INSTANCES_API = '\/api\/instances'/);
  assert.match(js, /url\.searchParams\.set\('instance', state\.instance\)/);
  assert.match(js, /url\.searchParams\.set\('epoch'/);
  assert.match(js, /history\.replaceState/);
  assert.match(js, /classList\.toggle\('privacy'\)/);
  assert.match(css, /body\.privacy \.txt/);
});

test('实时：每秒按 after 游标增量拉取，空会话不走 after', () => {
  assert.match(js, /POLL_MS = 1000/);
  assert.match(js, /fetchActivity\(state\.rows\.size \? \{ after: maxId\(\) \} : \{\}\)/);
  assert.match(js, /while \(data\.hasNewer && state\.rows\.size\)/);
  assert.match(js, /\{ before: state\.nextCursor \}/);
});

test('回合分组：消息按 inboundSeqs 归入回合，工具按 toolId 配对', () => {
  assert.match(js, /kind === 'agent\.turn_started'/);
  assert.match(js, /claim\(open, meta\.inboundSeqs\)/);
  assert.match(js, /tools\.get\(toolId\)/);
  assert.match(js, /LARK_DELIVERY = /);
  assert.match(js, /CLI_DELIVERY = /); // 保留方舟 Worker 和旧版 CLI 投递记录
  assert.match(js, /模型实际收到/);
});

test('时间带折叠长空白并高亮视口内回合', () => {
  assert.match(js, /GAP_MS = 120_000/);
  assert.match(js, /function buildTimeMap\(/);
  assert.match(js, /IntersectionObserver/);
  assert.match(css, /\.strip \.bar\.inview/);
});

test('每轮输入/输出单独成区，系统事件进侧栏', () => {
  assert.match(js, /'io io-in'/);
  assert.match(js, /`io io-out/);
  assert.match(js, /kind\.startsWith\('system\.'\)\) \{ system\.push\(row\)/);
  assert.match(html, /id="system"/);
});

test('渲染层只用安全 DOM API', () => {
  assert.doesNotMatch(js, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
});

test('Core 已结算投递进入输出区，MCP 调用和未确认投递不会重复计作成功', () => {
  const { buildModel, deliveryOf } = runInNewContext(
    js.slice(0, js.indexOf('// ---------- 渲染：上下文流')) + '\n({ buildModel, deliveryOf });',
    { document: { getElementById: () => ({}) } },
  );
  for (const connector of ['lark', 'weixin']) {
    const rows = [
      { kind: 'agent.turn_started', metadata: { inboundSeqs: [1] } },
      { kind: 'agent.tool_called', summary: 'personal_agent · reply_message', metadata: {
        toolId: 'reply', toolName: 'reply_message', toolServer: 'personal_agent',
        input: JSON.stringify({ markdown: '已完成' }),
      } },
      { kind: 'delivery.queued', outboxId: 1, status: 'PENDING', summary: '已完成' },
      { kind: 'delivery.sent', outboxId: 1, status: 'SENT', summary: '已完成',
        metadata: { connector, action: 'message.reply' } },
      { kind: 'agent.tool', status: 'COMPLETED', metadata: { toolId: 'reply', output: 'ok' } },
      { kind: 'agent.tool', status: 'COMPLETED', metadata: { toolName: 'bash',
        input: 'mcp__personal_agent__reply_message', output: 'ok' } },
      { kind: 'delivery.uncertain', outboxId: 2, status: 'UNCERTAIN', summary: 'timeout' },
      { kind: 'agent.turn_completed', status: 'completed' },
    ].map((row, i) => ({ id: i + 1, createdAtMs: i * 100, ...row }));
    const turn = buildModel(rows).turns[0];
    const deliveries = turn.steps.filter((step) => step.type === 'tool' && deliveryOf(step));
    assert.equal(deliveries.length, 1);
    assert.equal(deliveryOf(deliveries[0]).mode, '回复');
    assert.equal(deliveryOf(deliveries[0]).text, '已完成');
    assert.equal(turn.steps.filter((step) => step.type === 'tool').length, 3);
  }
});

test('明确不回复进入输出区，回合后的「本轮未表态」挂回原回合', () => {
  const { buildModel, deliveryOf } = runInNewContext(
    js.slice(0, js.indexOf('// ---------- 渲染：上下文流')) + '\n({ buildModel, deliveryOf });',
    { document: { getElementById: () => ({}) } },
  );
  const rows = [
    { kind: 'message.received', inboundSeq: 1, summary: '谢谢' },
    { kind: 'agent.turn_started', metadata: { inboundSeqs: [1] } },
    { kind: 'delivery.silent', inboundSeq: 1, status: 'SILENT', summary: 'chatter between others',
      metadata: { tool: 'stay_silent', decisionSeq: 1 } },
    { kind: 'agent.turn_completed', status: 'completed' },
    { kind: 'message.received', inboundSeq: 2, summary: '在吗' },
    { kind: 'agent.turn_started', metadata: { inboundSeqs: [2] } },
    { kind: 'agent.turn_completed', status: 'completed' },
    { kind: 'agent.decision_missing', inboundSeq: 2, status: 'NUDGED', severity: 'warn' },
  ].map((row, i) => ({ id: i + 1, createdAtMs: i * 100, ...row }));
  const model = buildModel(rows);
  const [silent, missed] = model.turns;
  const deliveries = silent.steps.filter((step) => step.type === 'tool' && deliveryOf(step));
  assert.equal(deliveries.length, 1);
  assert.equal(deliveryOf(deliveries[0]).mode, '不回复');
  assert.equal(deliveryOf(deliveries[0]).text, 'chatter between others');
  assert.equal(deliveryOf(deliveries[0]).silent, true);
  assert.equal(JSON.stringify(missed.steps.map((step) => [step.type, step.row.kind])), '[["event","agent.decision_missing"]]');
  assert.equal(model.items.some((item) => item.type === 'row'), false);
  // The page cannot tell whether the instance has explicit silence on, so
  // the empty output keeps its wording.
  assert.match(js, /'没有发出消息（静默或仅模型文本）'/);
  assert.match(js, /delivery\.silent \? 'muted' : 'out'/);
});
