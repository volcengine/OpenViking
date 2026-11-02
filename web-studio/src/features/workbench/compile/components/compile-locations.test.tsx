// @vitest-environment jsdom
import { CompileLocations } from '#/features/workbench/compile/components/compile-locations'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const stat = vi.hoisted(() => vi.fn())
const clipboard = vi.hoisted(() => ({
  copy: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))
vi.mock('#/shared/lib/clipboard', () => ({
  copyTextToClipboard: clipboard.copy,
}))
vi.mock('sonner', () => ({ toast: clipboard }))
vi.mock('#/features/workbench/resources/lib/api', () => ({ fetchFsStat: stat }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    identityScopeKey: 'alice',
    connection: { userId: 'alice' },
  }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
vi.mock('#/features/workbench/compile/components/shared', () => ({
  CompileError: () => <p role="alert">Read failed</p>,
}))
vi.mock(
  '#/features/workbench/resources/components/directory-picker-dialog',
  () => ({
    DirectoryPickerDialog: ({
      open,
      onSelect,
      onOpenChange,
    }: {
      open: boolean
      onSelect: (uri: string) => void
      onOpenChange: (open: boolean) => void
    }) =>
      open ? (
        <div>
          <button onClick={() => onSelect('viking://resources/project/')}>
            Select project
          </button>
          <button onClick={() => onOpenChange(false)}>Cancel selection</button>
        </div>
      ) : null,
  }),
)

const clients: QueryClient[] = []
function Form({ initialSources }: { initialSources: string }) {
  const [sources, setSources] = useState(initialSources)
  const [to, setTo] = useState('')
  return (
    <>
      <CompileLocations
        sources={sources}
        to={to}
        onSources={setSources}
        onTarget={setTo}
        attempted={false}
        sourceError=""
        destinationError=""
      />
      <output data-testid="destination">{to}</output>
      <output data-testid="sources">{sources}</output>
    </>
  )
}
function mount(initialSources = '') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <Form initialSources={initialSources} />
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  vi.clearAllMocks()
  stat.mockReset().mockResolvedValue({ isDir: true })
  clipboard.copy.mockReset().mockResolvedValue(undefined)
})
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
})

it('browses for a destination and allows editing a new folder path', async () => {
  stat.mockRejectedValue(
    Object.assign(new Error('Missing'), { code: 'NOT_FOUND' }),
  )
  const page = mount()
  fireEvent.click(page.getAllByRole('button', { name: 'browse' })[1])
  fireEvent.click(page.getByText('Select project'))
  expect(page.getByTestId('destination').textContent).toBe(
    'viking://resources/project',
  )
  fireEvent.change(page.getByRole('textbox', { name: 'target *' }), {
    target: { value: 'viking://resources/project/项目知识库' },
  })
  expect(page.getByTestId('destination').textContent).toBe(
    'viking://resources/project/项目知识库',
  )
  await waitFor(() => expect(page.getByText('targetNew')).toBeTruthy())
})

it('does not describe permission failures as a new folder', async () => {
  stat.mockRejectedValue(
    Object.assign(new Error('Denied'), { code: 'PERMISSION_DENIED' }),
  )
  const page = mount()
  fireEvent.change(page.getByRole('textbox', { name: 'target *' }), {
    target: { value: 'viking://resources/private' },
  })
  await waitFor(() => expect(page.getByRole('alert')).toBeTruthy())
  expect(page.queryByText('targetNew')).toBeNull()
})

it('edits multiple path rows and removes them independently', () => {
  const page = mount('viking://resources/first')
  fireEvent.click(page.getByRole('button', { name: 'addSource' }))
  const inputs = page.getAllByRole('textbox', { name: 'sourceNumber' })
  expect(inputs).toHaveLength(2)
  fireEvent.change(inputs[1], {
    target: { value: 'viking://resources/second' },
  })
  expect(page.getByTestId('sources').textContent).toBe(
    'viking://resources/first\nviking://resources/second',
  )
  fireEvent.click(page.getAllByRole('button', { name: 'removeSource' })[0])
  expect(page.getByTestId('sources').textContent).toBe(
    'viking://resources/second',
  )
})

it('appends browsed folders without duplicates and preserves paths on cancel', () => {
  const page = mount('viking://user/alice/resources/notes')
  const browse = page.getAllByRole('button', { name: 'browse' })[0]
  fireEvent.click(browse)
  fireEvent.click(page.getByText('Select project'))
  const selected =
    'viking://user/alice/resources/notes\nviking://resources/project'
  expect(page.getByTestId('sources').textContent).toBe(selected)
  fireEvent.click(browse)
  fireEvent.click(page.getByText('Select project'))
  expect(page.getByTestId('sources').textContent).toBe(selected)
  fireEvent.click(browse)
  fireEvent.click(page.getByText('Cancel selection'))
  expect(page.getByTestId('sources').textContent).toBe(selected)
})

it('copies complete current paths without changing the form', async () => {
  const uri = 'viking://resources/项目/会议记录'
  const page = mount(uri)
  const destination = 'viking://resources/知识库'
  fireEvent.change(page.getByRole('textbox', { name: 'target *' }), {
    target: { value: destination },
  })
  fireEvent.click(page.getByRole('button', { name: `copyPath ${uri}` }))
  fireEvent.click(page.getByRole('button', { name: `copyPath ${destination}` }))
  expect(clipboard.copy.mock.calls).toEqual([[uri], [destination]])
  await waitFor(() => expect(clipboard.success).toHaveBeenCalledTimes(2))
  expect(page.getByTestId('sources').textContent).toBe(uri)
  expect(page.getByTestId('destination').textContent).toBe(destination)
})

it('disables empty-path copy and reports clipboard failure without success', async () => {
  const page = mount()
  expect(
    page
      .getAllByRole('button', { name: 'copyPath' })
      .every((button) => button.hasAttribute('disabled')),
  ).toBe(true)
  clipboard.copy.mockRejectedValue(new Error('Clipboard unavailable'))
  fireEvent.change(page.getByRole('textbox', { name: 'sourceNumber' }), {
    target: { value: 'viking://resources/notes' },
  })
  fireEvent.click(
    page.getByRole('button', { name: 'copyPath viking://resources/notes' }),
  )
  await waitFor(() =>
    expect(clipboard.error).toHaveBeenCalledWith('copyFailed'),
  )
  expect(clipboard.success).not.toHaveBeenCalled()
})
