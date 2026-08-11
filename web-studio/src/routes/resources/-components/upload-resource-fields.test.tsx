// @vitest-environment jsdom

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { UploadResourceFields } from './upload-resource-fields'
import type { SelectedUploadFile } from './upload-resource-fields'

const mocks = vi.hoisted(() => ({
  onDrop: null as ((files: File[]) => void) | null,
  pending: new Map<string, (value: null) => void>(),
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
  toast: Object.assign(vi.fn(), { error: vi.fn() }),
}))

afterEach(() => {
  cleanup()
  mocks.onDrop = null
  mocks.pending.clear()
})

describe('UploadResourceFields', () => {
  it('appends concurrent drops against the latest selected files', async () => {
    const updates: Array<
      (current: SelectedUploadFile[]) => SelectedUploadFile[]
    > = []
    render(
      <UploadResourceFields
        files={[]}
        onFilesChange={(update) => {
          if (typeof update === 'function') updates.push(update)
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

    let current: SelectedUploadFile[] = []
    for (const update of updates) current = update(current)
    expect(current.map(({ file }) => file.name).sort()).toEqual([
      'first.pdf',
      'second.pdf',
    ])
  })
})
