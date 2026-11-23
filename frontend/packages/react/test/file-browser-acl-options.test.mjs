import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  failure,
  fixture,
  initial,
  level,
  mount,
  ok,
  open,
  settle,
  tree,
  writes,
} from './helpers/acl-dom.mjs';
import { deferred } from './helpers/module-dom.mjs';

test('equivalent request options preserve confirmation and requests during locale changes', async () => {
  const fetch = fixture();
  const render = (locale) =>
    tree(fetch, { locale, requestOptions: { resourceId: 'shared' } });
  const view = await mount(render('en-US'));
  try {
    await open();
    await level('user:alice', 'Can read');
    const gets = fetch.countOf('/api/v1/acl');
    assert.ok(document.querySelector('.ov-file-browser-acl__confirmation'));
    await view.render(render('zh-CN'));
    assert.equal(fetch.countOf('/api/v1/acl'), gets);
    assert.match(
      document.querySelector('.ov-file-browser-acl__confirmation').textContent,
      /停止继承上级权限/,
    );
  } finally {
    await view.unmount();
  }
});

test('equivalent request options do not cancel a pending save or lose its success notification', async () => {
  const pending = deferred();
  let notifications = 0;
  const notify = () => notifications++;
  const fetch = fixture((call) =>
    call.init.method === 'PUT' ? pending.promise : undefined,
  );
  const render = (locale) =>
    tree(fetch, {
      locale,
      requestOptions: { userId: 'alice', resourceId: 'shared' },
      acl: { onChange: notify },
    });
  const view = await mount(render('en-US'));
  try {
    await open();
    await level('group:research', 'Can edit');
    const write = writes(fetch)[0];
    assert.ok(write);
    const gets = fetch.countOf('/api/v1/acl');
    await view.render(render('zh-CN'));
    assert.equal(write.init.signal.aborted, false);
    assert.equal(fetch.countOf('/api/v1/acl'), gets);
    const report = initial();
    await act(async () =>
      pending.resolve(
        await ok({
          ...report,
          direct_entries: write.body.entries,
          effective_entries: [
            ...report.inherited_entries.filter(
              (entry) =>
                !write.body.entries.some(
                  (item) => item.principal === entry.principal,
                ),
            ),
            ...write.body.entries,
          ],
        }),
      ),
    );
    await settle();
    assert.equal(notifications, 1);
    assert.equal(
      document.querySelector('.ov-toast--success .ov-toast__body')?.textContent,
      '权限已保存',
    );
    assert.doesNotMatch(
      document.querySelector('.ov-file-browser-acl [role="status"]')
        .textContent,
      /权限已保存/,
    );
    await act(() =>
      document.querySelector('[aria-label="关闭权限管理"]').click(),
    );
    await settle();
    assert.match(
      document.querySelector('.ov-toast--success').textContent,
      /权限已保存/,
    );
  } finally {
    await view.unmount();
  }
});

test('a host-known administrator appears once as a fixed named member', async () => {
  const report = initial();
  report.effective_entries.push({ principal: 'user:default', level: 'manage' });
  const fetch = fixture((call) =>
    call.path.endsWith('/acl') && call.init.method === 'GET'
      ? ok(report)
      : undefined,
  );
  const view = await mount(
    tree(fetch, {
      locale: 'zh-CN',
      acl: { administratorUserId: 'default' },
    }),
  );
  try {
    await open();
    const rows = [...document.querySelectorAll('.ov-file-browser-acl__entry')];
    const admins = rows.filter(
      (row) =>
        row.querySelector('.ov-file-browser-acl__name')?.textContent ===
        'default',
    );
    assert.equal(admins.length, 1);
    assert.equal(
      admins[0].querySelector('.ov-file-browser-acl__badge')?.textContent,
      '管理员',
    );
    assert.equal(admins[0].querySelector('button'), null);
    assert.doesNotMatch(
      document.querySelector('.ov-file-browser-acl').textContent,
      /数据空间管理员|管理员始终保留隐式管理权限/,
    );
  } finally {
    await view.unmount();
  }
});

test('a failed ACL save does not announce success', async () => {
  const fetch = fixture((call) =>
    call.init.method === 'PUT' ? failure('Save denied') : undefined,
  );
  const view = await mount(tree(fetch));
  try {
    await open();
    await level('group:research', 'Can edit');
    assert.equal(writes(fetch).length, 1);
    assert.equal(document.querySelector('.ov-toast--success'), null);
    assert.doesNotMatch(
      document.querySelector('.ov-file-browser-acl').textContent,
      /Permissions saved/,
    );
  } finally {
    await view.unmount();
  }
});

test('the ACL success toast respects host-provided copy', async () => {
  const view = await mount(
    tree(fixture(), {
      labels: { acl: { saved: 'Custom permissions saved' } },
    }),
  );
  try {
    await open();
    await level('group:research', 'Can edit');
    assert.equal(
      document.querySelector('.ov-toast--success .ov-toast__body')?.textContent,
      'Custom permissions saved',
    );
  } finally {
    await view.unmount();
  }
});
