// @vitest-environment jsdom
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import {
  act,
  cleanup,
  fireEvent,
  render,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ContextLink, useCompileSkillName } from './context-link'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const api = vi.hoisted(() => ({
  fetchCompileSkills: vi.fn(),
  fetchFsStat: vi.fn(),
}))
vi.mock('../-lib/api', () => ({ fetchCompileSkills: api.fetchCompileSkills }))
vi.mock('#/routes/resources/-lib/api', () => ({ fetchFsStat: api.fetchFsStat }))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'alice' }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    search,
    children,
  }: {
    to: string
    search: Record<string, string>
    children: ReactNode
  }) => <a href={`${to}?${new URLSearchParams(search)}`}>{children}</a>,
}))
const clients: QueryClient[] = []
function mount(children: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>{children}</QueryClientProvider>,
  )
}
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
  vi.resetAllMocks()
})

it('opens file materials in preview and directory materials in the explorer', async () => {
  api.fetchFsStat.mockImplementation(async (uri: string) => ({
    isDir: uri.endsWith('/folder'),
  }))
  const page = mount(
    <>
      <ContextLink uri="viking://resources/folder/report.md" resolveFile />
      <ContextLink uri="viking://resources/folder" resolveFile />
    </>,
  )
  await waitFor(() => {
    const link = new URL(
      page.getByRole('link', { name: 'report.md' }).getAttribute('href')!,
      'http://localhost',
    )
    expect(link.pathname).toBe('/playground')
    expect(link.searchParams.get('file')).toBe(
      'viking://resources/folder/report.md',
    )
  })
  const directory = new URL(
    page.getByRole('link', { name: 'folder' }).getAttribute('href')!,
    'http://localhost',
  )
  expect(directory.searchParams.get('uri')).toBe('viking://resources/folder')
  expect(directory.searchParams.has('file')).toBe(false)
})

function SkillLink() {
  const name = useCompileSkillName()
  const uri = 'viking://agent/skills/digest/'
  return <ContextLink uri={uri}>{name(uri)}</ContextLink>
}

it('uses the skill display name and keeps its original URI for navigation', async () => {
  api.fetchCompileSkills.mockResolvedValue([
    { uri: 'viking://agent/skills/digest', name: 'Discussion digest' },
  ])
  const page = mount(<SkillLink />)
  const link = await page.findByRole('link', { name: 'Discussion digest' })
  expect(
    new URL(link.getAttribute('href')!, 'http://localhost').searchParams.get(
      'uri',
    ),
  ).toBe('viking://agent/skills/digest/')
})

it('keeps a readable, usable link when skill metadata is unavailable', async () => {
  api.fetchCompileSkills.mockRejectedValue(new Error('Unavailable'))
  const page = mount(<SkillLink />)
  expect(page.getByRole('link', { name: 'digest' })).toBeTruthy()
})

it('withholds navigation until file metadata resolves', async () => {
  let resolve!: (entry: { isDir: boolean }) => void
  api.fetchFsStat.mockReturnValue(
    new Promise((done) => {
      resolve = done
    }),
  )
  const uri = 'viking://resources/folder/report.md'
  const page = mount(<ContextLink uri={uri} resolveFile />)
  expect(page.queryByRole('link')).toBeNull()
  expect(
    (page.getByRole('button', { name: 'report.md' }) as HTMLButtonElement)
      .disabled,
  ).toBe(true)

  await act(async () => resolve({ isDir: false }))
  const link = await page.findByRole('link', { name: 'report.md' })
  const target = new URL(link.getAttribute('href')!, 'http://localhost')
  expect(target.searchParams.get('uri')).toBe('viking://resources/folder/')
  expect(target.searchParams.get('file')).toBe(uri)
})

it('offers retry after metadata failure without exposing a directory link', async () => {
  let resolve!: (entry: { isDir: boolean }) => void
  api.fetchFsStat
    .mockRejectedValueOnce(new Error('Unavailable'))
    .mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done
        }),
    )
  const uri = 'viking://resources/folder/report.md'
  const page = mount(<ContextLink uri={uri} resolveFile />)
  const retry = await page.findByRole('button', { name: `retry: ${uri}` })
  expect(page.queryByRole('link')).toBeNull()
  fireEvent.click(retry)
  await waitFor(() => expect(api.fetchFsStat).toHaveBeenCalledTimes(2))
  expect(page.queryByRole('link')).toBeNull()
  expect((page.getByRole('button') as HTMLButtonElement).disabled).toBe(true)

  await act(async () => resolve({ isDir: false }))
  const link = await page.findByRole('link', { name: 'report.md' })
  expect(
    new URL(link.getAttribute('href')!, 'http://localhost').searchParams.get(
      'file',
    ),
  ).toBe(uri)
})

it('keeps known directory links immediately available without requesting metadata', () => {
  const page = mount(<ContextLink uri="viking://resources/output" />)
  expect(page.getByRole('link', { name: 'output' })).toBeTruthy()
  expect(api.fetchFsStat).not.toHaveBeenCalled()
})
