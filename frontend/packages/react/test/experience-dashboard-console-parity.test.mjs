import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import { mount } from './helpers/module-dom.mjs';

test('loading renders a skeleton that still states the state as text', async () => {
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'loading-sources',
      kind: 'file',
      tab: 'source',
      states: { sources: { loading: true } },
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
    assert.doesNotMatch(view.container.textContent, /No source records/);
  } finally {
    await view.unmount();
  }
});

test('the directory table holds its page height with a table skeleton', async () => {
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'loading-table',
      kind: 'directory',
      pageSize: 20,
      states: { list: { loading: true } },
    }),
  );
  try {
    // 目录表用表格骨架：表头 + 每页行数。通用占位条只有一百多像素，20 行的表是
    // 1700 多像素，数据到达时页面会突然长高。
    //
    // The directory table uses a table skeleton — a header plus one row per page slot.
    // Generic bars are a hundred-odd pixels against 1700+ for 20 rows, so the page
    // would jump when the data lands.
    const table = view.container.querySelector(
      '.ov-experience-dashboard__table-skeleton',
    );
    assert.ok(table, 'the directory view gets the table skeleton');
    assert.equal(table.getAttribute('aria-busy'), 'true');
    assert.equal(
      table.querySelector('[role="status"]').textContent,
      'Loading…',
    );
    assert.ok(
      table.querySelector('.ov-experience-dashboard__table-skeleton-head'),
      'the header row is drawn',
    );
    assert.equal(
      table.querySelectorAll('.ov-experience-dashboard__table-skeleton-row')
        .length,
      20,
      'one placeholder row per page slot',
    );
    // 通用骨架不该出现在目录视图里。
    //
    // The generic skeleton must not appear in the directory view.
    assert.equal(
      view.container.querySelector('.ov-experience-dashboard__skeleton'),
      null,
    );
  } finally {
    await view.unmount();
  }
});

test('a short final page draws only the rows it will hold', async () => {
  const render = (props) =>
    h(ExperienceDashboard, {
      uri: 'loading-last-page',
      kind: 'directory',
      pageSize: 20,
      states: { list: { loading: true } },
      ...props,
    });
  const rows = (view) =>
    view.container.querySelectorAll(
      '.ov-experience-dashboard__table-skeleton-row',
    ).length;
  const view = await mount(render({}));
  try {
    // 总数未知时退回每页条数。
    //
    // An unknown total falls back to the page size.
    assert.equal(rows(view), 20);

    // 第 2 页只剩 1 行：骨架画 1 行，加载完成时高度不变；画满 20 行会让下面
    // 的内容被往上拉一千多像素。
    //
    // One row left on page 2: the skeleton draws one, so the height holds when the data
    // lands; twenty would drag everything below up by a thousand-odd pixels.
    await view.render(render({ page: 1, data: { total: 21 } }));
    assert.equal(rows(view), 1);

    // 整页不满但仍有多行时按实际剩余行数画。
    //
    // A partially filled page with more than one row left draws exactly those rows.
    await view.render(render({ page: 1, data: { total: 25 } }));
    assert.equal(rows(view), 5);

    // 满页仍是每页条数。
    //
    // A full page still draws the page size.
    await view.render(render({ page: 0, data: { total: 45 } }));
    assert.equal(rows(view), 20);

    // 页码越界时不会画负数行。
    //
    // A page past the end draws no rows rather than a negative count.
    await view.render(render({ page: 9, data: { total: 21 } }));
    assert.equal(rows(view), 0);
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
