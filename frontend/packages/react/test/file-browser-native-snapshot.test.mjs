import { notificationContent } from './helpers/feedback-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  rootUri,
  openAction,
  workflow,
  click,
  button,
  act,
  ok,
} from './helpers/preview-native-dom.mjs';

test('ordinary file history saves a snapshot only on explicit request and retains notes on conflict', async () => {
  const uri = `${rootUri}/guide.md`;
  let entries = [];
  let fileLocked = true;
  const fetch = fixture(uri, false, (call) => {
    if (call.path.endsWith('/snapshot/log')) return ok(entries);
    if (call.path.endsWith('/snapshot/show'))
      return Promise.resolve(new Response('saved body'));
    if (!call.path.endsWith('/snapshot/commit')) return;
    if (fileLocked) {
      return Promise.resolve(
        Response.json(
          {
            status: 'error',
            error: {
              code: 'CONFLICT',
              message: 'A snapshot path is being processed',
            },
          },
          { status: 409 },
        ),
      );
    }
    entries = [
      {
        oid: 'snapshot-1',
        message: call.body.message,
        author: { time_seconds: 1794268800 },
      },
    ];
    return ok({ result: 'created', commit_oid: 'snapshot-1' });
  });
  const view = await mount(tree(fetch, uri));
  try {
    await openAction('Version history');
    const note = workflow().querySelector(
      'input[aria-label="Describe this snapshot"]',
    );
    const save = () => button('Save snapshot', workflow());
    assert.ok(note);
    assert.equal(save().disabled, true);
    assert.equal(fetch.countOf('/api/v1/snapshot/commit'), 0);
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      ).set.call(note, ' Update guide ');
      note.dispatchEvent(new window.Event('input', { bubbles: true }));
    });
    await click(save());
    assert.match(
      notificationContent()?.textContent ?? '',
      /A snapshot path is being processed/,
    );
    assert.equal(note.value, ' Update guide ');
    assert.equal(fetch.countOf('/api/v1/snapshot/log'), 1);

    fileLocked = false;
    await click(save());
    const commits = fetch.calls.filter((call) =>
      call.path.endsWith('/snapshot/commit'),
    );
    const expected = { message: 'Update guide', paths: [uri], branch: 'main' };
    assert.deepEqual(
      commits.map((call) => call.body),
      [expected, expected],
    );
    assert.equal(fetch.countOf('/api/v1/content/write'), 0);
    assert.equal(fetch.countOf('/api/v1/snapshot/log'), 2);
    assert.equal(note.value, '');
    assert.equal(notificationContent()?.textContent, undefined);
    assert.match(workflow().textContent, /Update guide/);
  } finally {
    await view.unmount();
  }
});

test('read-only ordinary files can open history without a snapshot form', async () => {
  const uri = `${rootUri}/guide.md`;
  const fetch = fixture(uri, false);
  const view = await mount(tree(fetch, uri, { readOnly: true }));
  try {
    await openAction('Version history');
    assert.ok(workflow());
    assert.equal(button('Save snapshot', workflow()), undefined);
    assert.equal(fetch.countOf('/api/v1/snapshot/log'), 1);
  } finally {
    await view.unmount();
  }
});
