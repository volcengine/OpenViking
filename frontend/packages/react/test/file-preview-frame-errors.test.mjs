import assert from 'node:assert/strict';
import test from 'node:test';
import { act, h, mount } from './helpers/monitoring-dom.mjs';
import { runtime as r } from './helpers/monitoring.mjs';

const frame = { id: 'frame-1', name: 'Frame 1', src: '/frame.jpg' };
const text = {
  previewImage: 'Enlarge image',
  closePreview: 'Close',
  expandVideo: 'Expand video',
  videoTranscript: 'Transcript',
  videoFrames: () => 'Frames',
  videoNoTranscript: 'No transcript',
  loading: 'Loading',
  loadFailed: 'Load failed',
  retry: 'Retry',
};
const media = (details = {}) =>
  h(r.FilePreviewMedia, {
    source: { fileName: 'clip.webm', url: '/clip.webm' },
    kind: 'video',
    reports: false,
    text,
    videoDetails: { segments: [], frames: [frame], ...details },
  });
const frameImage = (app) =>
  app.container.querySelector('.ov-file-preview__video-frames img');
const retry = (app) =>
  [...app.container.querySelectorAll('button')].find(
    (node) => node.textContent === 'Retry',
  );
async function openFrames(app) {
  await app.event(app.container.querySelectorAll('[role="tab"]')[1], 'click');
}

test('a failed thumbnail reports its source, prevents enlargement and can retry the same URL', async () => {
  const failures = [];
  let loads = 0;
  const app = await mount(
    media({
      onFrameError: (...args) => failures.push(args),
      onLoadFrames: () => loads++,
    }),
  );
  try {
    await openFrames(app);
    assert.equal(
      app.container.querySelector('.ov-file-preview__video-frame-name')
        .textContent,
      frame.name,
    );
    await app.event(frameImage(app), 'error');
    assert.deepEqual(failures, [[frame.id, frame.src]]);
    assert.equal(frameImage(app), null);
    assert.equal(
      app.container.querySelector('.ov-file-preview__video-frames button'),
      null,
    );
    assert.ok(app.container.textContent.includes('Load failed'));
    await app.event(retry(app), 'click');
    assert.equal(loads, 2);
    assert.equal(frameImage(app).getAttribute('src'), frame.src);
    await app.event(frameImage(app), 'load');
    assert.equal(retry(app), undefined);
  } finally {
    await app.dispose();
  }
});

test('a replacement source is not hidden by the previous source failure', async () => {
  const app = await mount(media());
  try {
    await openFrames(app);
    await app.event(frameImage(app), 'error');
    assert.ok(retry(app));
    await app.render(
      media({ frames: [{ ...frame, src: '/replacement.jpg' }] }),
    );
    assert.equal(frameImage(app).getAttribute('src'), '/replacement.jpg');
    assert.equal(retry(app), undefined);
  } finally {
    await app.dispose();
  }
});

test('an enlarged image failure closes the dialog and marks its thumbnail failed', async () => {
  const failures = [];
  const app = await mount(
    media({ onFrameError: (...args) => failures.push(args) }),
  );
  try {
    await openFrames(app);
    await app.event(
      app.container.querySelector('.ov-file-preview__video-frames button'),
      'click',
    );
    assert.ok(app.container.querySelector('dialog[open]'));
    await app.event(app.container.querySelector('dialog img'), 'error');
    assert.equal(app.container.querySelector('dialog[open]'), null);
    assert.equal(app.container.querySelector('dialog img'), null);
    assert.equal(frameImage(app), null);
    assert.ok(retry(app));
    assert.deepEqual(failures, [[frame.id, frame.src]]);
  } finally {
    await app.dispose();
  }
});

test('closing an enlarged frame leaves focus on the thumbnail', async () => {
  const app = await mount(media());
  try {
    await app.event(app.container.querySelector('video'), 'loadedmetadata');
    await openFrames(app);
    const thumbnail = frameImage(app).closest('button');
    thumbnail.focus();
    await app.event(thumbnail, 'click');

    const imageDialog = app.container.querySelector('dialog[open]');
    assert.ok(imageDialog);
    // Native dialogs restore focus before dispatching close. The DOM test
    // helper only dispatches the event, so the thumbnail is already focused.
    await act(() => imageDialog.close());

    assert.equal(imageDialog.open, false);
    assert.ok(
      thumbnail.matches(':focus'),
      'Focus should stay on the thumbnail',
    );
  } finally {
    await app.dispose();
  }
});

test('expanded preview preserves the player and follows the current transcript', async () => {
  const app = await mount(
    media({
      segments: [
        { startMs: 0, endMs: 1000, text: 'First entry' },
        { startMs: 1000, endMs: 2000, text: 'Second entry' },
      ],
    }),
  );
  try {
    const player = app.container.querySelector('video');
    const toggle = app.container.querySelector('[aria-label="Expand video"]');
    const active = () => app.container.querySelector('[aria-current="true"]');
    await app.event(player, 'loadedmetadata');
    assert.match(active().textContent, /First entry/);

    const panel = active().closest('[role="tabpanel"]');
    const next = active().closest('li').nextElementSibling;
    panel.getBoundingClientRect = () => ({ top: 0, bottom: 100 });
    Object.defineProperty(panel, 'clientHeight', { value: 100 });
    next.getBoundingClientRect = () => ({ top: 150, bottom: 200, height: 50 });
    player.currentTime = 1.25;
    await app.event(player, 'timeupdate');
    assert.match(active().textContent, /Second entry/);
    assert.equal(panel.scrollTop, 100);

    await openFrames(app);
    await app.event(toggle, 'click');
    assert.ok(app.container.querySelector('dialog[open]'));
    assert.equal(panel.hidden, false);
    assert.equal(panel.getAttribute('role'), 'region');
    assert.equal(app.container.querySelector('video'), player);
    assert.equal(player.currentTime, 1.25);

    await app.event(active(), 'click');
    assert.equal(player.currentTime, 1);

    await app.event(toggle, 'click');
    assert.equal(app.container.querySelector('dialog[open]'), null);
    assert.equal(panel.hidden, true);
    assert.equal(app.container.querySelector('video'), player);
    assert.equal(document.activeElement, toggle);
  } finally {
    await app.dispose();
  }
});
