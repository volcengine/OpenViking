import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, StrictMode } from 'react';
import { DirectoryTour } from '@openviking/react/components/directory-tour';
import { deferred, mount, settle } from './helpers/module-dom.mjs';

const steps = ['memories', 'sessions', 'peers'].map((id) => ({
  id,
  target: id,
  title: id,
  description: `About ${id}`,
}));
const panel = () => document.querySelector('.ov-directory-tour');
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

test('pending lookup stays hidden; missing steps skip and retain progress', async () => {
  const pending = deferred();
  const anchor = makeAnchor();
  const changes = [];
  const results = [];
  const view = await mount(
    h(DirectoryTour, {
      open: true,
      steps,
      locateTarget: (target) =>
        target === 'memories'
          ? pending.promise
          : Promise.resolve(target === 'sessions' ? null : anchor),
      onStepChange: (index) => changes.push(index),
      onComplete: (result) => results.push(result),
    }),
  );
  try {
    assert.equal(panel().style.visibility, 'hidden');
    await act(async () => pending.resolve(null));
    await settle();
    assert.deepEqual(changes, [1, 2]);
    assert.equal(panel().style.visibility, 'visible');
    assert.match(panel().textContent, /peers/);
    assert.match(panel().textContent, /3\/3/);
    await act(async () => panel().querySelector('button').click());
    assert.equal(panel(), null);
    assert.deepEqual(results, [{ skippedStepIds: ['memories', 'sessions'] }]);
  } finally {
    await view.unmount();
    anchor.remove();
  }
});

test('all missing targets finish once under StrictMode, even if host stays open', async () => {
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
    assert.equal(panel(), null);
    assert.deepEqual(results, [
      { skippedStepIds: steps.map((step) => step.id) },
    ]);
  } finally {
    await view.unmount();
  }
});

test('timeout aborts pending lookup and ignores its late resolution', async () => {
  const pending = deferred();
  const anchor = makeAnchor();
  let signal;
  const results = [];
  const view = await mount(
    h(DirectoryTour, {
      open: true,
      steps: steps.slice(0, 1),
      locateTimeout: 15,
      locateTarget: (_, value) => {
        signal = value;
        return pending.promise;
      },
      onComplete: (result) => results.push(result),
    }),
  );
  try {
    await act(async () => new Promise((resolve) => setTimeout(resolve, 25)));
    await settle();
    assert.equal(signal.aborted, true);
    assert.equal(panel(), null);
    await act(async () => pending.resolve(anchor));
    await settle();
    assert.equal(panel(), null);
    assert.equal(results.length, 1);
  } finally {
    await view.unmount();
    anchor.remove();
  }
});

test('a removed anchor skips to the next target without leaving a floating card', async () => {
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
    assert.equal(panel().style.visibility, 'visible');
    await act(async () => first.remove());
    await settle();
    assert.deepEqual(changes, [1]);
    assert.match(panel().textContent, /sessions/);
    assert.equal(panel().style.visibility, 'visible');
  } finally {
    await view.unmount();
    first.remove();
    second.remove();
  }
});

test('rejection and zero-sized targets skip once in controlled mode', async () => {
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
        onStepChange: (index) => changes.push(index),
      }),
    );
    try {
      await settle();
      assert.equal(panel().style.visibility, 'hidden');
      assert.deepEqual(changes, [1]);
    } finally {
      await view.unmount();
    }
  }
  anchor.remove();
});
