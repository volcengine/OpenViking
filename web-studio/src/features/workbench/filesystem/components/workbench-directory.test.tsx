// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkbenchDirectory } from './workbench-directory'

const { list } = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: { userId: 'alice' },
    identityScopeKey: 'alice',
  }),
}))
vi.mock('#/features/workbench/sessions/lib/use-session-titles', () => ({
  useSessionTitles: () => ({ getTitle: (name: string) => name }),
}))
vi.mock('#/features/workbench/resources/hooks/viking-fm', () => ({
  useVikingFsList: list,
}))
vi.mock('#/features/workbench/sessions/components/thread', () => ({
  Thread: ({ sessionId }: { sessionId: string }) => (
    <div data-testid="thread">{sessionId}</div>
  ),
}))
vi.mock('#/features/workbench/agent-experience', () => ({
  AgentExperienceList: () => <div data-testid="experiences" />,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

function entry(uri: string, name: string, isDir: boolean) {
  return {
    uri,
    name,
    isDir,
    abstract: '',
    overview: '',
    size: '',
    sizeBytes: null,
    modTime: '',
    modTimestamp: 0,
  }
}
beforeEach(() =>
  list.mockReturnValue({ data: { entries: [] }, isSuccess: true }),
)
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('workbench directory previews', () => {
  it('opens the current user session as a conversation', () => {
    render(
      <WorkbenchDirectory
        uri="viking://user/alice/sessions/s1/"
        onNavigate={vi.fn()}
      />,
    )
    expect(screen.getByTestId('thread').textContent).toBe('s1')
  })
  it('keeps another user session in file browsing without mounting chat', async () => {
    const uri = 'viking://user/bob/sessions/s1/'
    list.mockReturnValue({
      data: {
        entries: [entry(`${uri}messages.jsonl`, 'messages.jsonl', false)],
      },
      isSuccess: true,
    })
    const onNavigate = vi.fn()
    render(<WorkbenchDirectory uri={uri} onNavigate={onNavigate} />)
    expect(screen.queryByTestId('thread')).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'ide.sessionPreview' }),
    ).toBeNull()
    await userEvent.click(
      screen.getByRole('button', { name: 'messages.jsonl' }),
    )
    expect(onNavigate).toHaveBeenCalledWith(`${uri}messages.jsonl`)
  })
  it('preserves subdirectory and file navigation in a mixed experience directory', async () => {
    const uri = 'viking://user/alice/memories/experiences/'
    list.mockReturnValue({
      data: {
        entries: [
          entry(`${uri}default/`, 'default', true),
          entry(`${uri}review.md`, 'review.md', false),
        ],
      },
      isSuccess: true,
    })
    const onNavigate = vi.fn()
    render(<WorkbenchDirectory uri={uri} onNavigate={onNavigate} />)
    expect(screen.queryByTestId('experiences')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'default' }))
    expect(onNavigate).toHaveBeenCalledWith(`${uri}default/`)
    expect(screen.getByRole('button', { name: 'review.md' })).toBeTruthy()
  })
  it('uses the shared experience list for a leaf directory', () => {
    const uri = 'viking://user/alice/memories/experiences/default/'
    list.mockReturnValue({
      data: { entries: [entry(`${uri}review.md`, 'review.md', false)] },
      isSuccess: true,
    })
    render(<WorkbenchDirectory uri={uri} onNavigate={vi.fn()} />)
    expect(screen.getByTestId('experiences')).toBeTruthy()
  })
  it('does not show an empty experience list before the directory loads', () => {
    list.mockReturnValue({ isLoading: true, isSuccess: false })
    render(
      <WorkbenchDirectory
        uri="viking://user/alice/memories/experiences/"
        onNavigate={vi.fn()}
      />,
    )
    expect(screen.getByRole('status')).toBeTruthy()
    expect(screen.queryByTestId('experiences')).toBeNull()
  })
})
