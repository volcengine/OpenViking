import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, StrictMode } from 'react';
import {
  DirectoryTour,
  openVikingDirectoryTourSteps,
} from '@openviking/react/components/directory-tour';
import { deferred, mount, settle } from './helpers/module-dom.mjs';

const steps = ['memories', 'sessions', 'peers'].map((id) => ({
  id,
  target: id,
  title: id,
  description: `About ${id}`,
}));
const panel = () => document.querySelector('.ov-directory-tour');
const next = () =>
  act(async () => panel().querySelector('.ov-directory-tour__primary').click());
function makeAnchor() {
  const anchor = document.createElement('div');
  anchor.scrollIntoView = () => {};
  anchor.getBoundingClientRect = () => ({
    left: 10,
    top: 10,
    right: 110,
    bottom: 42,
    width: 100,
    height: 32,
  });
  document.body.append(anchor);
  return anchor;
}

test('a missing directory keeps its step open until Next and then resumes the tour', async () => {
  const pending = deferred();
  const anchor = makeAnchor();
  const changes = [];
  const results = [];
  const view = await mount(
    h(DirectoryTour, {
      open: true,
      steps: openVikingDirectoryTourSteps,
      defaultStep: 4,
      locale: 'zh-CN',
      locateTarget: (target) =>
        target === 'userExperiences'
          ? pending.promise
          : Promise.resolve(anchor),
      onStepChange: (index) => changes.push(index),
      onComplete: (result) => results.push(result),
    }),
  );
  try {
    assert.equal(panel().parentElement.style.visibility, 'hidden');
    await act(async () => pending.resolve(null));
    await settle();
    assert.deepEqual(changes, []);
    assert.deepEqual(results, []);
    assert.equal(panel().parentElement.style.visibility, 'visible');
    assert.equal(panel().getAttribute('aria-busy'), 'false');
    assert.equal(document.activeElement, panel());
    assert.equal(panel().querySelector('.ov-directory-tour__highlight'), null);
    assert.equal(
      panel().querySelector('[role="status"]').textContent,
      '当前暂未找到这个目录。',
    );
    assert.match(panel().textContent, /5\/6/);
    assert.equal(
      panel().querySelector('.ov-directory-tour__primary').textContent,
      '下一步',
    );
    await next();
    assert.deepEqual(changes, [5]);
    assert.match(panel().textContent, /peers/);
    assert.match(panel().textContent, /6\/6/);
    assert.equal(panel().querySelector('[role="status"]'), null);
    assert.ok(panel().querySelector('.ov-directory-tour__highlight'));
    await next();
    assert.equal(panel(), null);
    assert.deepEqual(results, [{ skippedStepIds: ['experiences'] }]);
  } finally {
    await view.unmount();
    anchor.remove();
  }
});

test('all missing targets finish once after acknowledging the last card', async () => {
  const results = [];
  const view = await mount(
    h(
      StrictMode,
      null,
      h(DirectoryTour, {
        open: true,
        steps,
        locateTarget: async () => null,
        onComplete: (result) => results.push(result),
      }),
    ),
  );
  try {
    for (const step of steps) {
      assert.equal(results.length, 0);
      assert.equal(panel().querySelector('h2').textContent, step.title);
      await next();
    }
    assert.equal(panel(), null);
    assert.deepEqual(results, [
      { skippedStepIds: steps.map((step) => step.id) },
    ]);
  } finally {
    await view.unmount();
  }
});

test('a removed anchor keeps the current card without a highlight', async () => {
  const first = makeAnchor();
  const second = makeAnchor();
  const changes = [];
  const view = await mount(
    h(DirectoryTour, {
      open: true,
      steps,
      locateTarget: async (target) => (target === 'memories' ? first : second),
      onStepChange: (index) => changes.push(index),
    }),
  );
  try {
    assert.equal(panel().parentElement.style.visibility, 'visible');
    await act(async () => first.remove());
    await settle();
    assert.deepEqual(changes, []);
    assert.match(panel().textContent, /memories/);
    assert.equal(panel().querySelector('.ov-directory-tour__highlight'), null);
    assert.equal(panel().parentElement.style.visibility, 'visible');
    await next();
    assert.deepEqual(changes, [1]);
    assert.match(panel().textContent, /sessions/);
    assert.equal(panel().parentElement.style.visibility, 'visible');
  } finally {
    await view.unmount();
    first.remove();
    second.remove();
  }
});

test('rejection and zero-sized targets wait for Next in controlled mode', async () => {
  const anchor = makeAnchor();
  anchor.getBoundingClientRect = () => ({ width: 0, height: 0 });
  for (const locateTarget of [
    async () => {
      throw new Error('unavailable');
    },
    async () => anchor,
  ]) {
    const changes = [];
    const view = await mount(
      h(DirectoryTour, {
        open: true,
        steps,
        currentStep: 0,
        locateTarget,
        labels: { unavailable: 'Directory is unavailable in this scope' },
        onStepChange: (index) => changes.push(index),
      }),
    );
    try {
      await settle();
      assert.equal(panel().parentElement.style.visibility, 'visible');
      assert.equal(
        panel().querySelector('[role="status"]').textContent,
        'Directory is unavailable in this scope',
      );
      assert.deepEqual(changes, []);
      await next();
      assert.deepEqual(changes, [1]);
    } finally {
      await view.unmount();
    }
  }
  anchor.remove();
});
