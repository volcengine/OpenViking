import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { DirectoryTour as RootTour } from '@openviking/react';
import {
  DirectoryTour,
  openVikingDirectoryTourSteps,
} from '@openviking/react/components/directory-tour';

test('root and subpath share one implementation and SSR never resolves browser anchors', () => {
  assert.equal(RootTour, DirectoryTour);
  const locateTarget = () => {
    throw new Error('SSR must not access anchors');
  };
  for (const open of [true, false]) {
    assert.equal(
      renderToStaticMarkup(
        createElement(DirectoryTour, {
          locale: 'en-US',
          open,
          steps: openVikingDirectoryTourSteps,
          locateTarget,
        }),
      ),
      '',
    );
  }
});

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
      renderToStaticMarkup(
        createElement(DirectoryTour, { locale: 'en-US', open: true, ...props }),
      ),
      '',
    );
  }
});
