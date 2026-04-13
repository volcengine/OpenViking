import { useMemo, useCallback } from 'react'
import { useExternalStoreRuntime } from '@assistant-ui/react'
import type { AppendMessage, ThreadMessageLike } from '@assistant-ui/react'

import { useChat } from './use-chat'
import { useSessionMessages } from './use-sessions'
import { useThreadListAdapter } from './use-thread-list-adapter'
import { convertMessage, buildStreamingMessage } from '../-lib/convert-message'

const EMPTY_MESSAGES: ThreadMessageLike[] = []

export function useAssistantRuntime(
  activeSessionId: string | null,
  setActiveSessionId: (id: string | null) => void,
  searchQuery?: string,
) {
  const threadListAdapter = useThreadListAdapter(activeSessionId, setActiveSessionId, searchQuery)

  // Only fetch history when a session is selected
  const { data: historyMessages } = useSessionMessages(activeSessionId ?? undefined)

  // useChat always runs (hooks can't be conditional), but is inert when no session
  const chat = useChat({
    sessionId: activeSessionId ?? '',
    initialMessages: activeSessionId ? historyMessages : undefined,
    persistMessages: true,
  })

  const messages: ThreadMessageLike[] = useMemo(() => {
    if (!activeSessionId) return EMPTY_MESSAGES

    const converted = chat.messages.map(convertMessage)

    if (chat.status === 'streaming') {
      converted.push(
        buildStreamingMessage({
          content: chat.streamingContent,
          toolCalls: chat.streamingToolCalls,
          reasoning: chat.streamingReasoning,
          iteration: chat.iteration,
        }),
      )
    }

    return converted
  }, [
    activeSessionId,
    chat.messages,
    chat.status,
    chat.streamingContent,
    chat.streamingToolCalls,
    chat.streamingReasoning,
    chat.iteration,
  ])

  const onNew = useCallback(
    async (message: AppendMessage) => {
      if (!activeSessionId) return
      const textParts = message.content.filter((p) => p.type === 'text')
      const text = textParts.map((p) => p.text).join('\n')
      if (text.trim()) {
        await chat.send(text)
      }
    },
    [activeSessionId, chat.send],
  )

  const onCancel = useCallback(async () => {
    chat.abort()
  }, [chat.abort])

  const runtime = useExternalStoreRuntime({
    isRunning: chat.status === 'streaming',
    messages,
    convertMessage: (msg) => msg,
    onNew,
    onCancel,
    adapters: {
      threadList: threadListAdapter,
    },
  })

  return runtime
}
