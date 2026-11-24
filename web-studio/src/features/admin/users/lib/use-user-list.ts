import { useState } from 'react'

export function useUserList(scope: string) {
  const [state, setState] = useState({
    scope,
    search: '',
    page: 1,
    pageSize: 20,
  })
  const current =
    state.scope === scope
      ? state
      : { scope, search: '', page: 1, pageSize: state.pageSize }
  if (state.scope !== scope) setState(current)
  return {
    ...current,
    setSearch: (search: string) => setState({ ...current, search, page: 1 }),
    setPage: (page: number) =>
      setState({ ...current, page: Math.max(1, page) }),
    setPageSize: (pageSize: number) =>
      setState({ ...current, pageSize, page: 1 }),
  }
}
