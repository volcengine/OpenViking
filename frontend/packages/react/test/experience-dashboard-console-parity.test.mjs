import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import { mount } from './helpers/module-dom.mjs';

test('loading renders a skeleton that still states the state as text', async () => {
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'loading-list',
      kind: 'directory',
      states: { list: { loading: true } },
    }),
  );
  try {
    const skeleton = view.container.querySelector(
      '.ov-experience-dashboard__skeleton',
    );
    assert.ok(skeleton, 'the skeleton replaces the empty state while loading');
    assert.equal(skeleton.getAttribute('aria-busy'), 'true');
    // 占位条对读屏器隐藏，加载状态由视觉隐藏的一行给出。
    //
    // The bars are hidden from assistive technology; a visually hidden line states it.
    assert.equal(skeleton.querySelector('[aria-hidden="true"]') !== null, true);
    assert.equal(
      skeleton.querySelector('[role="status"]').textContent,
      'Loading…',
    );
    assert.doesNotMatch(view.container.textContent, /No experiences/);
  } finally {
    await view.unmount();
  }
});

test('a failed metric request keeps the distribution and timeline on screen', async () => {
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'effect-error',
      kind: 'file',
      data: {
        outcomes: [{ outcome: 'success', count: 3 }],
        trajectories: [
          { uri: 'viking://user/a/memories/trajectories/t.md', name: 'T' },
        ],
        trajectoryTotal: 1,
      },
      states: {
        effect: { error: new Error('Metrics unavailable'), onRetry: () => {} },
      },
    }),
  );
  try {
    // 三项指标自己取数：失败只换掉指标那一块，结果分布与轨迹照常显示。
    //
    // The metrics fetch on their own: a failure replaces only that block while the
    // distribution and the timeline still render.
    assert.equal(view.container.querySelectorAll('dl').length, 0);
    const state = view.container.querySelector(
      '.ov-experience-dashboard__metrics-state',
    );
    assert.ok(state);
    assert.match(state.textContent, /Could not load data/);
    assert.match(view.container.textContent, /Task success/);
    assert.equal(
      view.container.querySelector('.ov-experience-dashboard__trajectory-name')
        .textContent,
      'T',
    );
  } finally {
    await view.unmount();
  }
});

test('an updated experience carries the tag while a plain one does not', async () => {
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'updated-list',
      kind: 'directory',
      data: {
        experiences: [
          { uri: 'a.md', name: 'a.md', isUpdated: true },
          { uri: 'b.md', name: 'b.md' },
        ],
      },
    }),
  );
  try {
    const tags = [
      ...view.container.querySelectorAll(
        '.ov-experience-dashboard__updated-tag',
      ),
    ];
    assert.equal(tags.length, 1);
    assert.equal(tags[0].textContent, 'Updated');
    const rows = [
      ...view.container.querySelectorAll('.ov-experience-dashboard__table-row'),
    ];
    assert.ok(rows[0].contains(tags[0]));
    assert.equal(
      rows[1].querySelector('.ov-experience-dashboard__updated-tag'),
      null,
    );
  } finally {
    await view.unmount();
  }
});

test('a metric header hint opens on focus instead of waiting for a native title', async () => {
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'hint-list',
      kind: 'directory',
      data: { experiences: [{ uri: 'a.md', name: 'a.md' }] },
    }),
  );
  try {
    // 说明触发器是按钮：键盘可达，读屏器按控件朗读；原生 title 两者都做不到。
    //
    // The hint trigger is a button: reachable by keyboard and announced as a control,
    // neither of which a native title offers.
    const hints = [
      ...view.container.querySelectorAll(
        '.ov-experience-dashboard__hint-label',
      ),
    ];
    assert.deepEqual(
      hints.map((hint) => hint.tagName),
      ['BUTTON', 'BUTTON'],
    );
    assert.deepEqual(
      hints.map((hint) => hint.textContent),
      ['Context injections', 'Experience recalls'],
    );
    assert.equal(
      view.container.querySelector('[role="columnheader"][title]'),
      null,
    );
    // 悬停提示有 300 毫秒延迟，聚焦后等它出现。
    //
    // The hint waits 300 ms before showing; wait for it after focusing.
    await act(async () => {
      hints[0].focus();
      await new Promise((resolve) => setTimeout(resolve, 350));
    });
    const surface = [...document.querySelectorAll('.ov-tooltip')].find((node) =>
      node.textContent?.startsWith('Number of times'),
    );
    assert.ok(surface, 'focusing the hint shows its explanation');
  } finally {
    await view.unmount();
  }
});
