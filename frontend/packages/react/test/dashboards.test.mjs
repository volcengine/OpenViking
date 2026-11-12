import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import {
  OpenVikingProvider,
  WikiDashboardPane,
  ExperienceDashboardPane,
} from './helpers/internal-hooks.mjs';
import { ExperienceDashboard } from '@openviking/react/components/experience-dashboard';
import {
  mount,
  settle,
  ok,
  recordingFetch,
  deferred,
} from './helpers/module-dom.mjs';

const user = 'viking://user/alice';
const uri = `${user}/memories/experiences/check.md`;
const wiki = `${user}/resources/wiki`;
const failed = () =>
  Promise.resolve(new Response('Unavailable', { status: 503 }));
const wrapper = (fetch, component) =>
  h(OpenVikingProvider, { fetch }, component);
const click = async (button) => {
  assert.ok(button);
  await act(async () => button.click());
  await settle();
};

test('Wiki malformed footer is an error with retry; a real empty Wiki retains zero', async () => {
  let valid = false;
  const fetch = recordingFetch(({ path }) =>
    path.endsWith('/stat')
      ? ok({ isDir: false })
      : ok(valid ? '# No conflicts' : '<!-- CONFLICTS_CONTENT {broken -->'),
  );
  const view = await mount(
    wrapper(
      fetch,
      h(WikiDashboardPane, { uri: `${wiki}/guide.md`, kind: 'file' }),
    ),
  );
  assert.match(view.container.textContent, /Invalid CONFLICTS_CONTENT/);
  assert.doesNotMatch(view.container.textContent, /No matching conflicts/);
  valid = true;
  await click(
    [...view.container.querySelectorAll('button')].find(
      (button) => button.textContent === 'Retry',
    ),
  );
  assert.match(view.container.textContent, /No matching conflicts/);
  assert.equal(view.container.querySelector('dd').textContent, '0');
  await view.unmount();
});

test('controlled experience data needs no Provider, distinguishes zero from unavailable, and conditionally exposes Test', async () => {
  const view = await mount(
    h(ExperienceDashboard, { uri, kind: 'file', data: {} }),
  );
  assert.deepEqual(
    [...view.container.querySelectorAll('dd')].map((item) => item.textContent),
    ['—', '—', '—'],
  );
  assert.equal(view.container.querySelectorAll('[role="tab"]').length, 2);
  await view.render(
    h(ExperienceDashboard, {
      uri,
      kind: 'file',
      data: {
        effect: { injectionCount: 0, recallCount: 0, injectionRate: 0 },
        testMaterials: [],
      },
    }),
  );
  assert.deepEqual(
    [...view.container.querySelectorAll('dd')].map((item) => item.textContent),
    ['0', '0', '0%'],
  );
  await click(
    [...view.container.querySelectorAll('[role="tab"]')].find(
      (item) => item.textContent === 'Test',
    ),
  );
  assert.match(view.container.textContent, /No test materials/);
  await view.unmount();
});

test('native outcome failure stays independent from trajectories, retry works, and stale URI results are ignored', async () => {
  let error = true;
  let delay = false;
  const pending = deferred();
  const fetch = recordingFetch(({ path }) => {
    if (path.endsWith('/outcomes'))
      return delay
        ? pending.promise
        : error
          ? failed()
          : ok({ outcome_distribution: [{ outcome: 'success', count: 8 }] });
    if (path.endsWith('/trajectories'))
      return ok({
        items: [
          {
            uri: `${user}/memories/trajectories/task.md`,
            name: 'Task verification',
          },
        ],
        total: 1,
        has_more: false,
      });
    return ok({ attrs: { memory: { links: [] } } });
  });
  const view = await mount(wrapper(fetch, h(ExperienceDashboardPane, { uri })));
  assert.match(view.container.textContent, /Could not load data/);
  assert.match(view.container.textContent, /Task verification/);
  assert.equal(view.container.querySelectorAll('[role="tab"]').length, 2);
  error = false;
  await click(
    [...view.container.querySelectorAll('button')].find(
      (item) => item.textContent === 'Retry',
    ),
  );
  assert.match(view.container.textContent, /Task success8/);
  delay = true;
  await view.render(
    wrapper(
      fetch,
      h(ExperienceDashboardPane, { uri: uri.replace('check.md', 'slow.md') }),
    ),
  );
  const slow = fetch.calls.findLast((call) => call.path.endsWith('/outcomes'));
  delay = false;
  await view.render(
    wrapper(
      fetch,
      h(ExperienceDashboardPane, { uri: uri.replace('check.md', 'next.md') }),
    ),
  );
  assert.equal(slow.init.signal.aborted, true);
  await act(async () =>
    pending.resolve(
      await ok({ outcome_distribution: [{ outcome: 'success', count: 777 }] }),
    ),
  );
  await settle();
  assert.match(view.container.textContent, /Task success8/);
  assert.doesNotMatch(view.container.textContent, /777/);
  await view.unmount();
});

test('loading the second trajectory page keeps the first one visible', async () => {
  const pageSize = 20;
  const total = 40;
  const fetch = recordingFetch(({ path, query }) => {
    if (path.endsWith('/outcomes'))
      return ok({ outcome_distribution: [{ outcome: 'success', count: 1 }] });
    if (path.endsWith('/trajectories')) {
      const offset = Number(query?.get?.('offset') ?? query?.offset ?? 0);
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
