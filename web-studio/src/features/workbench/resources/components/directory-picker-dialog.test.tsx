// @vitest-environment jsdom
import { DirectoryPickerDialog } from '#/features/workbench/resources/components/directory-picker-dialog'
import zh from '#/i18n/locales/zh-CN/resources'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import type * as ReactI18next from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const list = vi.hoisted(() => vi.fn())
vi.mock('#/shared/api/ov-client/index', () => ({
  getFsLs: list,
  getOvResult: (result: unknown) => result,
  isOvClientError: () => false,
}))
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof ReactI18next>()),
  useTranslation: () => ({
    t: (key: string, values: Record<string, unknown> = {}) => {
      const template =
        key
          .split('.')
          .reduce((value: any, part) => value?.[part], zh.addResource) ?? key
      return String(template).replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
        String(values[name] ?? ''),
      )
    },
  }),
}))

const root = 'viking://user/alice/resources/'
const shared = 'viking://resources/'
const folders: Record<string, string[]> = {
  [root]: ['project/', 'notes/'],
  [`${root}project/`]: ['research/'],
  [`${root}project/research/`]: [],
  [shared]: ['team/'],
}
const clients: QueryClient[] = []

beforeEach(() => {
  list
    .mockReset()
    .mockImplementation(({ query }: { query: { uri: string } }) =>
      Promise.resolve(folders[query.uri] ?? []),
    )
})
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
})

function mount() {
  const onSelect = vi.fn()
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  clients.push(client)
  function Harness({ revision }: { revision: number }) {
    const [open, setOpen] = useState(false)
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>
          Open picker {revision}
        </button>
        <DirectoryPickerDialog
          open={open}
          onOpenChange={setOpen}
          value={root}
          userResourceRoot={root}
          identityScopeKey="alice"
          onSelect={onSelect}
        />
      </>
    )
  }
  const element = (revision: number) => (
    <QueryClientProvider client={client}>
      <Harness revision={revision} />
    </QueryClientProvider>
  )
  const page = render(element(0))
  return {
    onSelect,
    rerender: () => page.rerender(element(1)),
  }
}

it('preserves single-selection browsing and confirms only the current directory', async () => {
  const user = userEvent.setup()
  const page = mount()
  await user.click(screen.getByRole('button', { name: 'Open picker 0' }))
  await user.click(await screen.findByRole('button', { name: 'project' }))
  await screen.findByRole('button', { name: 'research' })
  page.rerender()
  expect(screen.getByRole('button', { name: 'research' })).toBeTruthy()
  expect(screen.queryByRole('checkbox')).toBeNull()
  expect(page.onSelect).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '选择' }))
  expect(page.onSelect).toHaveBeenCalledExactlyOnceWith(`${root}project/`)
})

it('discards navigation when cancelled and reopens at the original directory', async () => {
  const user = userEvent.setup()
  const page = mount()
  await user.click(screen.getByRole('button', { name: 'Open picker 0' }))
  await user.click(await screen.findByRole('button', { name: 'project' }))
  await screen.findByRole('button', { name: 'research' })
  await user.click(screen.getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(page.onSelect).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Open picker 0' }))
  expect(screen.getByRole('button', { name: 'notes' })).toBeTruthy()
  await user.click(screen.getByRole('button', { name: '选择' }))
  expect(page.onSelect).toHaveBeenCalledExactlyOnceWith(root)
})
