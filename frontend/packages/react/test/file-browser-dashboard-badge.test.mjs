import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import {
  OpenVikingProvider,
  useFileBrowser,
} from './helpers/internal-hooks.mjs';
import {
  fixture,
  tree,
  mount,
  target,
  rootUri,
  ok,
  click,
  settle,
} from './helpers/preview-native-dom.mjs';
import { deferred } from './helpers/module-dom.mjs';

const badge = (view) =>
  view.container.querySelector('.ov-file-preview__tab-badge');
const boardTab = (view) =>
  view.container.querySelector('[role="tab"][data-key^="dashboard:"]');
const conflict = (index, fixed = false) => ({
  match_text: `Policy ${index}`,
  problem_description: `Conflict ${index}`,
  reason: 'Sources disagree',
  severity: 'high',
  state: fixed ? 'fixed' : 'unfixed',
  begin_time: 100,
  fix_time: fixed ? 101 : null,
  fix_plan: '',
  resolution_basis: '',
});
const content = (count) => {
  const records = [
    ...Array.from({ length: count }, (_, i) => conflict(i)),
    conflict(count, true),
  ];
  return `${records.map((item) => item.match_text).join('\n')}\n<!-- CONFLICTS_CONTENT\n${records.map(JSON.stringify).join(',\n')}\n-->`;
};

test('Wiki file badges count unresolved records in both views and clear after refresh', async () => {
  const file = `${target}/guide.md`;
  let count = 105;
  const fetch = fixture(file, false, ({ path, uri }) => {
    if (path.endsWith('/fs/ls'))
      return ok(
        uri === rootUri
          ? [{ uri: file, isDir: false, tags: ['compile_target=true'] }]
          : [],
      );
    if (path.endsWith('/search/find')) return ok({ resources: [] });
    if (path.endsWith('/content/read')) return ok(content(count));
  });
  const view = await mount(tree(fetch, file));
  try {
    assert.equal(badge(view)?.textContent, '99+');
    assert.equal(badge(view)?.title, '105 unresolved conflicts');
    assert.equal(boardTab(view).getAttribute('aria-selected'), 'false');
    const reads = fetch.countOf('/api/v1/content/read');
    await view.render(tree(fetch, file, { locale: 'zh-CN' }));
    assert.equal(badge(view)?.title, '105 条待解决冲突');
    assert.equal(fetch.countOf('/api/v1/content/read'), reads);
    await view.render(tree(fetch, file));
    await click(boardTab(view));
    assert.equal(badge(view)?.textContent, '99+');
    assert.equal(fetch.countOf('/api/v1/content/read'), reads);
    assert.equal(
      view.container.querySelector('.ov-wiki-dashboard dd')?.textContent,
      '105',
    );
    count = 1;
    await click(view.container.querySelector('[aria-label="Refresh"]'));
    assert.equal(badge(view)?.textContent, '1');
    count = 0;
    await click(view.container.querySelector('[aria-label="Refresh"]'));
    assert.equal(badge(view), null);
  } finally {
    await view.unmount();
  }
});

test('Wiki directory badges explain the file unit, cap 100, and hide unknown results', async () => {
  let count = 3;
  let fail = false;
  const fetch = fixture(
    target,
    true,
    ({ path }) => {
      if (path.endsWith('/search/find'))
        return fail
          ? Promise.resolve(new Response('', { status: 503 }))
          : ok({
              resources: Array.from({ length: count }, (_, i) => ({
                uri: `${target}/${i}.md`,
                level: 2,
              })),
            });
    },
    [{ to: target }],
  );
  const view = await mount(tree(fetch, target));
  try {
    assert.equal(badge(view)?.textContent, '3');
    assert.equal(badge(view)?.title, '3 files with unresolved conflicts');
    await click(boardTab(view));
    assert.equal(
      view.container.querySelector('.ov-wiki-dashboard dd')?.textContent,
      '3',
    );
    await view.render(
      tree(fetch, target, { renderDashboard: () => h('p', null, 'Host view') }),
    );
    assert.match(view.container.textContent, /Host view/);
    assert.equal(badge(view)?.textContent, '3');
    count = 100;
    await click(view.container.querySelector('[aria-label="Refresh"]'));
    assert.equal(badge(view)?.textContent, '99+');
    assert.equal(badge(view)?.title, '99+ files with unresolved conflicts');
    fail = true;
    await click(view.container.querySelector('[aria-label="Refresh"]'));
    assert.equal(badge(view), null);
  } finally {
    await view.unmount();
  }
});

test('a late prior file result cannot publish its badge after resource navigation', async () => {
  const file = `${target}/slow.txt`,
    next = `${target}/next.txt`;
  const delayed = deferred();
  const fetch = fixture(file, false, ({ path, uri }) => {
    if (path.endsWith('/fs/stat'))
      return ok({ isDir: uri === rootUri, access: 'manage' });
    if (path.endsWith('/fs/ls'))
      return ok([
        { uri: file, isDir: false, tags: ['compile_target=true'] },
        { uri: next, isDir: false, tags: ['compile_target=true'] },
      ]);
    if (path.endsWith('/search/find')) return ok({ resources: [] });
    if (path.endsWith('/content/read'))
      return uri === file ? delayed.promise : ok(content(2));
  });
  let browser;
  function Probe({ uri }) {
    browser = useFileBrowser({ rootUri, currentUri: uri, controlled: true });
    return null;
  }
  const render = (uri) => h(OpenVikingProvider, { fetch }, h(Probe, { uri }));
  const count = () =>
    browser.previewTabs?.find((tab) => tab.key.startsWith('dashboard:'))?.badge
      ?.count;
  const view = await mount(render(file));
  try {
    assert.equal(count(), undefined);
    await view.render(render(next));
    assert.equal(count(), 2);
    await delayed.resolve(await ok(content(40)));
    await settle();
    assert.equal(count(), 2);
  } finally {
    await view.unmount();
  }
});
