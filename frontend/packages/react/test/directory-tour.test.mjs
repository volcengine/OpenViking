import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderMarkup } from './helpers/csr-render.mjs';
import {
  DirectoryTour,
  getOpenVikingDirectoryTourSteps,
  openVikingDirectoryTourSteps,
} from '@openviking/react/components/directory-tour';

test('unsupported runtime locales fall back to the English preset', () => {
  for (const locale of ['en-GB', 'toString', 'constructor', '__proto__']) {
    assert.equal(
      getOpenVikingDirectoryTourSteps(locale),
      openVikingDirectoryTourSteps,
      locale,
    );
  }
});

test('preset preserves six semantic targets and separates private/shared resources', () => {
  assert.equal(getOpenVikingDirectoryTourSteps(), openVikingDirectoryTourSteps);
  assert.deepEqual(
    openVikingDirectoryTourSteps.map((step) => step.target),
    [
      'userMemories',
      'userSessions',
      'userResources',
      'sharedResources',
      'userExperiences',
      'userPeers',
    ],
  );
  assert.equal(
    new Set(openVikingDirectoryTourSteps.map((step) => step.id)).size,
    6,
  );
  assert.ok(
    openVikingDirectoryTourSteps.every(
      (step) => step.title && step.description,
    ),
  );
  const localized = getOpenVikingDirectoryTourSteps('zh-CN');
  assert.deepEqual(
    localized.map(({ id, target }) => ({ id, target })),
    openVikingDirectoryTourSteps.map(({ id, target }) => ({ id, target })),
  );
  assert.equal(localized[1].title, '轨迹记录（sessions）');
  assert.ok(localized.every((step) => step.title && step.description));
});

test('empty and out-of-range controlled tours render no browser surface', () => {
  for (const props of [
    { steps: [] },
    { steps: openVikingDirectoryTourSteps, currentStep: 99 },
  ]) {
    assert.equal(
      renderMarkup(
        createElement(DirectoryTour, { locale: 'en-US', open: true, ...props }),
      ),
      '',
    );
  }
});
