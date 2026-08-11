// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { UploadResourceFields } from './upload-resource-fields'
import type { SelectedUploadFile } from './upload-resource-fields'
import { MAX_UPLOAD_FILES } from '../-lib/upload'

const mocks = vi.hoisted(() => ({
  onDrop: null as ((files: File[]) => void) | null,
  pending: new Map<string, (value: null) => void>(),
  toast: vi.fn(),
}))

vi.mock('file-type', () => ({
  fileTypeFromBlob: (file: File) =>
    new Promise((resolve) => {
      mocks.pending.set(file.name, () => resolve(undefined))
    }),
}))

vi.mock('react-dropzone', () => ({
  useDropzone: ({ onDrop }: { onDrop: (files: File[]) => void }) => {
    mocks.onDrop = onDrop
    return {
      getInputProps: () => ({}),
      getRootProps: () => ({}),
      isDragActive: false,
    }
  },
}))

vi.mock('sonner', () => ({
  toast: Object.assign(mocks.toast, { error: vi.fn() }),
}))

afterEach(() => {
  cleanup()
  mocks.onDrop = null
  mocks.pending.clear()
  vi.clearAllMocks()
})

describe('UploadResourceFields', () => {
  it('appends concurrent drops against the latest selected files', async () => {
    let current: SelectedUploadFile[] = []
    render(
      <UploadResourceFields
        files={[]}
        onFilesChange={(update) => {
          current = typeof update === 'function' ? update(current) : update
        }}
        t={(key) => key}
      />,
    )

    mocks.onDrop?.([new File(['first'], 'first.pdf')])
    mocks.onDrop?.([new File(['second'], 'second.pdf')])
    mocks.pending.get('second.pdf')?.(null)
    await Promise.resolve()
    await Promise.resolve()
    mocks.pending.get('first.pdf')?.(null)
    await Promise.resolve()
    await Promise.resolve()

    expect(current.map(({ file }) => file.name).sort()).toEqual([
      'first.pdf',
      'second.pdf',
    ])
  })

  it('reports truncation when concurrent drops exceed the latest limit', async () => {
    let current: SelectedUploadFile[] = Array.from(
      { length: MAX_UPLOAD_FILES - 1 },
      (_, index) => ({
        id: `existing-${index}`,
        file: new File(['existing'], `existing-${index}.pdf`),
        fileType: null,
      }),
    )
    render(
      <UploadResourceFields
        files={current}
        onFilesChange={(update) => {
          current = typeof update === 'function' ? update(current) : update
        }}
        t={(key) => key}
      />,
    )

    mocks.onDrop?.([new File(['first'], 'first.pdf')])
    mocks.onDrop?.([new File(['second'], 'second.pdf')])
    mocks.pending.get('first.pdf')?.(null)
    mocks.pending.get('second.pdf')?.(null)

    await waitFor(() => expect(current).toHaveLength(MAX_UPLOAD_FILES))
    expect(mocks.toast).toHaveBeenCalledWith('tooManyFiles', {
      duration: 2500,
    })
  })

  it('removes from the latest files after an asynchronous append', async () => {
    const existing: SelectedUploadFile = {
      id: 'existing',
      file: new File(['existing'], 'existing.pdf'),
      fileType: null,
    }
    let current = [existing]
    render(
      <UploadResourceFields
        files={current}
        onFilesChange={(update) => {
          current = typeof update === 'function' ? update(current) : update
        }}
        t={(key) => key}
      />,
    )

    mocks.onDrop?.([new File(['new'], 'new.pdf')])
    mocks.pending.get('new.pdf')?.(null)
    await Promise.resolve()
    await Promise.resolve()
    fireEvent.click(screen.getByRole('button', { name: 'fileInfo.remove' }))

    expect(current.map(({ file }) => file.name)).toEqual(['new.pdf'])
  })
})
