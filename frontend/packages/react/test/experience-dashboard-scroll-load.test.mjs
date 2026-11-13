import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import { mount } from './helpers/module-dom.mjs';

// jsdom 不做布局：把内容区摆成可滚到底的样子，再派发滚动事件。
//
// jsdom lays nothing out, so pose the body as a scroller and dispatch scroll events.
const poseScroller = (view) => {
  const body = view.container.querySelector('.ov-experience-dashboard__body');
  body.style.overflowY = 'auto';
  Object.defineProperty(body, 'scrollHeight', {
    value: 1000,
    configurable: true,
  });
  Object.defineProperty(body, 'clientHeight', {
    value: 400,
    configurable: true,
  });
  return body;
};

test('reaching the bottom of the timeline loads the next page, the button stays', async () => {
  const pages = [];
  const trajectories = Array.from({ length: 20 }, (_, index) => ({
    uri: `viking://user/a/memories/trajectories/t${index}.md`,
    name: `T${index}`,
  }));
  const render = (data) =>
    h(ExperienceDashboard, {
      uri: 'scroll-load',
      kind: 'file',
      data,
      hasMore: true,
      onLoadMore: () => pages.push('more'),
    });
  // 先挂空列表，把内容区伪装成可滚动容器，再渲染轨迹：滚动容器在订阅时就要
  // 已确定，晚于订阅再加样式不会被重新识别。
  //
  // Mount the empty list first, pose the body as a scroller, then render the rows: the
  // scroll container is resolved when subscribing, so a style added afterwards is not
  // picked up.
  const view = await mount(render({}));
  try {
    const body = poseScroller(view);
    await view.render(render({ trajectories, trajectoryTotal: 40 }));
    const loadMore = view.container.querySelector(
      '.ov-experience-dashboard__load-more',
    );
    assert.ok(loadMore, 'the sentinel sits on the load-more row');
    // jsdom 不做布局：把内容区摆成已滚到底的样子再派发滚动事件。
    //
    // jsdom lays nothing out, so place the body at its end and dispatch a scroll.
    body.scrollTop = 600;
    await act(async () => {
      body.dispatchEvent(new window.Event('scroll'));
    });
    assert.deepEqual(pages, ['more']);
    // 停在底部不重复请求。
    //
    // Resting at the bottom does not ask again.
    await act(async () => {
      body.dispatchEvent(new window.Event('scroll'));
    });
    assert.deepEqual(pages, ['more']);
    // 往回滚再回到末尾（容器最大滚动位置就是 600）会再请求一次：失败时内容不会
    // 变高，只有这样才能重试同一页。
    //
    // Scrolling back up and returning to the end — 600 is this container's maximum —
    // asks again: after a failure the content does not grow, and this is what lets the
    // same page be retried.
    body.scrollTop = 550;
    await act(async () => {
      body.dispatchEvent(new window.Event('scroll'));
    });
    assert.deepEqual(pages, ['more'], 'scrolling up alone does not load');
    body.scrollTop = 600;
    await act(async () => {
      body.dispatchEvent(new window.Event('scroll'));
    });
    assert.deepEqual(pages, ['more', 'more']);
    // 再停在原地也不会继续请求。
    //
    // Resting there again does not keep asking.
    await act(async () => {
      body.dispatchEvent(new window.Event('scroll'));
    });
    assert.deepEqual(pages, ['more', 'more']);
    // 滚动加载之外，按钮仍然可用，键盘用户不依赖滚动。
    //
    // Beyond scroll-loading the button stays usable, so keyboard users do not depend on
    // scrolling.
    assert.equal(
      view.container.querySelector('.ov-experience-dashboard__load-more-action')
        .disabled,
      false,
    );
  } finally {
    await view.unmount();
  }
});

test('a successful append re-arms without scrolling back up', async () => {
  const pages = [];
  const render = (data) =>
    h(ExperienceDashboard, {
      uri: 'scroll-grow',
      kind: 'file',
      data,
      hasMore: true,
      onLoadMore: () => pages.push('more'),
    });
  const row = (n) => ({
    uri: `viking://user/a/memories/trajectories/t${n}.md`,
    name: `T${n}`,
  });
  const view = await mount(render({}));
  try {
    const body = poseScroller(view);
    await view.render(render({ trajectories: [row(0)], trajectoryTotal: 40 }));
    // 在触发区内请求下一页。
    //
    // Ask for the next page from inside the trigger zone.
    body.scrollTop = 400;
    await act(async () => {
      body.dispatchEvent(new window.Event('scroll'));
    });
    assert.deepEqual(pages, ['more']);

    // 追加成功：内容变高，底部下移。用户不往回滚，直接滚到新的底部——此时仍在
    // 触发区内，只有「内容变高」这一条能让它再请求一次。
    //
    // The append succeeds: the content grows and the end moves down. The user scrolls
    // straight to the new bottom without going back up, staying inside the trigger zone;
    // only the growth can let it ask again.
    Object.defineProperty(body, 'scrollHeight', {
      value: 1400,
      configurable: true,
    });
    await view.render(
      render({ trajectories: [row(0), row(1)], trajectoryTotal: 40 }),
    );
    body.scrollTop = 1000;
    await act(async () => {
      body.dispatchEvent(new window.Event('scroll'));
    });
    assert.deepEqual(pages, ['more', 'more']);

    // 内容没再变高时，停在新的底部不会继续请求。
    //
    // With no further growth, resting at the new bottom does not keep asking.
    await act(async () => {
      body.dispatchEvent(new window.Event('scroll'));
    });
    assert.deepEqual(pages, ['more', 'more']);
  } finally {
    await view.unmount();
  }
});

test('scrolling does not load when there is nothing more', async () => {
  const pages = [];
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'scroll-end',
      kind: 'file',
      data: {
        trajectories: [
          { uri: 'viking://user/a/memories/trajectories/t.md', name: 'T' },
        ],
        trajectoryTotal: 1,
      },
      onLoadMore: () => pages.push('more'),
    }),
  );
  try {
    const body = poseScroller(view);
    body.scrollTop = 600;
    await act(async () => {
      body.dispatchEvent(new window.Event('scroll'));
    });
    assert.deepEqual(pages, []);
  } finally {
    await view.unmount();
  }
});
