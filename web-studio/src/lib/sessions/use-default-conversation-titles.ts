import { useEffect } from 'react'
import { useQueries } from '@tanstack/react-query'
import { fetchSessionMessages } from './api'
import { useSessionTitles } from './use-session-titles'

const PLACEHOLDERS = new Set([
  '新建对话',
  '新建工作台会话',
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
      queryFn: async () => {
        const messages = await fetchSessionMessages(id)
        const first = messages.find(
          (message) =>
            message.role === 'user' &&
            message.parts.some(
              (part) => part.type === 'text' && part.text.trim(),
            ),
        )
        return (
          first?.parts
            .flatMap((part) => (part.type === 'text' ? [part.text] : []))
            .join(' ')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, 60) || ''
        )
      },
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
