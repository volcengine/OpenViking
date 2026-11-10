import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { OpenVikingProvider } from './helpers/internal-hooks.mjs';
import { useFileBrowser } from './helpers/internal-hooks.mjs';
import { mount, settle, ok, recordingFetch } from './helpers/module-dom.mjs';

const root = 'viking://user/alice/resources';
const wiki = `${root}/wiki`;
let browser;
function Probe({ kinds, renderDashboard }) {
  browser = useFileBrowser({
    rootUri: root,
    currentUri: wiki,
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
