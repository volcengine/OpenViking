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
  // 注入率固定一位小数，与另外两项指标同排时数字宽度不跳；零也显示成 0.0%。
  //
  // The rate always carries one decimal so the three metrics keep a steady width; zero
  // reads 0.0% as well.
  assert.deepEqual(
    [...view.container.querySelectorAll('dd')].map((item) => item.textContent),
    ['0', '0', '0.0%'],
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
