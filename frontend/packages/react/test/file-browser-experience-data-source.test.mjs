import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';

const directory = 'viking://user/alice/memories/experiences';
const uri = `${directory}/check.md`;
const trajectory = (name) => ({
  uri: `viking://user/alice/memories/trajectories/${name}.md`,
  name,
});
const fixture = () =>
  recordingFetch(({ path, uri: target }) => {
    if (path.endsWith('/ls')) return ok([{ uri, name: 'Native experience' }]);
    if (path.endsWith('/stat'))
      return ok({ uri: target, isDir: target === directory });
    if (path.endsWith('/attrs')) return ok({ attrs: { tags: [] } });
    if (path.endsWith('/outcomes')) return ok({ outcome_distribution: [] });
    if (path.endsWith('/trajectories'))
      return ok({ items: [], total: 0, has_more: false });
    return ok('Document');
  });
const render = (fetch, dataSource, currentUri = uri) =>
  h(
    OpenVikingProvider,
    { fetch },
    h(FileBrowser, {
      rootUri: directory,
      uri: currentUri,
      dashboardKinds: ['experience'],
      experienceDataSource: dataSource,
    }),
  );

const click = async (element) => {
  assert.ok(element);
  await act(async () => element.click());
  await settle();
};
const openDashboard = (container) =>
  click(
    [...container.querySelectorAll('[role="tab"]')].find(
      (tab) => tab.textContent === 'Experience dashboard',
    ),
  );

test('public FileBrowser accepts custom paged experiences and can fall back to the native list', async () => {
  const fetch = fixture();
  const pages = [];
  const list = async (request) => {
    pages.push(request);
    const name = `Experience page ${request.offset / request.limit + 1}`;
    return { items: [{ uri, name }], total: 21, hasMore: request.offset === 0 };
  };
  const view = await mount(render(fetch, { list }, directory));
  try {
    await openDashboard(view.container);
    assert.match(view.container.textContent, /Experience page 1/);
    assert.ok(pages.length > 0);
    assert.ok(pages.every((page) => page.offset === 0));
    await click(view.container.querySelector('button[aria-label="Next page"]'));
    assert.match(view.container.textContent, /Experience page 2/);
    assert.deepEqual(
      pages
        .filter(({ offset }) => offset > 0)
        .map(({ uri, offset, limit }) => ({ uri, offset, limit })),
      [{ uri: directory, offset: 20, limit: 20 }],
    );
    assert.equal(pages[0].signal.aborted, true);
    await view.render(
      render(fetch, { list: async () => undefined }, directory),
    );
    assert.match(view.container.textContent, /Native experience/);
    assert.ok(
      fetch.calls.some(
        ({ path, query }) => path.endsWith('/ls') && query.sort_by === 'mtime',
      ),
    );
  } finally {
    await view.unmount();
  }
});

test('custom trajectories retry the same page and abort when switching to source trace', async () => {
  const fetch = fixture();
  const pages = [];
  const pending = deferred();
  const outcomeRequests = [];
  const sourceRequests = [];
  let nextPageAttempts = 0;
  const dataSource = {
    async outcomes(request) {
      outcomeRequests.push(request);
      return [{ outcome: 'success', count: 42 }];
    },
    async trajectories(request) {
      pages.push(request);
      if (request.offset === 20 && nextPageAttempts++ === 0)
        throw new Error('Studio unavailable');
      if (request.offset === 40) return pending.promise;
      const name = request.offset === 0 ? 'First task' : 'Second task';
      return { items: [trajectory(name)], total: 41, hasMore: true };
    },
    async sources(request) {
      sourceRequests.push(request);
      return [trajectory('Source task')];
    },
  };
  const view = await mount(render(fetch, dataSource));
  const loadMore = () =>
    click(
      view.container.querySelector(
        '.ov-experience-dashboard__load-more-action',
      ),
    );
  const button = (name) =>
    [...view.container.querySelectorAll('button')].find(
      (element) => element.textContent === name,
    );
  try {
    await openDashboard(view.container);
    assert.match(view.container.textContent, /Task success42/);
    assert.match(view.container.textContent, /First task/);
    assert.equal(outcomeRequests[0].uri, uri);
    for (const key of ['startDate', 'endDate']) {
      assert.match(outcomeRequests[0][key], /^\d{4}-\d{2}-\d{2}$/);
      assert.equal(pages[0][key], outcomeRequests[0][key]);
    }
    assert.equal(
      fetch.countOf('/api/v1/agent-evolution/experiences/outcomes'),
      0,
    );
    assert.equal(
      fetch.countOf('/api/v1/agent-evolution/experiences/trajectories'),
      0,
    );

    await loadMore();
    assert.match(view.container.textContent, /First task/);
    await click(button('Retry'));
    assert.match(view.container.textContent, /First task.*Second task/s);
    await loadMore();
    assert.deepEqual(
      pages
        .slice(pages.findIndex(({ offset }) => offset === 20))
        .map(({ offset }) => offset),
      [20, 20, 40],
    );
    assert.equal(pages.at(-1).signal.aborted, false);
    await click(button('Source trace'));
    assert.equal(pages.at(-1).signal.aborted, true);
    assert.equal(outcomeRequests.at(-1).signal.aborted, true);
    assert.equal(sourceRequests[0].uri, uri);
    assert.match(view.container.textContent, /Source task/);
    await act(async () =>
      pending.resolve({
        items: [trajectory('Stale task')],
        total: 41,
        hasMore: false,
      }),
    );
    await settle();
    assert.doesNotMatch(view.container.textContent, /Stale task/);
  } finally {
    await view.unmount();
  }
  assert.equal(sourceRequests[0].signal.aborted, true);
});
