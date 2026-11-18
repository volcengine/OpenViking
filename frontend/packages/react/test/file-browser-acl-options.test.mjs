import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
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
    assert.match(document.body.textContent, /权限已保存/);
  } finally {
    await view.unmount();
  }
});
