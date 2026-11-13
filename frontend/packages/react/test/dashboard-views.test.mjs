import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { WikiDashboard } from '@openviking/react/components/wiki-dashboard';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import { mount } from './helpers/module-dom.mjs';

test('dashboard views render supplied states without a Provider or requests', async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = () => {
    calls++;
    throw new Error('Views must not request data');
  };
  let view;
  try {
    view = await mount(h(WikiDashboard, { kind: 'file', loading: true }));
    assert.equal(
      view.container.querySelector('[aria-busy]').getAttribute('aria-busy'),
      'true',
    );
    assert.match(view.container.textContent, /Pending conflicts/);
    assert.ok(view.container.querySelector('[aria-haspopup="menu"]'));
    await view.render(
      h(WikiDashboard, {
        kind: 'file',
        data: { conflicts: [] },
      }),
    );
    assert.match(view.container.textContent, /No matching conflicts/);
    assert.equal(view.container.querySelector('dd').textContent, '0');
    await view.render(h(WikiDashboard, { kind: 'directory', loading: true }));
    assert.match(view.container.textContent, /Conflicting files/);
    assert.equal(view.container.querySelector('[aria-haspopup="menu"]'), null);
    await view.render(
      h(ExperienceDashboard, { uri: 'experience-view', kind: 'file' }),
    );
    assert.match(view.container.textContent, /No distribution data/);
    assert.doesNotMatch(view.container.textContent, /Provider/);
    assert.equal(calls, 0);
  } finally {
    if (view) await view.unmount();
    globalThis.fetch = original;
  }
});

test('dashboard labels inherit undefined defaults and preserve explicit empty strings', async () => {
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'id',
      kind: 'file',
      tab: 'source',
      data: { sources: [{ uri: 'source', name: 'source' }] },
      labels: { sourceCount: undefined, latest: '' },
    }),
  );
  try {
    assert.match(view.container.textContent, /Generated from 1 trajectories/);
    assert.equal(
      view.container.querySelector('summary strong').textContent,
      '',
    );
    await view.render(
      h(WikiDashboard, {
        kind: 'file',
        data: { conflicts: [] },
        labels: { title: undefined, severity: undefined, conflicts: '' },
      }),
    );
    assert.equal(
      view.container.querySelector('h2').textContent,
      'Generation results',
    );
    assert.equal(view.container.querySelector('h3').textContent, '');
    assert.match(
      view.container
        .querySelector('[aria-haspopup="menu"]')
        .getAttribute('aria-label'),
      /Severity/,
    );
  } finally {
    await view.unmount();
  }
});

test('asynchronous test materials preserve valid experience selections and date range', async () => {
  const { act } = await import('react');
  const events = [];
  const render = (data = {}) =>
    h(ExperienceDashboard, {
      uri: 'same-resource',
      kind: 'file',
      data,
      onTabChange: (value) => events.push(value),
    });
  const view = await mount(render());
  const selectTab = (name) =>
    act(async () =>
      [...view.container.querySelectorAll('[role="tab"]')]
        .find((el) => el.textContent === name)
        .click(),
    );
  const active = () =>
    view.container.querySelector('[role="tab"][aria-selected="true"]')
      .textContent;
  const rangeLabel = () =>
    view.container.querySelector('.ov-experience-dashboard__range-trigger')
      .textContent;
  try {
    // 时间范围改成了浮层里的快捷区间：先点触发器，再选「最近 30 天」。
    //
    // The range became a shortcut list in a popover: open the trigger, then pick
    // "Last 30 days".
    await act(async () =>
      view.container
        .querySelector('.ov-experience-dashboard__range-trigger')
        .click(),
    );
    const option = [
      ...document.querySelectorAll('[role="menuitemradio"]'),
    ].find((el) => el.textContent === 'Last 30 days');
    assert.ok(option, 'the range popover lists the 30-day shortcut');
    await act(async () => option.click());
    assert.equal(rangeLabel(), 'Last 30 days');
    await selectTab('Source trace');
    await view.render(render({ testMaterials: [] }));
    assert.equal(active(), 'Source trace');
    await view.render(render());
    assert.equal(active(), 'Source trace');
    assert.deepEqual(events, ['source']);
    await selectTab('Application impact');
    assert.equal(rangeLabel(), 'Last 30 days');
    await view.render(render({ testMaterials: [] }));
    await selectTab('Test');
    await view.render(render());
    assert.equal(active(), 'Application impact');
    assert.equal(rangeLabel(), 'Last 30 days');
    await view.render(render({ testMaterials: [] }));
    assert.equal(active(), 'Application impact');
  } finally {
    await view.unmount();
  }
});

test('the directory pager counts from total while impact offers load-more', async () => {
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'paged',
      kind: 'directory',
      data: {
        experiences: [{ uri: 'a.md', name: 'a.md' }],
        total: 45,
      },
      pageSize: 20,
    }),
  );
  try {
    // 45 条、每页 20 条 => 3 页，第 1 页选中，末页可点。
    //
    // 45 rows at 20 per page => 3 pages, the first current and the last clickable.
    const pages = [...view.container.querySelectorAll('.ov-pagination__item')];
    assert.deepEqual(
      pages.map((item) => item.textContent),
      ['', '1', '2', '3', ''],
    );
    assert.equal(
      view.container.querySelector('[aria-current="page"]').textContent,
      '1',
    );
    await view.render(
      h(ExperienceDashboard, {
        uri: 'file',
        kind: 'file',
        data: {
          trajectories: [{ uri: 'trajectory.md', name: 'Trajectory' }],
          trajectoryTotal: 2,
        },
        hasMore: true,
        onPageChange: () => {},
      }),
    );
    // 「应用效果」的轨迹按游标翻页：出现加载更多，不出现页码器。
    //
    // Impact's trajectories page by cursor: load-more appears, the pager does not.
    assert.equal(view.container.querySelector('.ov-pagination'), null);
    assert.equal(
      view.container.querySelector('.ov-experience-dashboard__load-more-action')
        .textContent,
      'Scroll down to load more',
    );
  } finally {
    await view.unmount();
  }
});
