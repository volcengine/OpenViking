// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useDocumentGuard } from './use-document-guard'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('document navigation guard', () => {
  it('keeps unsaved protection when the current document is reselected', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { result } = renderHook(() => useDocumentGuard('file-a', 'Discard?'))
    act(() => result.current.onDirtyChange(true))
    act(() => expect(result.current.confirmNavigation('file-a')).toBe(false))
    expect(confirm).not.toHaveBeenCalled()
    expect(result.current.dirty).toBe(true)
    expect(result.current.dirtyRef.current).toBe(true)

    confirm.mockReturnValue(false)
    act(() => expect(result.current.confirmNavigation('file-b')).toBe(false))
    expect(confirm).toHaveBeenCalledOnce()
    expect(result.current.dirtyRef.current).toBe(true)
  })

  it('clears protection only after approving a different document', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { result } = renderHook(() => useDocumentGuard('file-a', 'Discard?'))
    act(() => result.current.onDirtyChange(true))
    act(() => expect(result.current.confirmNavigation('file-b')).toBe(true))
    expect(result.current.dirty).toBe(false)
    expect(result.current.dirtyRef.current).toBe(false)
  })

  it('protects route departures without a document target', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { result } = renderHook(() => useDocumentGuard('file-a', 'Discard?'))
    act(() => result.current.onDirtyChange(true))
    act(() => expect(result.current.confirmNavigation()).toBe(false))
    expect(result.current.dirty).toBe(true)
  })
})
