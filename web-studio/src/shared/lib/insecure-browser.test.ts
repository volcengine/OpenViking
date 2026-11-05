// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { createRandomUuid } from '#/shared/lib/browser-crypto'
import { copyTextToClipboard } from '#/shared/lib/clipboard'

afterEach(() => vi.unstubAllGlobals())

it('creates a UUID when randomUUID is unavailable on an HTTP origin', () => {
  vi.stubGlobal('crypto', {
    getRandomValues: (bytes: Uint8Array) => bytes.fill(42),
  })
  expect(createRandomUuid()).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  )
})

it('copies using the document fallback when the Clipboard API is unavailable', async () => {
  vi.stubGlobal('navigator', {})
  const copy = vi.fn(() => true)
  vi.stubGlobal('document', document)
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    value: copy,
  })
  await copyTextToClipboard('example')
  expect(copy).toHaveBeenCalledWith('copy')
  expect(document.querySelector('textarea')).toBeNull()
  delete (document as unknown as { execCommand?: unknown }).execCommand
})
