import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import {
  OpenVikingProvider,
  useFileBrowser,
} from './helpers/internal-hooks.mjs';
import {
  deferred,
  mount,
  ok,
  recordingFetch,
  settle,
} from './helpers/module-dom.mjs';

const root = 'viking://user/alice/resources';
const wiki = `${root}/wiki`;
const file = `${wiki}/page.md`;
const alias = 'viking://~/resources/wiki/page.md';
const canonical = (uri, user = 'alice') =>
  uri.replace(/^viking:\/\/~(?=\/|$)/, `viking://user/${user}`);
let browser;
let displayed;

function Probe({ currentUri, rootUri = root }) {
  browser = useFileBrowser({
    rootUri,
    currentUri,
    controlled: true,
    onUriChange() {},
    renderDashboard: (dashboard) => {
      displayed = dashboard;
    },
  });
  return h(
    'div',
    null,
    browser.previewTabs?.find((tab) => tab.key === browser.previewKey)?.content,
  );
}

function fixture({ source, attrsUri = true }) {
  const state = { user: 'alice', hold: false };
  const delayed = deferred();
  const fetch = recordingFetch(({ path, uri }) => {
    const identity = canonical(uri, state.user);
    if (path.endsWith('/configs'))
      return source === 'metadata'
        ? Promise.resolve(new Response('', { status: 404 }))
        : ok({
            targets: [{ to: wiki }, { to: wiki.replace('/alice/', '/bob/') }],
          });
    if (path.endsWith('/attrs'))
      return state.hold && state.user === 'alice'
        ? delayed.promise
        : ok({ ...(attrsUri ? { uri: identity } : {}), attrs: { tags: [] } });
    if (path.endsWith('/stat'))
      return ok({ uri: identity, isDir: !identity.endsWith('.md') });
    if (path.endsWith('/ls'))
      return ok(
        identity === root
          ? [
              {
                uri: wiki,
                isDir: true,
                tags: source === 'metadata' ? ['compile_target=true'] : [],
              },
            ]
          : identity === wiki
            ? [{ uri: file, isDir: false }]
            : [],
      );
    return ok('# Wiki document');
  });
  return { state, fetch, delayed };
}

for (const source of ['registry', 'metadata']) {
  for (const aliasRoot of [false, true]) {
    test(`FileBrowser discovers a Wiki child through ${source} with ${aliasRoot ? 'alias' : 'canonical'} root`, async () => {
      const { fetch } = fixture({ source });
      const rootUri = aliasRoot ? 'viking://~/resources' : root;
      const render = (currentUri) =>
        h(OpenVikingProvider, { fetch }, h(Probe, { currentUri, rootUri }));
      const view = await mount(render(file));
      try {
        assert.equal(
          browser.previewTabs.some((tab) => tab.key === `dashboard:${file}`),
          true,
        );
        await view.render(render(alias));
        assert.equal(
          browser.previewTabs.some((tab) => tab.key === `dashboard:${alias}`),
          true,
        );
        assert.equal(displayed.uri, alias);
        assert.equal(displayed.isRoot, false);
        await act(async () => browser.actions.openDashboard(alias));
        await settle();
        assert.equal(browser.previewKey, `dashboard:${alias}`);
        assert.match(view.container.textContent, /No matching conflicts/);
        assert.equal(
          view.container.querySelector('.ov-wiki-dashboard dt').textContent,
          'Pending conflicts',
        );
        await view.render(render(`${root}/wiki-other/page.md`));
        assert.equal(
          browser.previewTabs.some((tab) => tab.key.startsWith('dashboard:')),
          false,
        );
      } finally {
        await view.unmount();
      }
    });
  }
}

test('discovery falls back to native stat when attrs omits the canonical URI', async () => {
  const { fetch } = fixture({ source: 'registry', attrsUri: false });
  const view = await mount(
    h(OpenVikingProvider, { fetch }, h(Probe, { currentUri: alias })),
  );
  try {
    assert.equal(
      browser.previewTabs.some((tab) => tab.key === `dashboard:${alias}`),
      true,
    );
    assert.equal(displayed.uri, alias);
  } finally {
    await view.unmount();
  }
});

test('Wiki discovery never reuses a home-alias identity after the authenticated user changes', async () => {
  const { fetch, state, delayed } = fixture({ source: 'registry' });
  const render = () =>
    h(
      OpenVikingProvider,
      { fetch, userId: state.user },
      h(Probe, { currentUri: alias }),
    );
  const view = await mount(render());
  try {
    assert.equal(
      browser.previewTabs.some((tab) => tab.key.startsWith('dashboard:')),
      true,
    );
    state.hold = true;
    await act(async () => browser.actions.refresh());
    await settle();
    const stale = fetch.calls.findLast((call) => call.path.endsWith('/attrs'));
    state.user = 'bob';
    await view.render(render());
    assert.equal(stale.init.signal.aborted, true);
    assert.equal(
      browser.previewTabs.some((tab) => tab.key.startsWith('dashboard:')),
      false,
    );
    await act(async () =>
      delayed.resolve(await ok({ uri: file, attrs: { tags: [] } })),
    );
    await settle();
    assert.equal(
      browser.previewTabs.some((tab) => tab.key.startsWith('dashboard:')),
      false,
    );
  } finally {
    await view.unmount();
  }
});
