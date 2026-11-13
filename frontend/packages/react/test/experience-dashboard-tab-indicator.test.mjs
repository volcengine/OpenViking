import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import { mount } from './helpers/module-dom.mjs';

const render = (uri) => h(ExperienceDashboard, { uri, kind: 'file', data: {} });

const indicator = (view) =>
  view.container.querySelector('.ov-experience-dashboard__tab-indicator');

const activeTitle = (view) =>
  view.container.querySelector(
    '[role="tab"][aria-selected="true"] .ov-experience-dashboard__tab-title',
  );

/**
 * 让测试环境量出真实尺寸：jsdom 不做布局，元素的盒宽与偏移默认都是 0，而指示条
 * 的位置正是按标题的盒算出来的。这里给标题与页签栏按文档顺序摆出固定几何。
 *
 * Give the environment real geometry: jsdom lays nothing out, so boxes are 0 wide and
 * unpositioned by default, while the indicator's position is computed from the title's
 * box. This places the titles and the bar in document order.
 */
function layout(view) {
  const list = view.container.querySelector('.ov-experience-dashboard__tabs');
  const titles = [
    ...list.querySelectorAll('.ov-experience-dashboard__tab-title'),
  ];
  let left = 0;
  const place = (node, width) => {
    node.getBoundingClientRect = () => ({
      left,
      right: left + width,
      width,
      top: 0,
      bottom: 34,
      height: 34,
      x: left,
      y: 0,
      toJSON() {},
    });
    left += width;
  };
  list.getBoundingClientRect = () => ({
    left: 0,
    right: left,
    width: left,
    top: 0,
    bottom: 34,
    height: 34,
    x: 0,
    y: 0,
    toJSON() {},
  });
  // 图标与内边距各占 40，文字宽度按标题长度给一个固定值。
  for (const title of titles) {
    left += 40;
    place(title, 48);
    left += 12;
  }
  return { list, titles };
}

test('the indicator centres on the title text, not the whole tab', async () => {
  const view = await mount(render('indicator-centre'));
  try {
    const { list, titles } = layout(view);
    // 几何变了要重新测量：ResizeObserver 的替身不回调，这里手动触发一次重渲染。
    await view.render(render('indicator-centre'));
    const bar = indicator(view);
    assert.ok(bar, 'the indicator mounts once its position is known');
    const barBox = bar.getBoundingClientRect();
    const titleBox = titles[0].getBoundingClientRect();
    // 指示条是相对页签栏绝对定位的，位置来自 CSS 变量。
    const offset = Number.parseFloat(
      list.style.getPropertyValue('--ov-experience-indicator-left'),
    );
    assert.equal(
      offset,
      titleBox.left + titleBox.width / 2 - 8,
      'the bar is centred on the first title',
    );
    // 首个页签带图标，按按钮居中会落在标题左侧 24px 处。
    assert.notEqual(
      offset,
      barBox.left,
      'the bar is not pinned to the bar start',
    );
    assert.ok(titleBox.width > 0);
  } finally {
    await view.unmount();
  }
});

test('the indicator stays out of the tree until a title has width', async () => {
  const view = await mount(render('indicator-mount'));
  try {
    // 宽度为 0 时（未布局、被隐藏、页签栏在折叠面板里）不挂指示条：与其在左边缘
    // 显示一条位置错误的横线，不如先不显示。
    //
    // With a zero-width title — not laid out, hidden, or inside a collapsed panel — the
    // indicator stays unmounted: better nothing than a bar sitting at the wrong place on
    // the left edge.
    assert.equal(indicator(view), null);
    layout(view);
    await view.render(render('indicator-mount'));
    // 有宽度之后才挂上，且首帧就带最终坐标，没有从左侧滑入的过程。
    //
    // It mounts once the title has width, and its first frame already carries the final
    // coordinate, so nothing slides in from the left.
    assert.ok(indicator(view));
    assert.equal(
      indicator(view).getAnimations?.()?.length ?? 0,
      0,
      'no transition runs on the first frame',
    );
  } finally {
    await view.unmount();
  }
});

test('switching tabs moves the same indicator element', async () => {
  const view = await mount(render('indicator-switch'));
  try {
    const { titles } = layout(view);
    await view.render(render('indicator-switch'));
    const before = indicator(view);
    assert.ok(before);
    const target = [...view.container.querySelectorAll('[role="tab"]')].find(
      (tab) => tab.getAttribute('aria-selected') !== 'true',
    );
    await act(async () => target.click());
    const after = indicator(view);
    // 整条页签栏只有一个指示条，切换时不重新挂载，位置改由 CSS 变量驱动。
    //
    // The bar holds a single indicator; a switch does not remount it, only its CSS
    // variable changes.
    assert.equal(after, before);
    const list = view.container.querySelector('.ov-experience-dashboard__tabs');
    const titleBox = titles[1].getBoundingClientRect();
    assert.equal(
      Number.parseFloat(
        list.style.getPropertyValue('--ov-experience-indicator-left'),
      ),
      titleBox.left + titleBox.width / 2 - 8,
    );
  } finally {
    await view.unmount();
  }
});
