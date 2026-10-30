// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useUserList } from './use-user-list'

describe('user list request state', () => {
  it('resets the page when searching or changing page size', () => {
    const { result } = renderHook(() => useUserList('account'))
    expect(result.current.pageSize).toBe(20)
    act(() => result.current.setPage(142))
    expect(result.current.page).toBe(142)
    act(() => result.current.setSearch('user-2833'))
    expect(result.current.page).toBe(1)
    expect(result.current.search).toBe('user-2833')
    act(() => result.current.setPage(2))
    act(() => result.current.setPageSize(100))
    expect(result.current.page).toBe(1)
    expect(result.current.pageSize).toBe(100)
  })

  it('resets search and page when switching accounts', () => {
    const { result, rerender } = renderHook((scope) => useUserList(scope), {
      initialProps: 'account',
    })
    act(() => result.current.setSearch('alice'))
    act(() => result.current.setPage(2))
    rerender('other-account')
    expect(result.current.search).toBe('')
    expect(result.current.page).toBe(1)
  })
})
