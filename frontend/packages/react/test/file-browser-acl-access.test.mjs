import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  button,
  click,
  fixture,
  level,
  mount,
  ok,
  open,
  settle,
  tree,
  URI,
  writes,
} from './helpers/acl-dom.mjs';

test('native group summaries become group principals and already granted groups are filtered', async () => {
  const fetch = fixture();
  const view = await mount(tree(fetch));
  try {
    await open();
    await act(async () =>
      document.querySelector('.ov-file-browser-acl__add input').focus(),
    );
    await click(button('Group'));
    await act(async () => new Promise((resolve) => setTimeout(resolve, 240)));
    assert.ok(
      fetch.calls.some((call) =>
        call.path.endsWith('/admin/accounts/demo/groups'),
      ),
    );
    const candidates = document.querySelector(
      '.ov-file-browser-acl__candidates',
    );
    assert.match(candidates.textContent, /engineering/);
    assert.ok(!candidates.textContent.includes('research'));
    await click(candidates.querySelector('input'));
    await click(button('Add'));
    assert.ok(
      writes(fetch)[0].body.entries.some(
        (entry) => entry.principal === 'group:engineering',
      ),
    );
  } finally {
    await view.unmount();
  }
});

test('read and write show effective levels without a management action', async () => {
  let permission = 'read';
  const fetch = fixture((call) =>
    call.path.endsWith('/fs/stat')
      ? ok({ isDir: true, access: permission })
      : undefined,
  );
  const view = await mount(tree(fetch));
  try {
    assert.equal(
      document.querySelector('.ov-file-browser-acl__tag').textContent,
      'Can read',
    );
    assert.equal(document.querySelector('.ov-file-browser-acl__manage'), null);
    permission = 'write';
    await view.render(tree(fetch, { requestOptions: { userId: 'writer' } }));
    assert.equal(
      document.querySelector('.ov-file-browser-acl__tag').textContent,
      'Can edit',
    );
    assert.equal(document.querySelector('.ov-file-browser-acl__manage'), null);
    assert.equal(fetch.countOf('/api/v1/acl'), 0);
  } finally {
    await view.unmount();
  }
});

test('denied rows cannot open; container rows expand but cannot select or preview', async () => {
  const denied = `${URI}/denied`;
  const container = `${URI}/container`;
  const selected = [];
  const fetch = fixture((call) =>
    call.path.endsWith('/fs/ls') && call.uri === 'viking://resources'
      ? ok([
          {
            uri: denied,
            isDir: true,
            access: 'denied',
            tags: ['compile_target=true'],
          },
          {
            uri: container,
            isDir: true,
            access: 'container',
            tags: ['compile_target=true'],
          },
          { uri: `${URI}/readme.md`, access: 'read' },
          { uri: `${URI}/draft.md`, access: 'write' },
          { uri: `${URI}/managed.md`, access: 'manage' },
        ])
      : undefined,
  );
  const view = await mount(
    tree(fetch, {
      defaultUri: undefined,
      acl: false,
      onUriChange: (uri) => selected.push(uri),
    }),
  );
  try {
    const rows = [...view.container.querySelectorAll('[role="treeitem"]')];
    assert.match(rows[0].textContent, /No access/);
    assert.match(rows[1].textContent, /Traverse only/);
    assert.match(rows[2].textContent, /Read-only/);
    for (const row of rows.slice(3)) {
      assert.doesNotMatch(row.textContent, /No access|Traverse only|Read-only/);
    }
    assert.equal(rows[0].querySelector('.ov-file-browser__dashboard'), null);
    assert.equal(rows[1].querySelector('.ov-file-browser__dashboard'), null);
    await click(rows[0]);
    await click(rows[1]);
    await act(async () =>
      rows[1].dispatchEvent(
        new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }),
      ),
    );
    assert.deepEqual(selected, []);
    assert.equal(rows[0].querySelector('button').disabled, true);
    await click(rows[1].querySelector('button'));
    assert.ok(
      fetch.calls.some(
        (call) => call.path.endsWith('/fs/ls') && call.uri === container,
      ),
    );
    assert.equal(fetch.countOf('/api/v1/content/overview'), 0);
    await view.render(tree(fetch, { uri: container, acl: false }));
    await settle();
    assert.match(view.container.textContent, /only be expanded/);
    assert.equal(fetch.countOf('/api/v1/content/overview'), 0);
    assert.equal(fetch.countOf('/api/v1/content/read'), 0);
  } finally {
    await view.unmount();
  }
});

test('a successful native manage probe enables the Modal when metadata is missing', async () => {
  const fetch = fixture((call) =>
    call.path.endsWith('/fs/stat') ? ok({ isDir: true }) : undefined,
  );
  const view = await mount(tree(fetch));
  try {
    assert.ok(fetch.countOf('/api/v1/acl') > 0);
    await open();
    await level('group:research', 'Can edit');
    assert.equal(writes(fetch).length, 1);
  } finally {
    await view.unmount();
  }
});

test('permission lookup failures remain retryable instead of silently hiding ACL', async () => {
  let failed = true;
  const fetch = fixture((call) =>
    call.path.endsWith('/fs/stat') && failed
      ? Promise.reject(new Error('Temporary network failure'))
      : undefined,
  );
  const view = await mount(tree(fetch));
  try {
    assert.match(
      document.querySelector('.ov-file-browser-acl__lookup-error').textContent,
      /Could not load permissions/,
    );
    failed = false;
    await click(
      button(
        'Retry',
        document.querySelector('.ov-file-browser-acl__lookup-error'),
      ),
    );
    assert.ok(document.querySelector('.ov-file-browser-acl__tag'));
    await open();
    assert.ok(document.querySelector('.ov-file-browser-acl [role="dialog"]'));
  } finally {
    await view.unmount();
  }
});
