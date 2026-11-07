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
import i18n from '#/i18n'
import { SessionsPage } from './index'
import { ThreadList } from './components/thread-list'

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  navigate: vi.fn(),
  setTitle: vi.fn(),
  error: vi.fn(),
}))
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getRouteApi: () => ({ useSearch: () => ({}) }),
  useNavigate: () => mocks.navigate,
  Link: ({ children }: { children: React.ReactNode }) => (
    <span>{children}</span>
  ),
}))
vi.mock('sonner', () => ({ toast: { error: mocks.error } }))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: 'opensource' }),
}))
vi.mock('#/features/workbench/sessions/lib/use-session-titles', () => ({
  useSessionTitles: () => ({
    getTitle: (id: string) => id,
    removeTitle: vi.fn(),
    setTitle: mocks.setTitle,
  }),
}))
vi.mock('#/features/workbench/sessions/lib/use-sessions', () => ({
  useCreateSession: () => ({ mutateAsync: mocks.create, isPending: false }),
  useDeleteSession: () => ({}),
  useSessionListByRecency: () => ({ data: [], isPending: false }),
}))
vi.mock('#/features/workbench/sessions/components/thread', () => ({
  Thread: () => null,
}))

beforeEach(() => vi.resetAllMocks())
afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('en')
})

it.each([
  ['button', 'en'],
  ['shortcut', 'en'],
  ['button', 'zh-CN'],
  ['shortcut', 'zh-CN'],
] as const)(
  'reports a failed %s creation in %s and permits a successful retry',
  async (entrypoint, language) => {
    await i18n.changeLanguage(language)
    mocks.create
      .mockRejectedValueOnce(new Error('Service unavailable'))
      .mockResolvedValueOnce({ session_id: 'created' })
    if (entrypoint === 'button') render(<ThreadList />)
    else render(<SessionsPage />)
    const user = userEvent.setup()
    const create = async () => {
      if (entrypoint === 'button')
        await user.click(
          screen.getByRole('button', {
            name: i18n.t('sessions:threadList.newSession'),
          }),
        )
      else fireEvent.keyDown(document, { key: 'n', ctrlKey: true })
    }
    await create()
    await waitFor(() =>
      expect(mocks.error).toHaveBeenCalledWith(
        i18n.t('sessions:threadList.createFailed', {
          error: 'Service unavailable',
        }),
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
      i18n.t('sessions:threadList.newSession'),
    )
  },
)
