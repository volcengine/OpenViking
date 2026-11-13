import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import { mount } from './helpers/module-dom.mjs';

const trajectory = {
  uri: 'viking://user/a/memories/trajectories/t.md',
  name: 'Release verification',
};

const render = (onOpenUri) =>
  h(ExperienceDashboard, {
    uri: 'file',
    kind: 'file',
    data: { trajectories: [trajectory], trajectoryTotal: 1 },
    onOpenUri,
  });

test('a trajectory title is text and the open button owns the action', async () => {
  const opened = [];
  const view = await mount(render((uri) => opened.push(uri)));
  try {
    // 控制台把打开动作放在行尾的图标按钮上，标题只是文字。两处都调 onOpenUri
    // 会让同一行出现两个指向同一目标的入口。
    //
    // The console puts the open action on the icon button at the end of the row; the
    // title is text. Two triggers for one destination would offer the same trip twice.
    const title = view.container.querySelector(
      '.ov-experience-dashboard__trajectory-name',
    );
    assert.equal(title.tagName, 'STRONG');
    assert.equal(title.textContent, 'Release verification');
    assert.equal(
      title.closest('button'),
      null,
      'the title is not inside a button',
    );
    const open = view.container.querySelector(
      '.ov-experience-dashboard__icon-button[aria-label="Open"]',
    );
    assert.ok(open, 'the open button stays');
    await act(async () => open.click());
    assert.deepEqual(opened, [trajectory.uri]);
  } finally {
    await view.unmount();
  }
});

test('the open button is disabled without a host callback', async () => {
  const view = await mount(render(undefined));
  try {
    const open = view.container.querySelector(
      '.ov-experience-dashboard__icon-button[aria-label="Open"]',
    );
    assert.equal(open.disabled, true);
  } finally {
    await view.unmount();
  }
});

test('a failed section retries through a bordered secondary button', async () => {
  let retried = 0;
  const view = await mount(
    h(ExperienceDashboard, {
      uri: 'failed',
      kind: 'file',
      data: {},
      states: {
        outcomes: {
          error: new Error('Unavailable'),
          onRetry: () => retried++,
        },
      },
    }),
  );
  try {
    // 控制台的 ContextRetryAction 是 `type="outline" size="small"`；文字形态在
    // 成片留白里看不出是可点的控件。
    //
    // The console's ContextRetryAction is `type="outline" size="small"`; a text-only
    // form reads as plain copy in that much whitespace.
    const button = [...view.container.querySelectorAll('button')].find(
      (item) => item.textContent === 'Retry',
    );
    assert.ok(button);
    assert.match(button.className, /ov-button--outline/);
    assert.match(button.className, /ov-button--small/);
    await act(async () => button.click());
    assert.equal(retried, 1);
  } finally {
    await view.unmount();
  }
});
