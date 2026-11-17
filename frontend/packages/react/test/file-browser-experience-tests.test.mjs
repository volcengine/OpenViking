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
const fixture = (respond) =>
  recordingFetch(({ path, uri: target }) => {
    const response = respond?.(path, target);
    if (response) return response;
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

const findByText = (container, selector, label) =>
  [...container.querySelectorAll(selector)].find(
    (element) => element.textContent === label,
  );

test('Test tab is opt-in and loads test materials by page with retry', async () => {
  const fetch = fixture();
  const requests = [];
  let total = 21;
  const dataSource = {
    async tests(request) {
      requests.push(request);
      if (request.offset && requests.length === 2)
        throw new Error('Unavailable');
      if (request.offset >= total) return { items: [], total };
      return {
        items: [
          {
            id: String(request.offset),
            title: 'Material',
            content: `Fixture ${request.offset}`,
          },
        ],
        total,
      };
    },
  };
  const view = await mount(render(fetch, undefined));
  const findTab = (label) => findByText(view.container, '[role="tab"]', label);
  try {
    await click(findTab('Experience dashboard'));
    assert.equal(findTab('Test'), undefined);
    await view.render(render(fetch, dataSource));
    assert.ok(findTab('Test'));
    assert.equal(requests.length, 0);
    await click(findTab('Test'));
    assert.match(view.container.textContent, /Fixture 0/);
    await click(view.container.querySelector('button[aria-label="Next page"]'));
    await click(
      [...view.container.querySelectorAll('button')].find(
        (element) => element.textContent === 'Retry',
      ),
    );
    assert.match(view.container.textContent, /Fixture 20/);
    assert.deepEqual(
      requests.map(({ uri: target, offset, limit }) => [target, offset, limit]),
      [
        [uri, 0, 20],
        [uri, 20, 20],
        [uri, 20, 20],
      ],
    );
    assert.equal(requests[0].signal.aborted, true);
    total = 20;
    await click(view.container.querySelector('.ov-file-browser__refresh'));
    assert.match(view.container.textContent, /Fixture 0/);
    assert.deepEqual(
      requests.slice(-2).map(({ offset }) => offset),
      [20, 0],
    );
    total = 0;
    await click(view.container.querySelector('.ov-file-browser__refresh'));
    assert.match(view.container.textContent, /No test materials/);
    assert.equal(requests.at(-1).offset, 0);
  } finally {
    await view.unmount();
  }
});

test('Source trace shows commit groups and retries only the selected trajectory messages', async () => {
  const latest = 'viking://user/alice/memories/trajectories/latest.md';
  const previous = 'viking://user/alice/memories/trajectories/previous.md';
  const updatedAt = '2026-11-14T06:12:00Z';
  const fetch = fixture((path, target) =>
    path.endsWith('/content/read') && target === previous
      ? ok('# Previous trajectory document')
      : undefined,
  );
  const groupRequests = [];
  const messageRequests = [];
  const pending = deferred();
  const view = await mount(
    render(fetch, {
      async sourceGroups(request) {
        groupRequests.push(request);
        return [
          {
            id: 'latest',
            updatedAt: '2026-11-17T07:30:00Z',
            trajectories: [{ uri: latest, name: 'Latest trajectory' }],
          },
          {
            id: 'previous',
            updatedAt,
            trajectories: [{ uri: previous, name: 'Previous trajectory' }],
          },
        ];
      },
      async trajectoryMessages(request) {
        messageRequests.push(request);
        if (request.uri === latest) return pending.promise;
        if (messageRequests.length === 1)
          throw new Error('Archive unavailable');
        return {
          items: [{ role: 'user', content: 'Previous source message' }],
        };
      },
    }),
  );
  const findTab = (label) => findByText(view.container, '[role="tab"]', label);
  const dialog = () => document.querySelector('[role="dialog"]');
  const sourceReads = () =>
    fetch.calls.filter(
      ({ path, uri: target }) =>
        path.endsWith('/content/read') && target === previous,
    );
  try {
    await click(findTab('Experience dashboard'));
    assert.equal(groupRequests.length, 0);
    await click(findTab('Source trace'));
    assert.deepEqual(
      groupRequests.map(({ uri: target }) => target),
      [uri],
    );
    const groups = view.container.querySelectorAll('details');
    assert.equal(groups.length, 2);
    assert.match(
      groups[0].textContent,
      /Latest experience.*Latest trajectory/s,
    );
    assert.equal(groups[1].querySelector('summary time').dateTime, updatedAt);
    assert.match(groups[1].textContent, /Previous trajectory/);
    assert.equal(messageRequests.length, 0);

    const trigger = findByText(groups[1], 'button', 'Trajectory details');
    await click(trigger);
    assert.match(dialog()?.textContent ?? '', /Previous trajectory document/);
    await click(findByText(dialog(), 'button', 'Retry'));
    assert.match(dialog()?.textContent ?? '', /Previous source message/);
    assert.deepEqual(
      messageRequests.map(({ uri: target }) => target),
      [previous, previous],
    );
    assert.equal(sourceReads().length, 1);

    await click(dialog().querySelector('button[aria-label="Close dialog"]'));
    await click(findByText(groups[0], 'button', 'Trajectory details'));
    const latestRequest = messageRequests.at(-1);
    assert.equal(latestRequest.uri, latest);
    await click(dialog().querySelector('button[aria-label="Close dialog"]'));
    assert.equal(latestRequest.signal.aborted, true);
    await act(async () =>
      pending.resolve({ items: [{ role: 'user', content: 'Stale message' }] }),
    );
    assert.equal(dialog(), null);
  } finally {
    await view.unmount();
  }
});
