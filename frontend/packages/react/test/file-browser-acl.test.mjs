import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  button,
  click,
  fixture,
  initial,
  level,
  mount,
  ok,
  open,
  settle,
  tree,
  URI,
  writes,
  failure,
} from './helpers/acl-dom.mjs';

test('an increase retains inheritance; a decrease requires confirmation and language switches preserve it', async () => {
  const fetch = fixture();
  const view = await mount(tree(fetch));
  try {
    await open();
    await level('group:research', 'Can edit');
    assert.equal(writes(fetch).length, 1);
    assert.equal(writes(fetch)[0].body.acl_mode, 'inherit');
    assert.deepEqual(writes(fetch)[0].body.entries, [
      { principal: 'group:research', level: 'write' },
      { principal: 'user:bob', level: 'write' },
    ]);
    await level('user:alice', 'Can read');
    assert.match(
      document.body.textContent,
      /Stop inheriting parent permissions/,
    );
    assert.equal(document.activeElement.textContent, 'Cancel');
    assert.equal(writes(fetch).length, 1);
    const gets = fetch.countOf('/api/v1/acl');
    await view.render(tree(fetch, { locale: 'zh-CN' }));
    assert.match(document.body.textContent, /停止继承上级权限/);
    assert.equal(fetch.countOf('/api/v1/acl'), gets);
    await click(button('取消'));
    assert.equal(writes(fetch).length, 1);
    await level('user:alice', '可阅读');
    await click(button('确定'));
    assert.equal(writes(fetch)[1].body.acl_mode, 'restricted');
    assert.deepEqual(
      writes(fetch)[1].body.entries.find(
        (entry) => entry.principal === 'user:alice',
      ),
      { principal: 'user:alice', level: 'read' },
    );
  } finally {
    await view.unmount();
  }
});

test('restore clears direct grants using PUT when the gateway blocks DELETE', async () => {
  const report = { ...initial(), acl_mode: 'restricted' };
  const fetch = fixture((call) =>
    call.path.endsWith('/acl') && call.init.method === 'GET'
      ? ok(report)
      : call.path.endsWith('/acl') && call.init.method === 'DELETE'
        ? failure('ApiBlocked')
        : undefined,
  );
  const view = await mount(tree(fetch));
  try {
    await open();
    await click(button('Restore inheritance'));
    assert.match(
      document.querySelector('.ov-file-browser-acl__confirmation').textContent,
      /user:bob/,
    );
    await click(button('Confirm'));
    assert.equal(writes(fetch)[0].init.method, 'PUT');
    assert.deepEqual(writes(fetch)[0].body, {
      uri: URI,
      acl_mode: 'inherit',
      entries: [],
    });
  } finally {
    await view.unmount();
  }
});

test('principal selection is native, paginated, and never requests credentials', async () => {
  const fetch = fixture();
  const view = await mount(tree(fetch));
  try {
    await open();
    await click(document.querySelector('.ov-file-browser-acl__add input'));
    await act(async () => new Promise((resolve) => setTimeout(resolve, 240)));
    const request = fetch.calls.find((call) => call.path.endsWith('/users'));
    assert.equal(request.query.include_credentials, 'false');
    assert.equal(request.query.include_summary, 'true');
    assert.equal(request.path, '/api/v1/admin/accounts/demo/users');
    const candidate = [
      ...document.querySelectorAll('.ov-file-browser-acl__candidate'),
    ].find((node) => node.textContent.includes('carol'));
    await click(candidate.querySelector('input'));
    await click(button('Add'));
    assert.ok(
      writes(fetch)[0].body.entries.some(
        (entry) => entry.principal === 'user:carol' && entry.level === 'read',
      ),
    );
    assert.equal(writes(fetch)[0].body.acl_mode, 'inherit');
  } finally {
    await view.unmount();
  }
});

test('save failure retains previous grants and the server reason', async () => {
  const fetch = fixture((call) =>
    call.path.endsWith('/acl') && call.init.method === 'PUT'
      ? failure('Membership changed')
      : undefined,
  );
  const view = await mount(tree(fetch));
  try {
    await open();
    await level('group:research', 'Can edit');
    assert.match(
      document.querySelector('.ov-file-browser-acl__failure').textContent,
      /Membership changed/,
    );
    assert.ok(
      document.querySelector('button[aria-label="group:research: Can read"]'),
    );
    assert.equal(writes(fetch).length, 1);
  } finally {
    await view.unmount();
  }
});

test('ACL is opt-in and a 403 does not invent an effective read or write level', async () => {
  const fetch = fixture((call) =>
    call.path.endsWith('/fs/stat')
      ? ok({ isDir: true })
      : call.path.endsWith('/acl')
        ? failure()
        : undefined,
  );
  const view = await mount(tree(fetch, { acl: false }));
  try {
    assert.equal(fetch.countOf('/api/v1/acl'), 0);
    await view.render(tree(fetch, { acl: true }, { userId: 'default' }));
    await settle();
    assert.ok(fetch.countOf('/api/v1/acl') > 0);
    assert.equal(document.querySelector('.ov-file-browser-acl__manage'), null);
    assert.equal(document.querySelector('.ov-file-browser-acl__tag'), null);
  } finally {
    await view.unmount();
  }
});
