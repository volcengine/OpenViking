// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { SnapshotDiffViewer } from '#/features/workbench/filesystem/components/snapshot-diff-viewer'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(cleanup)

it('marks the missing version while retaining the added file diff', () => {
  render(
    <SnapshotDiffViewer
      diff={'--- old\n+++ new\n@@ -0,0 +1 @@\n+new content\n'}
      missingBase
    />,
  )
  expect(screen.getByText('versionMissingFile')).toBeTruthy()
  expect(screen.getByText('new content')).toBeTruthy()
  expect(screen.getByText('+1')).toBeTruthy()
})
