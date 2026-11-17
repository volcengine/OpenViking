// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { MAX_CARD_BYTES, cardBytes, describeElapsed, fitCard, isLive, isRetryable, taskCard } from '../card.mjs';

const NOW = Date.UTC(2026, 10, 17, 9, 40);
const task = {
  id: 't331',
  title: '实现飞书状态卡片',
  status: 'running',
  requesterId: 'ou_requester0001',
  requesterName: '瑞士卷',
  startedAt: NOW - 7 * 60_000,
  progress: ['a', 'b'],
};

const buttons = (card) => card.elements.find((element) => element.tag === 'action')?.actions ?? [];

describe('taskCard', () => {
  it('colours the header by status and marks finished states', () => {
    const template = (status) => taskCard({ ...task, status }, { now: NOW }).header.template;
    assert.equal(template('queued'), 'wathet');
    assert.equal(template('running'), 'blue');
    assert.equal(template('waiting'), 'orange');
    assert.equal(template('done'), 'green');
    assert.equal(template('failed'), 'red');
    assert.equal(template('stopped'), 'grey');
    assert.equal(isLive('waiting'), true);
    assert.equal(isLive('done'), false);
    assert.equal(isRetryable('failed'), true);
    assert.equal(isRetryable('done'), false);
    assert.equal(template('something_new'), 'grey');
  });

  it('offers stop while the task runs and retry only after it failed or stopped', () => {
    assert.deepEqual(buttons(taskCard(task, { now: NOW })).map((button) => button.value), [
      { superagent_task: 'stop', task_id: 't331' },
    ]);
    const failed = taskCard({ ...task, status: 'failed' }, { now: NOW });
    assert.deepEqual(buttons(failed).map((button) => button.text.content), ['重试']);
    assert.equal(buttons(taskCard({ ...task, status: 'done' }, { now: NOW })).length, 0);
  });

  it('keeps the button row when a card is settled but drops its buttons', () => {
    const settled = taskCard({ ...task, status: 'stopped' }, { now: NOW, settled: true, note: '已重新派发为 t332' });
    assert.equal(buttons(settled).length, 0);
    assert.match(JSON.stringify(settled), /已重新派发为 t332/);
  });

  it('needs update_multi, otherwise the patch is accepted and ignored', () => {
    assert.equal(taskCard(task, { now: NOW }).config.update_multi, true);
  });

  it('escapes markdown so a title cannot restyle the body', () => {
    const card = taskCard({ ...task, title: '**x** `y` <at id=all>', requesterName: 'a_b' }, { now: NOW });
    const body = card.elements[0].text.content;
    assert.equal(body.includes('**x** `y`'), false);
    assert.match(body, /\\\*\\\*x\\\*\\\*/);
    assert.match(card.elements[1].text.content, /a\\_b/);
  });

  it('shows the waiting question above progress', () => {
    const card = taskCard({ ...task, status: 'waiting', question: '要继续吗？' }, { now: NOW });
    const lines = card.elements.map((element) => element.text?.content ?? '');
    assert.match(lines[2], /等待你的回答/);
    assert.match(lines[3], /^a\nb$/);
  });

  it('fits the 30 KiB limit by dropping old progress, not by failing', () => {
    const long = { ...task, progress: Array.from({ length: 300 }, (_, i) => `${i} ${'x'.repeat(300)}`) };
    const card = fitCard(long, { now: NOW });
    assert.ok(cardBytes(card) <= MAX_CARD_BYTES);
    assert.ok(card.elements.some((element) => element.text?.content?.includes('299 ')));
    const tiny = fitCard({ ...task, title: 'x'.repeat(4000) }, { now: NOW });
    assert.ok(cardBytes(tiny) <= MAX_CARD_BYTES);
  });

  it('describes elapsed time in whole units', () => {
    assert.equal(describeElapsed(30_000), '不到 1 分钟');
    assert.equal(describeElapsed(7 * 60_000), '7 分钟');
    assert.equal(describeElapsed((2 * 60 + 5) * 60_000), '2 小时 5 分钟');
    assert.equal(describeElapsed(Number.NaN), '');
  });
});
