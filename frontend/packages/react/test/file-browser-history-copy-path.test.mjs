import './helpers/history-viewer-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  fixture,
  tree,
  mount,
  openAction,
  workflow,
  click,
  ok,
} from './helpers/preview-native-dom.mjs';

const uri = 'viking://user/alice/skills/%E4%B8%AD%E6%96%87/guide.md';
const oid = 'a'.repeat(40);
const feedback = () => document.querySelector('.ov-path-copy-feedback');

test('historical path copy waits for its clipboard result and uses path-specific feedback', async () => {
  let complete;
  let fail = false;
  const writes = [];
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: {
      writeText(value) {
        writes.push(value);
        return fail
          ? Promise.reject(new Error('Denied'))
          : new Promise((resolve) => (complete = resolve));
      },
    },
  });
  const fetch = fixture(uri, false, (call) => {
    if (call.path.endsWith('/snapshot/log'))
      return ok([{ oid, author: { time_seconds: 1794268900 } }]);
    if (call.path.endsWith('/snapshot/show'))
      return Promise.resolve(new Response('historical body'));
  });
  const view = await mount(tree(fetch, uri, { rootUri: 'viking://' }));
  try {
    await openAction('Version history');
    await click(
      workflow().querySelector('.ov-file-history__commit-list button'),
    );
    const copyPath = () =>
      workflow().querySelector('button[aria-label="Copy file path"]');
    const status = workflow().querySelector(
      '.ov-path-copy-status[role="status"]',
    );
    assert.equal(status.textContent, '');
    await click(copyPath());
    assert.equal(feedback(), null);
    await act(async () => complete());
    assert.equal(writes[0], 'viking://user/alice/skills/中文/guide.md');
    assert.equal(feedback()?.textContent.trim(), 'Node path copied');
    assert.equal(status.textContent.trim(), 'Node path copied');
    assert.equal(feedback().parentElement, copyPath().closest('.ov-modal'));
    fail = true;
    await click(copyPath());
    assert.equal(feedback(), null);
    assert.match(
      document.querySelector('.ov-toast--error').textContent,
      /copy/i,
    );
    assert.equal(copyPath().disabled, false);
    assert.equal(fetch.countOf('/api/v1/content/write'), 0);
    assert.equal(fetch.countOf('/api/v1/snapshot/restore'), 0);
  } finally {
    delete navigator.clipboard;
    await view.unmount();
  }
});
