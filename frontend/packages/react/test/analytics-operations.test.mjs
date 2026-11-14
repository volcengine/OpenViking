import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree,
  rootUri,
  openAction,
  mount,
  act,
  settle,
  click,
} from './helpers/preview-native-dom.mjs';

const uri = `${rootUri}/secret.md`;
const content = 'private message content';
const eventsFor = (events, target) =>
  events.filter((event) => event.name.includes(target));

test('copy path and copy content have separate click and outcome events without copied values', async () => {
  const events = [];
  let deny = false;
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: {
      async writeText() {
        if (deny) throw new Error('private clipboard message');
      },
    },
  });
  const fetch = fixture(uri, false, (call) =>
    call.path.endsWith('/content/read')
      ? Promise.resolve(
          new Response(JSON.stringify({ status: 'ok', result: content }), {
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      : undefined,
  );
  const view = await mount(
    tree(
      fetch,
      uri,
      {},
      { analytics: { onEvent: (event) => events.push(event) } },
    ),
  );
  try {
    await settle(10);
    await click(document.querySelector('[aria-label="Copy path"]'));
    await openAction('Copy content');
    deny = true;
    await click(document.querySelector('[aria-label="Copy path"]'));
    await openAction('Copy content');
    assert.deepEqual(
      eventsFor(events, 'copy_path').map((event) => event.name),
      [
        'openviking_file_preview_copy_path_click',
        'openviking_file_preview_copy_path_success',
        'openviking_file_preview_copy_path_click',
        'openviking_file_preview_copy_path_fail',
      ],
    );
    assert.deepEqual(
      eventsFor(events, 'copy_content').map((event) => event.name),
      [
        'openviking_file_preview_copy_content_click',
        'openviking_file_preview_copy_content_success',
        'openviking_file_preview_copy_content_click',
        'openviking_file_preview_copy_content_fail',
      ],
    );
    assert.equal(
      /secret|private|viking:\/\//.test(JSON.stringify(events)),
      false,
    );
  } finally {
    await view.unmount();
  }
});

test('failed downloads emit failure only, and successful initiation emits start only', async () => {
  const events = [];
  let fail = true;
  const fetch = fixture(uri, false, (call) =>
    call.path.endsWith('/content/download')
      ? fail
        ? Promise.resolve(new Response('private error', { status: 503 }))
        : Promise.resolve(new Response('bytes'))
      : undefined,
  );
  const originalCreate = URL.createObjectURL,
    originalRevoke = URL.revokeObjectURL;
  const originalClick = window.HTMLAnchorElement.prototype.click;
  URL.createObjectURL = () => 'blob:example';
  URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = () => {};
  const view = await mount(
    tree(
      fetch,
      uri,
      {},
      { analytics: { onEvent: (event) => events.push(event) } },
    ),
  );
  try {
    await openAction('Download');
    await settle();
    assert.deepEqual(
      eventsFor(events, 'download').map((event) => event.name),
      ['openviking_file_preview_download_fail'],
    );
    assert.equal(eventsFor(events, 'download')[0].params.error_kind, 'http');
    fail = false;
    await openAction('Download');
    await settle();
    assert.deepEqual(
      eventsFor(events, 'download').map((event) => event.name),
      [
        'openviking_file_preview_download_fail',
        'openviking_file_preview_download_start',
      ],
    );
    assert.equal(JSON.stringify(events).includes('private error'), false);
  } finally {
    await view.unmount();
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    window.HTMLAnchorElement.prototype.click = originalClick;
  }
});

test('analytics configuration changes do not restart the preview request', async () => {
  const fetch = fixture(uri, false);
  const view = await mount(tree(fetch, uri));
  try {
    const before = fetch.calls.length;
    await view.render(tree(fetch, uri, {}, { analytics: { onEvent() {} } }));
    await settle();
    assert.equal(fetch.calls.length, before);
  } finally {
    await view.unmount();
  }
});
