// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Route } from './index'
import { ThreadList } from './-components/thread-list'

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  navigate: vi.fn(),
  setTitle: vi.fn(),
  error: vi.fn(),
}))
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  createFileRoute: () => (options: object) => ({
    ...options,
    useSearch: () => ({}),
  }),
  useNavigate: () => mocks.navigate,
  Link: ({ children }: { children: React.ReactNode }) => (
    <span>{children}</span>
  ),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'en' },
    t: (key: string, options?: { error?: string }) =>
      options?.error ? `${key}: ${options.error}` : key,
  }),
}))
vi.mock('sonner', () => ({ toast: { error: mocks.error } }))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))
vi.mock('#/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: 'opensource' }),
}))
vi.mock('#/lib/sessions/use-session-titles', () => ({
  useSessionTitles: () => ({
    getTitle: (id: string) => id,
    removeTitle: vi.fn(),
    setTitle: mocks.setTitle,
  }),
}))
vi.mock('#/lib/sessions/use-sessions', () => ({
  useCreateSession: () => ({ mutateAsync: mocks.create, isPending: false }),
  useDeleteSession: () => ({}),
  useSessionListByRecency: () => ({ data: [], isPending: false }),
}))
vi.mock('./-components/thread', () => ({ Thread: () => null }))

beforeEach(() => vi.resetAllMocks())
afterEach(cleanup)

it.each(['button', 'shortcut'] as const)(
  'reports a failed %s creation and permits a successful retry',
  async (entrypoint) => {
    mocks.create
      .mockRejectedValueOnce(new Error('Service unavailable'))
      .mockResolvedValueOnce({ session_id: 'created' })
    if (entrypoint === 'button') render(<ThreadList />)
    else {
      const Page = (
        Route as unknown as {
          component: React.ComponentType & { preload?: () => Promise<unknown> }
        }
      ).component
      await Page.preload?.()
      render(<Page />)
    }
    const user = userEvent.setup()
    const create = async () => {
      if (entrypoint === 'button')
        await user.click(
          screen.getByRole('button', { name: 'threadList.newSession' }),
        )
      else fireEvent.keyDown(document, { key: 'n', ctrlKey: true })
    }
    await create()
    await waitFor(() =>
      expect(mocks.error).toHaveBeenCalledWith(
        'threadList.createFailed: Service unavailable',
      ),
    )
    expect(mocks.navigate).not.toHaveBeenCalled()
    expect(mocks.setTitle).not.toHaveBeenCalled()
    await create()
    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith({
        to: '/sessions',
        search: { s: 'created' },
      }),
    )
    expect(mocks.error).toHaveBeenCalledTimes(1)
    expect(mocks.setTitle).toHaveBeenCalledWith(
      'created',
      'threadList.newSession',
    )
  },
)
