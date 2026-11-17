// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  MAX_CARD_BYTES, cardBytes, describeElapsed, isLive, isRetryable, normalizeTask, taskCard,
} from '../card.mjs';

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
const texts = (card) => card.elements.flatMap((element) => [element.text, ...(element.elements ?? [])])
  .filter(Boolean).map((item) => item.content ?? '');

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
    assert.deepEqual(buttons(taskCard({ ...task, status: 'failed' }, { now: NOW })).map((button) => button.text.content), ['重试']);
    assert.equal(buttons(taskCard({ ...task, status: 'done' }, { now: NOW })).length, 0);
  });

  it('keeps a settled card but drops its buttons', () => {
    const settled = taskCard({ ...task, status: 'stopped' }, { now: NOW, settled: true, note: '已重新派发为 t332' });
    assert.equal(buttons(settled).length, 0);
    assert.ok(texts(settled).some((line) => line.includes('已重新派发为 t332')));
  });

  it('needs update_multi, otherwise the patch is accepted and ignored', () => {
    assert.deepEqual(taskCard(task, { now: NOW }).config, { wide_screen_mode: true, update_multi: true });
  });

  it('renders every text as plain text, so a title cannot restyle the card', () => {
    const card = taskCard({ ...task, title: '**x** `y` <at id=all>', requesterName: 'a_b' }, { now: NOW });
    const tags = new Set();
    const walk = (value) => {
      if (!value || typeof value !== 'object') return;
      if (value.tag) tags.add(value.tag);
      for (const child of Object.values(value)) walk(child);
    };
    walk(card);
    assert.equal(tags.has('lark_md'), false);
    assert.ok(texts(card).includes('**x** `y` <at id=all>'));
    assert.ok(texts(card).some((line) => line.startsWith('请求人：a_b')));
  });

  it('shows the waiting question above progress', () => {
    const card = taskCard({ ...task, status: 'waiting', question: '要继续吗？' }, { now: NOW });
    assert.equal(texts(card)[2], '等待你的回答：要继续吗？');
    assert.equal(texts(card)[3], 'a\nb');
  });

  it('describes elapsed time in whole units', () => {
    assert.equal(describeElapsed(30_000), '不到 1 分钟');
    assert.equal(describeElapsed(7 * 60_000), '7 分钟');
    assert.equal(describeElapsed((2 * 60 + 5) * 60_000), '2 小时 5 分钟');
    assert.equal(describeElapsed(Number.NaN), '');
  });
});

describe('normalizeTask', () => {
  it('reads the scheduler row as well as a camelCase task', () => {
    const row = normalizeTask({ id: 't1', title: 'x', status: 'running', requester_id: 'ou_a', created_at_ms: 5, summary: 'did a thing' });
    assert.equal(row.requesterId, 'ou_a');
    assert.equal(row.startedAt, 5);
    assert.deepEqual(row.progress, ['did a thing']);
    assert.equal(normalizeTask({ id: 't1', requesterId: 'ou_b' }).requesterId, 'ou_b');
  });

  it('renders a scheduler row without camelCase fields', () => {
    const card = taskCard({ id: 't1', title: 'x', status: 'running', requester_name: '张三', created_at_ms: NOW - 60_000, summary: '半成品' },
      { now: NOW });
    assert.ok(texts(card).includes('请求人：张三 · 已进行：1 分钟'));
    assert.ok(texts(card).includes('半成品'));
  });
});

describe('size', () => {
  it('keeps the newest progress within its budget, drop-oldest', () => {
    const long = { ...task, progress: Array.from({ length: 300 }, (_, i) => `${i} ${'x'.repeat(300)}`) };
    const card = taskCard(long, { now: NOW });
    assert.ok(texts(card).some((line) => line.includes('299 ')), 'the newest step survives');
    assert.ok(!texts(card).some((line) => line.includes('0 ')), 'the oldest step does not');
  });

  it('caps a long title and an enormous progress entry', () => {
    const card = taskCard({ ...task, title: 'x'.repeat(4000), progress: ['y'.repeat(50_000)] }, { now: NOW });
    const [title] = texts(card);
    const progress = texts(card).find((line) => line.startsWith('y'));
    assert.ok(title.length <= 121);
    assert.ok(progress.length <= 1201);
    assert.ok(cardBytes(card) < 4_000, 'a rendered card is far below the 30 KiB limit');
  });

  it('stays valid JSON under the limit for generated tasks', () => {
    // Deterministic pseudo-random text, including the characters that used to
    // be able to restyle the card.
    let seed = 7;
    const next = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
    const pick = (chars, max) => Array.from({ length: Math.floor(next() * max) },
      () => chars[Math.floor(next() * chars.length)]).join('');
    const alphabet = '**__~~`<>[]()中文abc \n\t!';
    for (const status of ['queued', 'running', 'waiting', 'blocked', 'done', 'failed', 'stopped', 'unknown']) {
      const card = taskCard({ id: 't1', title: pick(alphabet, 400), status, requesterName: pick(alphabet, 80),
        question: pick(alphabet, 400), progress: Array.from({ length: 20 }, () => pick(alphabet, 400)),
        startedAt: NOW - 90_000 }, { now: NOW, note: pick(alphabet, 800), footer: pick(alphabet, 300) });
      assert.ok(cardBytes(card) <= MAX_CARD_BYTES);
      assert.doesNotThrow(() => JSON.parse(JSON.stringify(card)));
      assert.equal(JSON.stringify(card).includes('lark_md'), false);
    }
  });

  it('never renders past the platform limit, whatever the task holds', () => {
    const worst = taskCard({ id: 't1', title: '标'.repeat(5000), status: 'waiting', requesterName: '名'.repeat(500),
      question: '问'.repeat(5000), progress: Array.from({ length: 100 }, () => '进'.repeat(2000)),
      startedAt: NOW }, { now: NOW, note: '注'.repeat(2000), footer: '脚'.repeat(2000) });
    assert.ok(cardBytes(worst) <= MAX_CARD_BYTES);
  });
});
