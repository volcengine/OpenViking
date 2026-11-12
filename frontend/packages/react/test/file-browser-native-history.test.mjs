import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  mount,
  rootUri,
  target,
  openAction,
  workflow,
  click,
  button,
  ok,
  settle,
} from './helpers/preview-native-dom.mjs';

test('compile file history uses retained versions and their snapshot paths; language switches preserve the selected revision', async () => {
  const uri = `${target}/doc.md`;
  const fetch = fixture(
    uri,
    false,
    (call) => {
      if (call.path.endsWith('/snapshot/versions'))
        return ok({
          versions: [
            {
              version_id: 'version-2',
              version_number: 2,
              snapshot_id: 'snapshot-2',
              snapshot_path: `${target}/original.md`,
              created_at: 1794268800,
            },
          ],
        });
      if (call.path.endsWith('/snapshot/show'))
        return Promise.resolve(new Response('historical body'));
    },
    [{ to: target }],
  );
  const view = await mount(tree(fetch, uri));
  try {
    await openAction('Version history');
    const request = fetch.calls.find((call) =>
      call.path.endsWith('/snapshot/versions'),
    );
    assert.deepEqual(request.query, { uri, limit: '100' });
    await click(workflow().querySelector('[aria-label="Version 2"]'));
    const show = fetch.calls.find((call) =>
      call.path.endsWith('/snapshot/show'),
    );
    assert.deepEqual(show.query, {
      target_ref: 'snapshot-2',
      path: `${target}/original.md`,
      raw: 'false',
    });
    await click(button('Markdown', workflow()));
    assert.equal(
      workflow().querySelector('pre').textContent,
      'historical body',
    );
    const count = fetch.calls.length;
    await view.render(tree(fetch, uri, { locale: 'zh-CN' }));
    assert.equal(fetch.calls.length, count);
    assert.match(workflow().textContent, /版本 2/);
    assert.equal(
      workflow().querySelector('pre').textContent,
      'historical body',
    );
  } finally {
    await view.unmount();
  }
});

test('experience history is path-scoped, retryable and never supplies an edit capability', async () => {
  const uri = 'viking://user/alice/memories/experiences/a.md';
  let attempts = 0;
  const fetch = fixture(uri, false, (call) => {
    if (call.path.endsWith('/snapshot/log'))
      return ++attempts === 1
        ? Promise.resolve(new Response('offline', { status: 503 }))
        : ok([
            {
              oid: 'snapshot',
              message: 'Initial experience',
              author: { time_seconds: 1794268800 },
              committer: { time_seconds: 1825804800 },
            },
          ]);
  });
  const view = await mount(tree(fetch, uri, { rootUri: 'viking://' }));
  try {
    assert.equal(view.container.querySelector('[aria-label="Edit"]'), null);
    await openAction('Version history');
    assert.ok(workflow().querySelector('[role="alert"]'));
    await click(button('Retry', workflow()));
    assert.match(workflow().textContent, /Initial experience/);
    assert.equal(
      workflow().querySelector('time')?.dateTime,
      new Date(1794268800000).toISOString(),
    );
    assert.match(workflow().querySelector('time').textContent, /2026/);
    assert.deepEqual(
      fetch.calls.find((call) => call.path.endsWith('/snapshot/log')).query,
      { paths: uri, limit: '500' },
    );
    assert.equal(workflow().querySelector('[role="alert"]'), null);
    await view.render(tree(fetch, `${rootUri}/ordinary.md`));
    await settle();
    assert.equal(workflow(), null);
  } finally {
    await view.unmount();
  }
});

test('Compile directory history renders native log author dates', async () => {
  const fetch = fixture(
    target,
    true,
    (call) =>
      call.path.endsWith('/snapshot/log')
        ? ok([
            {
              oid: 'directory-snapshot',
              message: 'Compile directory',
              author: { time_seconds: 1794268800 },
              committer: { time_seconds: 1825804800 },
            },
          ])
        : undefined,
    [{ to: target }],
  );
  const view = await mount(tree(fetch, target));
  try {
    await openAction('Version history');
    assert.equal(
      workflow().querySelector('time')?.dateTime,
      new Date(1794268800000).toISOString(),
    );
    assert.match(workflow().querySelector('time').textContent, /2026/);
  } finally {
    await view.unmount();
  }
});
