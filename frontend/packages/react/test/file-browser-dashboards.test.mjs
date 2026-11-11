import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { useFileBrowser } from './helpers/internal-hooks.mjs';
import {
  mount,
  settle,
  ok,
  recordingFetch,
  deferred,
} from './helpers/module-dom.mjs';

const root = 'viking://user/alice/resources';
const wiki = `${root}/wiki`;
let browser;
function Probe({ kinds, renderDashboard, currentUri = wiki }) {
  browser = useFileBrowser({
    rootUri: root,
    currentUri,
    onUriChange() {},
    dashboardKinds: kinds,
    renderDashboard,
  });
  return h(
    'div',
    null,
    browser.previewTabs?.find((tab) => tab.key === browser.previewKey)?.content,
  );
}

test('FileBrowser opens metadata-backed Wiki despite a missing registry; permissions and host views remain host-owned', async () => {
  const fetch = recordingFetch(({ path, uri }) => {
    if (path.endsWith('/configs'))
      return Promise.resolve(new Response('', { status: 404 }));
    if (path.endsWith('/attrs')) return ok({ attrs: { tags: [] } });
    if (path.endsWith('/stat')) return ok({ isDir: true });
    if (path.endsWith('/ls'))
      return uri === root
        ? ok([
            { uri: wiki, isDir: true, tags: ['compile_target=true'] },
            { uri: `${root}/wiki-old`, isDir: true },
            { uri: `${root}/plain`, isDir: true },
          ])
        : ok([]);
    return ok('Summary');
  });
  const render = (props = {}) =>
    h(OpenVikingProvider, { fetch }, h(Probe, props));
  const view = await mount(render());
  assert.equal(browser.dashboards.get(wiki).kind, 'wiki');
  assert.equal(browser.dashboards.get(`${root}/plain`), undefined);
  assert.equal(browser.dashboards.get(`${root}/wiki-old`), undefined);
  assert.equal(browser.dashboardError, null);
  await act(async () => browser.actions.openDashboard(wiki));
  await settle();
  assert.equal(
    view.container.querySelector('.ov-wiki-dashboard dd').textContent,
    '0',
  );
  assert.match(view.container.textContent, /No matching conflicts/);
  await view.render(
    render({
      renderDashboard: (dashboard) =>
        h('p', null, `Host view: ${dashboard.uri}`),
    }),
  );
  assert.equal(view.container.textContent, `Host view: ${wiki}`);
  await view.render(render({ kinds: [] }));
  assert.equal(browser.dashboards.size, 0);
  assert.equal(
    browser.previewTabs?.some((tab) => tab.key.startsWith('dashboard:')),
    false,
  );
  assert.equal(view.container.querySelector('.ov-wiki-dashboard'), null);
  await view.unmount();
});

for (const kind of ['wiki', 'experience']) {
  test(`FileBrowser refresh reloads its open ${kind} dashboard`, async () => {
    const currentUri =
      kind === 'wiki'
        ? wiki
        : 'viking://user/alice/memories/experiences/check.md';
    let value = 1;
    let hold = false;
    const delayed = deferred();
    const fetch = recordingFetch(({ path, uri }) => {
      if (path.endsWith('/configs')) return ok({ targets: [] });
      if (path.endsWith('/attrs')) return ok({ attrs: { tags: [] } });
      if (path.endsWith('/stat')) return ok({ isDir: kind === 'wiki' });
      if (path.endsWith('/ls'))
        return ok(
          uri === root
            ? [{ uri: wiki, isDir: true, tags: ['compile_target=true'] }]
            : Array.from({ length: value }, (_, i) => ({
                uri: `${wiki}/${i}.md`,
              })),
        );
      if (path.endsWith('/outcomes'))
        return hold
          ? delayed.promise
          : ok({
              outcome_distribution: [{ outcome: 'success', count: value }],
            });
      if (path.endsWith('/trajectories'))
        return ok({ items: [], total: 0, has_more: false });
      return ok('Document');
    });
    const view = await mount(
      h(OpenVikingProvider, { fetch }, h(Probe, { currentUri })),
    );
    try {
      await act(async () => browser.actions.openDashboard(currentUri));
      await settle();
      assert.match(
        view.container.textContent,
        kind === 'wiki' ? /1/ : /Task success1/,
      );
      value = 2;
      await act(async () => browser.actions.refresh());
      await settle();
      assert.match(
        view.container.textContent,
        kind === 'wiki' ? /2/ : /Task success2/,
      );
      if (kind === 'experience') {
        hold = true;
        await act(async () => browser.actions.refresh());
        await settle();
        const stale = fetch.calls.findLast((call) =>
          call.path.endsWith('/outcomes'),
        );
        hold = false;
        value = 3;
        await act(async () => browser.actions.refresh());
        await settle();
        assert.equal(stale.init.signal.aborted, true);
        await act(async () =>
          delayed.resolve(
            await ok({
              outcome_distribution: [{ outcome: 'success', count: 99 }],
            }),
          ),
        );
        await settle();
        assert.match(view.container.textContent, /Task success3/);
        assert.doesNotMatch(view.container.textContent, /Task success99/);
      }
    } finally {
      await view.unmount();
    }
  });
}
