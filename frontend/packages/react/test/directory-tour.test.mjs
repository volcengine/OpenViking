import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderMarkup } from './helpers/csr-render.mjs';
import {
  DirectoryTour,
  openVikingDirectoryTourSteps,
} from '@openviking/react/components/directory-tour';

test('preset preserves six semantic targets and separates private/shared resources', () => {
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
