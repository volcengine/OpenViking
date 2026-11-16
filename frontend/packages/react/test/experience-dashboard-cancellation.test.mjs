import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
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

test('unmount aborts the trajectory request in flight', async () => {
  const pending = deferred();
  const fetch = recordingFetch(({ path }) => {
    if (path.endsWith('/outcomes'))
      return ok({ outcome_distribution: [{ outcome: 'success', count: 1 }] });
    if (path.endsWith('/trajectories')) return pending.promise;
    return ok({ attrs: { memory: { links: [] } } });
  });
  const view = await mount(wrapper(fetch, h(ExperienceDashboardPane, { uri })));
  const call = fetch.calls.find((item) => item.path.endsWith('/trajectories'));
  assert.ok(call, 'the first page was requested');
  assert.equal(call.init.signal.aborted, false);
  await view.unmount();
  // 卸载后必须真正中止在途请求，而不只是忽略它的结果。
  //
  // Unmounting must abort the request, not merely ignore its result.
  assert.equal(call.init.signal.aborted, true);
});

test('switching to another resource aborts the previous trajectory request', async () => {
  const slow = deferred();
  const fetch = recordingFetch(({ path, query }) => {
    if (path.endsWith('/outcomes'))
      return ok({ outcome_distribution: [{ outcome: 'success', count: 1 }] });
    if (path.endsWith('/trajectories')) {
      const target = String(query?.experience_uri ?? '');
      return target.endsWith('slow.md')
        ? slow.promise
        : ok({
            items: [
              { uri: `${user}/memories/trajectories/next.md`, name: 'Next' },
            ],
            total: 1,
            has_more: false,
          });
    }
    return ok({ attrs: { memory: { links: [] } } });
  });
  const slowUri = `${user}/memories/experiences/slow.md`;
  const view = await mount(
    wrapper(fetch, h(ExperienceDashboardPane, { uri: slowUri })),
  );
  try {
    const first = fetch.calls.find((item) =>
      item.path.endsWith('/trajectories'),
    );
    assert.equal(first.init.signal.aborted, false);
    await view.render(
      wrapper(
        fetch,
        h(ExperienceDashboardPane, {
          uri: `${user}/memories/experiences/next.md`,
        }),
      ),
    );
    await settle();
    // 切资源时旧请求要被真正中止。
    //
    // Switching resources must abort the old request for real.
    assert.equal(first.init.signal.aborted, true);
    assert.match(view.container.textContent, /Next/);
  } finally {
    await view.unmount();
  }
});
