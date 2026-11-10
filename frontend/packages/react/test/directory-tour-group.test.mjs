import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, StrictMode } from 'react';
import {
  DirectoryTour,
  DirectoryTourIntro,
} from '@openviking/react/components/directory-tour';
import { mount, settle } from './helpers/module-dom.mjs';

globalThis.MutationObserver = window.MutationObserver;
HTMLElement.prototype.scrollIntoView = function () {};
const box = (top, height) => ({
  top,
  bottom: top + height,
  left: 20,
  right: 320,
  width: 300,
  height,
});
function anchors() {
  const group = [100, 132, 164, 196].map((top) => {
    const row = document.createElement('div');
    row.getBoundingClientRect = () => box(top, 32);
    document.body.append(row);
    return row;
  });
  return group;
}
const step = {
  id: 'team',
  target: 'team',
  title: 'Team experiences',
  description: 'Shared knowledge',
  cardWidth: 320,
};

test('custom single-step content highlights all rows, keeps selection and completes only on click', async () => {
  const rows = anchors();
  rows[1].setAttribute('aria-selected', 'true');
  let completed = 0;
  let located = 0;
  let edited = 0;
  const locateTarget = async () => {
    located++;
    return rows;
  };
  const renderContent = ({ complete }) =>
    h(DirectoryTourIntro, {
      locale: 'en-US',
      title: 'Team',
      description: 'Shared knowledge',
      onDone: complete,
      features: [h('button', { onClick: () => edited++ }, 'Edit')],
    });
  const props = {
    locale: 'en-US',
    open: true,
    steps: [step],
    locateTarget,
    renderContent,
    onComplete: () => completed++,
  };
  const view = await mount(h(StrictMode, null, h(DirectoryTour, props)));
  try {
    assert.equal(completed, 0);
    const dialog = document.querySelector('[role="dialog"]');
    assert.equal(dialog.style.width, '320px');
    const highlight = dialog.querySelector('.ov-directory-tour__highlight');
    assert.equal(highlight.style.top, '100px');
    assert.equal(highlight.style.height, '128px');
    assert.equal(highlight.style.width, '300px');
    assert.equal(dialog.querySelector('.ov-directory-tour__progress'), null);
    assert.equal(rows[1].getAttribute('aria-selected'), 'true');
    const count = located;
    await view.render(
      h(StrictMode, null, h(DirectoryTour, { ...props, locale: 'zh-CN' })),
    );
    assert.equal(
      located,
      count,
      'language changes must not locate the target again',
    );
    await act(async () =>
      dialog.querySelector('.ov-directory-tour__features button').click(),
    );
    assert.equal(edited, 1);
    assert.equal(completed, 0, 'edit is independent of acknowledgement');
    await act(async () =>
      dialog.querySelector('.ov-directory-tour__primary').click(),
    );
    assert.equal(completed, 1);
    await act(async () => {
      rows.forEach((row, index) => {
        row.getBoundingClientRect = () => box(-40 + index * 32, 32);
      });
      window.dispatchEvent(new window.Event('scroll'));
    });
    assert.equal(
      dialog.style.visibility,
      'visible',
      'visible children keep a group callout visible',
    );
    assert.equal(highlight.style.top, '0px');
    assert.equal(highlight.style.height, '88px');
    await act(async () => {
      rows.forEach((row, index) => {
        row.getBoundingClientRect = () => box(100 + index * 32, 32);
      });
      window.dispatchEvent(new window.Event('scroll'));
    });
    rows.at(-1).remove();
    await settle();
    assert.equal(
      highlight.style.height,
      '96px',
      'removed children shrink the group highlight',
    );
    rows[0].remove();
    await settle();
    assert.match(dialog.textContent, /此目录暂不可用/);
    assert.equal(dialog.querySelector('.ov-directory-tour__intro'), null);
  } finally {
    await view.unmount();
    rows.forEach((row) => row.remove());
  }
});

test('custom content never bypasses failed-anchor recovery and stale resolutions are ignored', async () => {
  let resolve;
  let rendered = 0;
  const rows = anchors();
  const view = await mount(
    h(DirectoryTour, {
      locale: 'en-US',
      open: true,
      steps: [step],
      locateTimeout: 10,
      locateTarget: () =>
        new Promise((done) => {
          resolve = done;
        }),
      renderContent: () => {
        rendered++;
        return 'Ready';
      },
    }),
  );
  try {
    await act(async () => new Promise((done) => setTimeout(done, 20)));
    assert.match(
      document.querySelector('[role="dialog"]').textContent,
      /unavailable/,
    );
    await act(async () => resolve(rows));
    assert.equal(rendered, 0);
  } finally {
    await view.unmount();
    rows.forEach((row) => row.remove());
  }
});
