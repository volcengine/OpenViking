// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'

import { ContextTree } from './context-explorer'

const { useVikingFsListMock } = vi.hoisted(() => ({
  useVikingFsListMock: vi.fn(),
}))

vi.mock('#/routes/resources/-hooks/viking-fm', () => ({
  useVikingFsList: useVikingFsListMock,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { name?: string }) => {
      if (key === 'explorer.title') return 'Context tree'
      if (key === 'explorer.expandDirectory') {
        return `Expand ${options?.name}`
      }
      if (key === 'explorer.collapseDirectory') {
        return `Collapse ${options?.name}`
      }
      if (key === 'explorer.namespaces.resources') {
        return 'External resources the Agent can reference'
      }
      return key
    },
  }),
}))

const directory: VikingFsEntry = {
  abstract: '',
  isDir: true,
  modTime: '2026-08-24 12:00',
  modTimestamp: 1,
  name: 'resources',
  overview: '',
  size: '',
  sizeBytes: null,
  uri: 'viking://resources',
}

const file: VikingFsEntry = {
  abstract: '',
  isDir: false,
  modTime: '2026-08-24 12:00',
  modTimestamp: 1,
  name: 'guide.md',
  overview: '',
  size: '1 KB',
  sizeBytes: 1024,
  uri: 'viking://resources/guide.md',
}

beforeEach(() => {
  useVikingFsListMock.mockImplementation((uri: string) => ({
    data: { entries: uri === 'viking://' ? [directory] : [file] },
    isError: false,
    isLoading: false,
  }))
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    callback(0)
    return 1
  })
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

function renderTree({
  currentUri = 'viking://',
  expandedKeys = new Set<string>(),
  selectedFileUri = null,
}: {
  currentUri?: string
  expandedKeys?: Set<string>
  selectedFileUri?: string | null
} = {}) {
  const onExpandedKeysChange = vi.fn()
  const onSelectDirectory = vi.fn()
  const onSelectFile = vi.fn()

  render(
    <ContextTree
      currentUri={currentUri}
      expandedKeys={expandedKeys}
      onExpandedKeysChange={onExpandedKeysChange}
      onSelectDirectory={onSelectDirectory}
      onSelectFile={onSelectFile}
      selectedFileUri={selectedFileUri}
    />,
  )

  return { onExpandedKeysChange, onSelectDirectory, onSelectFile }
}

describe('ContextTree keyboard semantics', () => {
  it('renders native controls in a labelled, nested list', () => {
    renderTree({
      expandedKeys: new Set([directory.uri]),
      selectedFileUri: file.uri,
    })

    const rootList = screen.getByRole('list', { name: 'Context tree' })
    const disclosure = screen.getByRole('button', {
      name: 'Collapse resources',
    })
    const directorySelection = screen.getByRole('button', {
      name: 'resources',
    })
    const fileSelection = screen.getByRole('button', { name: 'guide.md' })

    expect(rootList.contains(directorySelection)).toBe(true)
    expect(disclosure.getAttribute('aria-expanded')).toBe('true')
    expect(disclosure.getAttribute('title')).toBe('Collapse resources')
    expect(directorySelection.tabIndex).toBe(0)
    expect(fileSelection.tabIndex).toBe(0)
    expect(fileSelection.getAttribute('aria-current')).toBe('location')

    fileSelection.focus()
    expect(document.activeElement).toBe(fileSelection)

    const directoryItem = directorySelection.closest('li')
    const childList = fileSelection.closest('ul')
    expect(directoryItem).toBeTruthy()
    expect(childList?.parentElement).toBe(directoryItem)
  })

  it('activates disclosure with Enter without selecting the directory', async () => {
    const user = userEvent.setup()
    const { onExpandedKeysChange, onSelectDirectory, onSelectFile } =
      renderTree()

    const disclosure = screen.getByRole('button', {
      name: 'Expand resources',
    })
    expect(disclosure.getAttribute('aria-expanded')).toBe('false')

    await user.tab()
    expect(document.activeElement).toBe(disclosure)
    await user.keyboard('{Enter}')

    expect(onExpandedKeysChange).toHaveBeenCalledOnce()
    expect(onExpandedKeysChange.mock.calls[0]?.[0]).toEqual(
      new Set([directory.uri]),
    )
    expect(onSelectDirectory).not.toHaveBeenCalled()
    expect(onSelectFile).not.toHaveBeenCalled()
  })

  it('selects directories with Space and files with Enter', async () => {
    const user = userEvent.setup()
    const { onExpandedKeysChange, onSelectDirectory, onSelectFile } =
      renderTree({ expandedKeys: new Set([directory.uri]) })

    const disclosure = screen.getByRole('button', {
      name: 'Collapse resources',
    })
    const directorySelection = screen.getByRole('button', {
      name: 'resources',
    })
    const fileSelection = screen.getByRole('button', { name: 'guide.md' })

    await user.tab()
    expect(document.activeElement).toBe(disclosure)
    await user.tab()
    expect(document.activeElement).toBe(directorySelection)
    await user.keyboard(' ')
    await user.tab()
    expect(document.activeElement).toBe(fileSelection)
    await user.keyboard('{Enter}')

    expect(onSelectDirectory).toHaveBeenCalledOnce()
    expect(onSelectDirectory).toHaveBeenCalledWith(
      expect.objectContaining({ uri: directory.uri }),
    )
    expect(onSelectFile).toHaveBeenCalledOnce()
    expect(onSelectFile).toHaveBeenCalledWith(file)
    expect(onExpandedKeysChange).not.toHaveBeenCalled()
  })
})
