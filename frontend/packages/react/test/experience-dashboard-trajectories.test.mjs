import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import {
  OpenVikingProvider,
  ExperienceDashboardPane,
} from './helpers/internal-hooks.mjs';
import {
  mount,
  settle,
  ok,
  recordingFetch,
  deferred,
} from './helpers/module-dom.mjs';

const user = 'viking://user/alice';
const uri = `${user}/memories/experiences/check.md`;
const wrapper = (fetch, component) =>
  h(OpenVikingProvider, { fetch }, component);
const click = async (button) => {
  assert.ok(button);
  await act(async () => button.click());
  await settle();
};

test('loading the second trajectory page keeps the first one visible', async () => {
  const pageSize = 20;
  const total = 40;
  const fetch = recordingFetch(({ path, query }) => {
    if (path.endsWith('/outcomes'))
      return ok({ outcome_distribution: [{ outcome: 'success', count: 1 }] });
    if (path.endsWith('/trajectories')) {
      const offset = Number(query?.offset ?? 0);
      const items = Array.from({ length: pageSize }, (_, index) => {
        const n = offset + index + 1;
        return {
          uri: `${user}/memories/trajectories/task-${n}.md`,
          name: `Task ${n}`,
        };
      });
      return ok({
        items,
        total,
        has_more: offset + pageSize < total,
      });
    }
    return ok({ attrs: { memory: { links: [] } } });
  });
  const view = await mount(wrapper(fetch, h(ExperienceDashboardPane, { uri })));
  const names = () =>
    [
      ...view.container.querySelectorAll(
        '.ov-experience-dashboard__trajectory-name',
      ),
    ].map((el) => el.textContent);
  try {
    assert.equal(names().length, pageSize);
    assert.equal(names()[0], 'Task 1');
    await click(
      view.container.querySelector(
        '.ov-experience-dashboard__load-more-action',
      ),
    );
    // 第二页加载后第一页必须还在：以前只保留新响应，前 20 条会消失。
    //
    // After the second page the first must still be there; it used to be dropped.
    assert.equal(names().length, total);
    assert.equal(names()[0], 'Task 1');
    assert.equal(names().at(-1), `Task ${total}`);
  } finally {
    await view.unmount();
  }
});

test('load-more requests only the next page and keeps rows while waiting or failing', async () => {
  const pageSize = 20;
  const total = 60;
  const gate = deferred();
  let pageState = 'wait';
  const fetch = recordingFetch(({ path, query }) => {
    if (path.endsWith('/outcomes'))
      return ok({ outcome_distribution: [{ outcome: 'success', count: 1 }] });
    if (path.endsWith('/trajectories')) {
      const offset = Number(query?.offset ?? 0);
      if (offset > 0 && pageState === 'wait') return gate.promise;
      if (offset > 0 && pageState === 'fail')
        return Promise.resolve(new Response('Unavailable', { status: 503 }));
      const items = Array.from({ length: pageSize }, (_, index) => {
        const n = offset + index + 1;
        return {
          uri: `${user}/memories/trajectories/task-${n}.md`,
          name: `Task ${n}`,
        };
      });
      return ok({ items, total, has_more: offset + pageSize < total });
    }
    return ok({ attrs: { memory: { links: [] } } });
  });
  const view = await mount(wrapper(fetch, h(ExperienceDashboardPane, { uri })));
  const names = () =>
    [
      ...view.container.querySelectorAll(
        '.ov-experience-dashboard__trajectory-name',
      ),
    ].map((el) => el.textContent);
  const offsets = () =>
    fetch.calls
      .filter((call) => call.path.endsWith('/trajectories'))
      .map((call) => Number(call.query?.offset ?? 0));
  const clickLoadMore = () =>
    act(async () =>
      view.container
        .querySelector('.ov-experience-dashboard__load-more-action')
        .click(),
    );
  try {
    assert.equal(names().length, pageSize);
    assert.deepEqual(offsets(), [0]);

    // 加载更多只请求下一页：累计请求量线性增长，不重放历史页。
    //
    // Load-more requests only the next page, so the total grows linearly.
    await clickLoadMore();
    await settle();
    assert.deepEqual(offsets(), [0, pageSize]);
    // 等待期间已加载的 20 条必须还在。
    //
    // While the request is in flight, the 20 loaded rows must stay.
    assert.equal(names().length, pageSize);

    // 失败时同样保留已加载的记录，并就地给出重试。
    //
    // A failure keeps the loaded rows too, with an inline retry.
    pageState = 'fail';
    // 让在途请求真正失败：resolve 成一个被拒的 promise。
    //
    // Make the in-flight request fail: resolve with a rejected promise.
    await act(async () => {
      gate.resolve(
        Promise.reject(new Response('Unavailable', { status: 503 })),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await settle();
    assert.equal(names().length, pageSize);
    assert.match(view.container.textContent, /Could not load data/);

    // 重试仍请求同一页，成功后追加。
    //
    // The retry asks for the same page and appends it.
    pageState = 'ok';
    await click(
      [...view.container.querySelectorAll('button')].find(
        (item) => item.textContent === 'Retry',
      ),
    );
    assert.equal(names().length, pageSize * 2);
    assert.deepEqual(offsets(), [0, pageSize, pageSize]);

    // 再加载一次请求第三页。
    //
    // One more load asks for the third page.
    await clickLoadMore();
    await settle();
    assert.deepEqual(offsets(), [0, pageSize, pageSize, pageSize * 2]);
    assert.equal(names().length, total);
    assert.equal(names()[0], 'Task 1');
    assert.equal(names().at(-1), `Task ${total}`);
  } finally {
    await view.unmount();
  }
});

test('a late trajectory response for the previous resource is ignored', async () => {
  const pending = deferred();
  const fetch = recordingFetch(({ path, query }) => {
    if (path.endsWith('/outcomes'))
      return ok({ outcome_distribution: [{ outcome: 'success', count: 1 }] });
    if (path.endsWith('/trajectories')) {
      const target = String(query?.experience_uri ?? '');
      if (target.endsWith('slow.md')) return pending.promise;
      return ok({
        items: [{ uri: `${user}/memories/trajectories/next.md`, name: 'Next' }],
        total: 1,
        has_more: false,
      });
    }
    return ok({ attrs: { memory: { links: [] } } });
  });
  const slow = `${user}/memories/experiences/slow.md`;
  const view = await mount(
    wrapper(fetch, h(ExperienceDashboardPane, { uri: slow })),
  );
  try {
    await view.render(
      wrapper(
        fetch,
        h(ExperienceDashboardPane, {
          uri: `${user}/memories/experiences/next.md`,
        }),
      ),
    );
    await settle();
    assert.match(view.container.textContent, /Next/);
    // 旧资源迟到的响应不得覆盖新资源的结果。
    //
    // A late response for the old resource must not overwrite the new one.
    await act(async () => {
      pending.resolve(
        ok({
          items: [
            { uri: `${user}/memories/trajectories/stale.md`, name: 'Stale' },
          ],
          total: 1,
          has_more: false,
        }),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await settle();
    assert.doesNotMatch(view.container.textContent, /Stale/);
    assert.match(view.container.textContent, /Next/);
  } finally {
    await view.unmount();
  }
});
