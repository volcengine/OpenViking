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
    // 停在底部不重复请求；再往下滚一段才会请求下一页。
    //
    // Resting at the bottom does not ask again; scrolling further does.
    await act(async () => {
      body.dispatchEvent(new window.Event('scroll'));
    });
    assert.deepEqual(pages, ['more']);
    body.scrollTop = 700;
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
