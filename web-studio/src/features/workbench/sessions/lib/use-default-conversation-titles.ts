import { fetchSessionFirstTitle } from '#/features/workbench/sessions/lib/api'
import { useSessionTitles } from '#/features/workbench/sessions/lib/use-session-titles'
import { useQueries } from '@tanstack/react-query'
import { useEffect } from 'react'

const PLACEHOLDERS = new Set([
  '新建对话',
  '新建工作台会话',
  '新建文件系统会话',
  'New conversation',
  'New session',
])

export function useDefaultConversationTitles(scope: string, ids: string[]) {
  const { getTitle, setTitle } = useSessionTitles(scope)
  const missing = ids.filter(
    (id) => getTitle(id) === id || PLACEHOLDERS.has(getTitle(id)),
  )
  const queries = useQueries({
    queries: missing.map((id) => ({
      queryKey: ['conversation-first-message', scope, id],
      queryFn: () => fetchSessionFirstTitle(id),
      staleTime: 60_000,
      retry: false,
    })),
  })
  useEffect(() => {
    queries.forEach((query, index) => {
      if (query.data) setTitle(missing[index], query.data)
    })
  }, [queries, missing, setTitle])
}
