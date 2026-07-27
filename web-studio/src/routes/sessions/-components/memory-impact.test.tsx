// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MemoryImpact } from './memory-impact'
import type { SessionMemoryDiff } from '#/lib/sessions/memory-diff'
import type { SessionMeta } from '@ov-server/api/v1/sessions'

const { useSessionMemoryDiffsMock } = vi.hoisted(() => ({
  useSessionMemoryDiffsMock: vi.fn(),
}))

vi.mock('#/lib/sessions/use-sessions', () => ({
  useSessionMemoryDiffs: useSessionMemoryDiffsMock,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { resolvedLanguage: 'en' },
    t: (key: string) => key,
  }),
}))

afterEach(cleanup)

const diffs: SessionMemoryDiff[] = [
  {
    archiveId: 'archive-1',
    archiveUri: 'viking://session/archive-1',
    extractedAt: '2026-07-26T00:00:00Z',
    operations: [
      {
        after: 'profile',
        kind: 'add',
        memoryType: 'profile',
        uri: 'viking://user/profile',
      },
      {
        after: 'preference',
        kind: 'delete',
        memoryType: 'preferences',
        uri: 'viking://user/preferences',
      },
    ],
    summary: { adds: 1, deletes: 1, updates: 0 },
  },
  {
    archiveId: 'archive-2',
    archiveUri: 'viking://session/archive-2',
    extractedAt: '2026-07-26T01:00:00Z',
    operations: [
      {
        after: 'preference',
        kind: 'update',
        memoryType: 'preferences',
        uri: 'viking://user/preferences/second',
      },
    ],
    summary: { adds: 0, deletes: 0, updates: 1 },
  },
]

describe('MemoryImpact', () => {
  it('filters archives and recomputes per-archive counts by memory type', () => {
    useSessionMemoryDiffsMock.mockReturnValue({
      data: diffs,
      isError: false,
      isLoading: false,
      refetch: vi.fn(),
    })

    render(
      <MemoryImpact session={{ commit_count: 2 } as unknown as SessionMeta} />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'impact.open' }))
    fireEvent.click(screen.getByRole('tab', { name: 'profile' }))

    const visibleArchive = screen.getByText('archive-1').closest('section')
    expect(visibleArchive?.textContent).toContain('+1')
    expect(visibleArchive?.textContent).not.toContain('−1')
    expect(screen.queryByText('archive-2')).toBeNull()
  })
})
