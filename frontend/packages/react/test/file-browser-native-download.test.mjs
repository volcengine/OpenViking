import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  rootUri,
  openAction,
  act,
  settle,
  ok,
} from './helpers/preview-native-dom.mjs';

test('file download and directory export use their native endpoints', async () => {
  const uri = `${rootUri}/doc.md`;
  const directory = `${rootUri}/folder.with.dots`;
  const fetch = fixture(uri, false, (call) => {
    if (call.path.endsWith('/fs/ls'))
      return ok(
        call.uri === rootUri ? [{ uri }, { uri: directory, isDir: true }] : [],
      );
    if (call.path.endsWith('/content/download'))
      return Promise.resolve(new Response('original bytes'));
    if (call.path.endsWith('/pack/export'))
      return Promise.resolve(new Response(new Uint8Array([80, 75, 3, 4])));
  });
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const originalClick = window.HTMLAnchorElement.prototype.click;
  const blobs = [];
  const names = [];
  URL.createObjectURL = (blob) => {
    blobs.push(blob);
    return 'blob:download';
  };
  URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () {
    names.push(this.download);
  };
  const view = await mount(tree(fetch, uri));
  try {
    await openAction('Download');
    await view.render(tree(fetch, directory));
    await openAction('Export .ovpack');
    assert.equal(await blobs[0].text(), 'original bytes');
    assert.deepEqual(
      [...new Uint8Array(await blobs[1].arrayBuffer())],
      [80, 75, 3, 4],
    );
    assert.deepEqual(names, ['doc.md', 'folder.with.dots.ovpack']);
    const pack = fetch.calls.find((call) => call.path.endsWith('/pack/export'));
    assert.equal(pack.init.method, 'POST');
    assert.deepEqual(pack.body, { uri: directory });
  } finally {
    await view.unmount();
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    window.HTMLAnchorElement.prototype.click = originalClick;
  }
});

test('changing account aborts an in-flight download and never saves its late response', async () => {
  const uri = `${rootUri}/doc.md`;
  let resolve;
  let saves = 0;
  const fetch = fixture(uri, false, (call) =>
    call.path.endsWith('/content/download')
      ? new Promise((done) => {
          resolve = done;
        })
      : undefined,
  );
  const originalCreate = URL.createObjectURL;
  URL.createObjectURL = () => {
    saves++;
    return 'blob:late';
  };
  const view = await mount(tree(fetch, uri, {}, { accountId: 'a' }));
  try {
    // Don't wait for the pending action to settle before switching accounts.
    const more = document.querySelector('[aria-haspopup="menu"]');
    await act(async () => more.click());
    const item = [...document.querySelectorAll('[role="menuitem"]')].find(
      (node) => node.textContent.trim() === 'Download',
    );
    await act(async () => item.click());
    await settle();
    const call = fetch.calls.find((entry) =>
      entry.path.endsWith('/content/download'),
    );
    await view.render(tree(fetch, uri, {}, { accountId: 'b' }));
    assert.equal(call.init.signal.aborted, true);
    await act(async () => resolve(new Response('late bytes')));
    await settle();
    assert.equal(saves, 0);
  } finally {
    await view.unmount();
    URL.createObjectURL = originalCreate;
  }
});
