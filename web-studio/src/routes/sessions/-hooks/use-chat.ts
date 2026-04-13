import { useCallback, useEffect, useRef, useState } from 'react'

import type { ChatStatus, StreamToolCall } from '../-types/chat'
import type { Message, MessagePart, TextPart, ToolPart } from '../-types/message'
import { addMessage, sendChatStream, serializeParts } from '../-lib/api'
import { generateTitle } from '../-lib/generate-title'
import { parseSseStream } from '../-lib/sse'
import { setSessionTitle } from './use-session-titles'

function generateId(): string {
  return `msg_${crypto.randomUUID().replace(/-/g, '')}`
}

function createUserMessage(content: string): Message {
  return {
    id: generateId(),
    role: 'user',
    parts: [{ type: 'text', text: content }],
    created_at: new Date().toISOString(),
  }
}

function buildAssistantMessage(
  content: string,
  toolCalls: StreamToolCall[],
): Message {
  const parts: MessagePart[] = []

  // Tool parts first (matches backend ordering)
  for (const tc of toolCalls) {
    const toolPart: ToolPart = {
      type: 'tool',
      tool_id: '',
      tool_name: tc.name,
      tool_uri: '',
      skill_uri: '',
      tool_status: 'completed',
      tool_output: tc.result,
    }
    try {
      toolPart.tool_input = JSON.parse(tc.arguments)
    } catch {
      toolPart.tool_input = { raw: tc.arguments }
    }
    parts.push(toolPart)
  }

  // Text part
  if (content) {
    parts.push({ type: 'text', text: content } satisfies TextPart)
  }

  return {
    id: generateId(),
    role: 'assistant',
    parts,
    created_at: new Date().toISOString(),
  }
}

export interface UseChatOptions {
  sessionId: string
  /** Initial messages to populate the chat. */
  initialMessages?: Message[]
  /** Whether to persist messages via the sessions API after each exchange. */
  persistMessages?: boolean
}

export interface UseChatReturn {
  messages: Message[]
  status: ChatStatus
  error: string | undefined
  streamingContent: string
  streamingToolCalls: StreamToolCall[]
  streamingReasoning: string
  iteration: number
  send: (message: string) => Promise<void>
  abort: () => void
  reset: () => void
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
}

export function useChat(options: UseChatOptions): UseChatReturn {
  const { sessionId, initialMessages, persistMessages = true } = options

  const [messages, setMessages] = useState<Message[]>(initialMessages ?? [])
  const [status, setStatus] = useState<ChatStatus>('idle')
  const [error, setError] = useState<string>()
  const [streamingContent, setStreamingContent] = useState('')
  const [streamingToolCalls, setStreamingToolCalls] = useState<StreamToolCall[]>([])
  const [streamingReasoning, setStreamingReasoning] = useState('')
  const [iteration, setIteration] = useState(0)

  const abortRef = useRef<AbortController | null>(null)
  const messagesRef = useRef<Message[]>(messages)
  messagesRef.current = messages

  // Reset state when sessionId changes
  useEffect(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setMessages([])
    setStatus('idle')
    setError(undefined)
    setStreamingContent('')
    setStreamingToolCalls([])
    setStreamingReasoning('')
    setIteration(0)
  }, [sessionId])

  // Sync initialMessages into state when they load (e.g. history fetched)
  useEffect(() => {
    if (initialMessages && initialMessages.length > 0 && status !== 'streaming') {
      setMessages(initialMessages)
    }
  }, [initialMessages]) // eslint-disable-line react-hooks/exhaustive-deps

  const abort = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const reset = useCallback(() => {
    abort()
    setMessages(initialMessages ?? [])
    setStatus('idle')
    setError(undefined)
    setStreamingContent('')
    setStreamingToolCalls([])
    setStreamingReasoning('')
    setIteration(0)
  }, [abort, initialMessages])

  const send = useCallback(async (message: string) => {
    if (status === 'streaming') return

    const isFirstExchange = messagesRef.current.length === 0

    const userMsg = createUserMessage(message)
    setMessages((prev) => [...prev, userMsg])
    setStatus('streaming')
    setError(undefined)
    setStreamingContent('')
    setStreamingToolCalls([])
    setStreamingReasoning('')
    setIteration(0)

    const controller = new AbortController()
    abortRef.current = controller

    // Accumulators (mutable for performance during streaming)
    let accContent = ''
    let accReasoning = ''
    const accToolCalls: StreamToolCall[] = []
    let lastToolCall: StreamToolCall | null = null

    try {
      const response = await sendChatStream(
        { message, session_id: sessionId },
        controller.signal,
      )

      for await (const event of parseSseStream(response)) {
        if (controller.signal.aborted) break

        switch (event.event) {
          case 'iteration': {
            const data = String(event.data)
            const match = data.match(/(\d+)/)
            if (match) setIteration(Number(match[1]))
            break
          }

          case 'content_delta': {
            accContent += String(event.data)
            setStreamingContent(accContent)
            break
          }

          case 'reasoning_delta': {
            accReasoning += String(event.data)
            setStreamingReasoning(accReasoning)
            break
          }

          case 'reasoning': {
            // Complete reasoning block (fallback if no deltas were sent)
            if (!accReasoning) {
              accReasoning = String(event.data)
              setStreamingReasoning(accReasoning)
            }
            break
          }

          case 'tool_call': {
            // Format: "tool_name({...args})"
            const raw = String(event.data)
            const parenIdx = raw.indexOf('(')
            const name = parenIdx > 0 ? raw.slice(0, parenIdx) : raw
            const args = parenIdx > 0 ? raw.slice(parenIdx + 1, -1) : ''
            lastToolCall = { name, arguments: args }
            accToolCalls.push(lastToolCall)
            setStreamingToolCalls([...accToolCalls])
            break
          }

          case 'tool_result': {
            if (lastToolCall) {
              lastToolCall.result = String(event.data)
              setStreamingToolCalls([...accToolCalls])
            }
            break
          }

          case 'response': {
            // Final complete response — overrides accumulated deltas
            accContent = String(event.data)
            setStreamingContent(accContent)
            break
          }
        }
      }

      // Build assistant message and finalize
      const assistantMsg = buildAssistantMessage(accContent, accToolCalls)
      setMessages((prev) => [...prev, assistantMsg])
      setStatus('idle')
      setStreamingContent('')
      setStreamingToolCalls([])
      setStreamingReasoning('')

      // Persist to openviking session (bot doesn't do this automatically)
      if (persistMessages) {
        try {
          // Sequential: user message must precede assistant message
          await addMessage(sessionId, 'user', message)
          await addMessage(sessionId, 'assistant', undefined, serializeParts(assistantMsg.parts))
        } catch {
          // Persistence failure is non-blocking
        }
      }

      // Generate session title on first exchange
      if (sessionId && isFirstExchange) {
        // Immediate: use first user message as temp title
        setSessionTitle(sessionId, message.slice(0, 20))
        // Async: ask AI for a better title
        generateTitle(message, accContent).then((title) => {
          if (title) setSessionTitle(sessionId, title)
        }).catch(() => {/* non-blocking */})
      }
    } catch (err) {
      if (controller.signal.aborted) {
        // Aborted intentionally — still finalize any partial content
        if (accContent) {
          const partialMsg = buildAssistantMessage(accContent, accToolCalls)
          setMessages((prev) => [...prev, partialMsg])
        }
        setStatus('idle')
      } else {
        const msg = err instanceof Error ? err.message : String(err)
        setError(msg)
        setStatus('error')
      }
    } finally {
      abortRef.current = null
    }
  }, [status, sessionId, persistMessages])

  return {
    messages,
    status,
    error,
    streamingContent,
    streamingToolCalls,
    streamingReasoning,
    iteration,
    send,
    abort,
    reset,
    setMessages,
  }
}
