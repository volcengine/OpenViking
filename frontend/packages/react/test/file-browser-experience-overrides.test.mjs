import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const directory = 'viking://user/alice/memories/experiences';
const uri = `${directory}/check.md`;
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
const render = (fetch, dataSource, currentUri = uri, sections) =>
  h(
    OpenVikingProvider,
    { fetch },
    h(FileBrowser, {
      rootUri: directory,
      uri: currentUri,
      dashboardKinds: ['experience'],
      experienceDataSource: dataSource,
      experienceDashboardSlots: { sections },
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

test('experience metrics and section rendering can be overridden independently', async () => {
  const fetch = fixture();
  const requests = [];
  let unavailable = true;
  const dataSource = {
    async effect(request) {
      requests.push(request);
      if (unavailable) throw new Error('Metrics unavailable');
      return { injectionCount: 2, recallCount: 4, injectionRate: 0.5 };
    },
  };
  const sections = {
    effect: ({ data }, content) =>
      data.effect
        ? h('div', null, `Custom recalls: ${data.effect.recallCount}`)
        : content,
  };
  const view = await mount(render(fetch, dataSource, uri, sections));
  try {
    await openDashboard(view.container);
    assert.match(view.container.textContent, /Metrics unavailable/);
    unavailable = false;
    await click(
      [...view.container.querySelectorAll('button')].find(
        (button) => button.textContent === 'Retry',
      ),
    );
    assert.match(view.container.textContent, /Custom recalls: 4/);
    const previous = requests.at(-1);
    await click(
      view.container.querySelector(
        '[role="combobox"][aria-label="Statistics period"]',
      ),
    );
    await click(
      [...document.querySelectorAll('[role="option"]')].find(
        (option) => option.textContent === 'Last 30 days',
      ),
    );
    assert.equal(requests.at(-1).uri, uri);
    assert.notEqual(requests.at(-1).startDate, previous.startDate);
    assert.equal(previous.signal.aborted, true);
    assert.ok(
      fetch.countOf('/api/v1/agent-evolution/experiences/outcomes') > 0,
    );
    assert.ok(
      fetch.countOf('/api/v1/agent-evolution/experiences/trajectories') > 0,
    );
    assert.ok(
      ![...view.container.querySelectorAll('[role="tab"]')].some(
        (tab) => tab.textContent === 'Test',
      ),
    );
    await view.render(render(fetch, dataSource));
    assert.match(
      view.container.querySelector('.ov-experience-dashboard__metrics')
        .textContent,
      /50/,
    );
    await view.render(render(fetch, undefined));
    assert.match(
      view.container.querySelector('.ov-experience-dashboard__metrics')
        .textContent,
      /—/,
    );
  } finally {
    await view.unmount();
  }
});
