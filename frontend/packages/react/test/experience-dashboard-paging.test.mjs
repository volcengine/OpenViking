import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import { mount } from './helpers/module-dom.mjs';

test('load-more is disabled without onPageChange and tolerates a bare percent in the URI', async () => {
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'file',
      kind: 'file',
      data: {
        trajectories: [
          {
            uri: 'viking://user/alice/memories/trajectories/discount-50%.md',
            name: '',
          },
        ],
        trajectoryTotal: 1,
      },
      hasMore: true,
    }),
  );
  try {
    // 省略 onPageChange 时按钮必须禁用：以前这里始终传一个包装函数，disabled 永远不成立。
    //
    // Without onPageChange the button must be disabled; it used to receive a wrapper,
    // so `disabled` never applied.
    const loadMore = view.container.querySelector(
      '.ov-experience-dashboard__load-more-action',
    );
    assert.ok(loadMore);
    assert.equal(loadMore.disabled, true);

    // 文件名里的裸 % 会让 decodeURIComponent 抛错；回退后必须原样显示，而不是白屏。
    //
    // A bare % makes decodeURIComponent throw; the fallback must show the name as-is
    // rather than blanking the board.
    assert.equal(
      view.container.querySelector('.ov-experience-dashboard__trajectory-name')
        .textContent,
      'discount-50%.md',
    );
  } finally {
    await view.unmount();
  }
});

test('pagination names follow the interface language', async () => {
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'paged-zh',
      kind: 'directory',
      locale: 'zh-CN',
      data: { experiences: [{ uri: 'a.md', name: 'a.md' }], total: 200 },
      pageSize: 20,
    }),
  );
  try {
    const labels = [...view.container.querySelectorAll('.ov-pagination__item')]
      .map((item) => item.getAttribute('aria-label'))
      .filter(Boolean);
    // 中文界面下页码与跳页的读屏名称都应是中文。
    //
    // Under zh-CN both the page and jump names must be Chinese.
    assert.ok(labels.includes('第 1 页'), labels.join(' | '));
    assert.ok(
      labels.some((label) => label.includes('跳转')),
      labels.join(' | '),
    );
    assert.ok(
      !labels.some((label) => /Page|Jump/.test(label)),
      labels.join(' | '),
    );
  } finally {
    await view.unmount();
  }
});
