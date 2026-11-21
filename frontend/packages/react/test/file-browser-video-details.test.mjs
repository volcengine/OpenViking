import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  button,
  click,
  fixture,
  mount,
  ok,
  rootUri,
  settle,
  tree,
} from './helpers/preview-native-dom.mjs';
import { deferred } from './helpers/module-dom.mjs';

const folder = `${rootUri}/clip`;
const videoUri = `${folder}/clip.webm`;
const metadata = `${folder}/segment_info.json`;
const frame = `${folder}/frame-1.jpg`;
const transcript = JSON.stringify({
  frame_count: 1,
  audio_segments: [
    { content: 'Second line', start_time: 1000, end_time: 2000 },
    { content: 'Opening line', start_time: 0, end_time: 1000 },
  ],
});

function videoFixture(readMetadata) {
  return fixture(videoUri, false, (call) => {
    if (call.path.endsWith('/fs/ls') && call.uri === folder) {
      return ok([videoUri, metadata, frame].map((uri) => ({ uri })));
    }
    if (call.path.endsWith('/content/read') && call.uri === metadata) {
      return readMetadata(call);
    }
    if (call.path.endsWith('/content/download') && call.uri === frame) {
      return new Promise(() => {});
    }
  });
}

test('processed video shows playback before metadata, seeks by transcript, and requests frames on demand', async () => {
  const pending = deferred();
  const fetch = videoFixture(() => pending.promise);
  const view = await mount(tree(fetch, videoUri, {}, { userId: 'alice' }));
  try {
    const video = view.container.querySelector('video');
    assert.ok(video);
    assert.ok(button('Transcript', view.container));
    assert.equal(
      fetch.calls.some((call) => call.uri === frame),
      false,
    );
    await click(button('Extracted frames (1)', view.container));
    assert.ok(fetch.calls.find((call) => call.uri === frame));
    await act(async () =>
      video.dispatchEvent(new window.Event('loadedmetadata')),
    );
    pending.resolve(await ok(transcript));
    await settle();
    assert.equal(view.container.querySelector('video'), video);
    await click(button('Transcript', view.container));

    const entries = [
      ...view.container.querySelectorAll(
        '.ov-file-preview__video-transcript button',
      ),
    ];
    assert.equal(entries.length, 2);
    assert.match(entries[0].textContent, /Opening line/);
    await click(entries[1]);
    assert.equal(video.currentTime, 1);

    await click(button('Extracted frames (1)', view.container));
    const download = fetch.calls.find((call) => call.uri === frame);
    assert.equal(download?.path, '/api/v1/content/download');
    assert.equal(
      new Headers(download.init.headers).get('X-OpenViking-User'),
      'alice',
    );
    assert.equal(download.init.signal.aborted, false);
    await view.render(tree(fetch, `${rootUri}/another.md`));
    assert.equal(download.init.signal.aborted, true);
    assert.equal(button('Transcript', view.container), undefined);
  } finally {
    await view.unmount();
  }
});

for (const [name, readMetadata] of [
  [
    'a failed metadata request',
    () =>
      Promise.resolve(new Response('Metadata unavailable', { status: 503 })),
  ],
  ['malformed metadata', () => ok('{')],
  ['metadata without audio segments', () => ok('{}')],
]) {
  test(`readable frames remain available after ${name}`, async () => {
    const fetch = videoFixture(readMetadata);
    const view = await mount(tree(fetch, videoUri));
    try {
      assert.ok(view.container.querySelector('video'));
      assert.equal(
        fetch.calls.some((call) => call.uri === frame),
        false,
      );
      await click(button('Extracted frames (1)', view.container));
      assert.equal(
        fetch.calls.find((call) => call.uri === frame)?.path,
        '/api/v1/content/download',
      );
    } finally {
      await view.unmount();
    }
  });
}

test('late transcript results cannot restore a replaced video preview', async () => {
  const pending = deferred();
  const fetch = videoFixture(() => pending.promise);
  const view = await mount(tree(fetch, videoUri));
  try {
    await click(button('Extracted frames (1)', view.container));
    const download = fetch.calls.find((call) => call.uri === frame);
    const read = fetch.calls.find((call) => call.uri === metadata);
    await view.render(tree(fetch, `${rootUri}/another.md`));
    assert.equal(download.init.signal.aborted, true);
    assert.equal(read.init.signal.aborted, true);
    pending.resolve(await ok(transcript));
    await settle();
    assert.equal(view.container.querySelector('video'), null);
    assert.equal(button('Transcript', view.container), undefined);
  } finally {
    await view.unmount();
  }
});

test('a plain video does not request a transcript or display artifact tabs', async () => {
  const fetch = fixture(videoUri, false);
  const view = await mount(tree(fetch, videoUri));
  try {
    assert.ok(view.container.querySelector('video'));
    assert.equal(button('Transcript', view.container), undefined);
    assert.equal(
      fetch.calls.some((call) => call.uri === metadata),
      false,
    );
  } finally {
    await view.unmount();
  }
});
