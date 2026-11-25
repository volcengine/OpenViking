import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import {
  OpenVikingProvider,
  useFileBrowser,
} from './helpers/internal-hooks.mjs';
import { mount, ok, recordingFetch } from './helpers/module-dom.mjs';

const root = 'viking://user/alice/memories';
const profile = `${root}/profile.md`;
const soul = `${root}/soul.md`;

function Probe({ onBrowser, uri }) {
  onBrowser(
    useFileBrowser({
      rootUri: root,
      controlled: true,
      currentUri: uri,
    }),
  );
  return null;
}

test('a function-valued identity never reuses a body across account changes', async () => {
  let browser;
  let account = 'one';
  const accountId = () => account;
  const modTime = '2026-11-11T22:10:00.123Z';
  const fetch = recordingFetch(({ path, uri, init }) => {
    if (path.endsWith('/fs/ls'))
      return ok(
        [profile, soul].map((value) => ({
          uri: value,
          isDir: false,
          modTime,
        })),
      );
    if (path.endsWith('/content/read')) {
      const requested = new Headers(init.headers).get('X-OpenViking-Account');
      return ok(
        `${requested}:${uri.endsWith('profile.md') ? 'profile' : 'soul'}`,
      );
    }
    if (path.endsWith('/fs/attrs')) return ok({ uri, attrs: {} });
    throw new Error(`Unexpected request: ${path}`);
  });
  const page = (uri) =>
    h(
      OpenVikingProvider,
      { fetch, accountId },
      h(Probe, {
        uri,
        onBrowser(value) {
          browser = value;
        },
      }),
    );
  const view = await mount(page(profile));
  try {
    assert.equal(browser.previewTabs[0].source.content, 'one:profile');
    await view.render(page(soul));
    account = 'two';
    await view.render(page(profile));
    assert.equal(browser.previewTabs[0].source.content, 'two:profile');
    const reads = fetch.calls.filter((call) =>
      call.path.endsWith('/content/read'),
    );
    assert.equal(reads.length >= 3, true);
    assert.equal(
      new Headers(reads.at(-1).init.headers).get('X-OpenViking-Account'),
      'two',
    );
  } finally {
    await view.unmount();
  }
});

test('a file without a listed version does not reuse a stale body', async () => {
  let browser;
  let profileBody = 'first';
  const fetch = recordingFetch(({ path, uri }) => {
    if (path.endsWith('/fs/ls'))
      return ok([profile, soul].map((value) => ({ uri: value, isDir: false })));
    if (path.endsWith('/fs/stat')) return ok({ isDir: false });
    if (path.endsWith('/content/read'))
      return ok(uri === profile ? profileBody : 'soul');
    if (path.endsWith('/fs/attrs')) return ok({ uri, attrs: {} });
    throw new Error(`Unexpected request: ${path}`);
  });
  const page = (uri) =>
    h(
      OpenVikingProvider,
      { fetch },
      h(Probe, {
        uri,
        onBrowser(value) {
          browser = value;
        },
      }),
    );
  const view = await mount(page(profile));
  try {
    assert.equal(browser.previewTabs[0].source.content, 'first');
    await view.render(page(soul));
    profileBody = 'second';
    await view.render(page(profile));
    assert.equal(browser.previewTabs[0].source.content, 'second');
    assert.equal(fetch.countOf('/api/v1/content/read'), 3);
  } finally {
    await view.unmount();
  }
});
