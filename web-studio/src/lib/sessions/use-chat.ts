import { useCallback, useEffect, useRef, useState } from 'react'

import type { ChatStatus, StreamToolCall } from './types/chat'
import type {
  Message,
  MessagePart,
  ContextPart,
  ReasoningPart,
  TextPart,
  ToolPart,
} from './types/message'
import { addMessage, sendChatStream, serializeParts } from './api'
import { parseSseStream, streamEventDataToText } from './sse'
import { setSessionTitle } from './use-session-titles'
import { createBrowserId } from '../browser-crypto'

function createUserMessage(content: string): Message {
  return {
    id: createBrowserId('msg'),
    role: 'user',
    parts: [{ type: 'text', text: content }],
    created_at: new Date().toISOString(),
  }
}

function toolCallKey(toolCall: StreamToolCall): string {
  return `${toolCall.iteration ?? 0}\u0000${toolCall.name}\u0000${toolCall.arguments}`
}

function dedupeToolCalls(toolCalls: StreamToolCall[]): StreamToolCall[] {
  const result: StreamToolCall[] = []
  const byKey = new Map<string, StreamToolCall>()

  for (const toolCall of toolCalls) {
    const key = toolCallKey(toolCall)
    const existing = byKey.get(key)
    if (!existing) {
      const next = { ...toolCall }
      byKey.set(key, next)
      result.push(next)
      continue
    }
    if (!existing.result && toolCall.result) {
      existing.result = toolCall.result
    }
  }

  return result
}

function isToolErrorResult(result?: string): boolean {
  return Boolean(result?.trimStart().toLowerCase().startsWith('error'))
}

function clonePart(part: MessagePart): MessagePart {
  switch (part.type) {
    case 'text':
      return { ...part } satisfies TextPart
    case 'reasoning':
      return { ...part } satisfies ReasoningPart
    case 'tool':
      return {
        ...part,
        tool_input: part.tool_input ? { ...part.tool_input } : undefined,
      } satisfies ToolPart
    case 'context':
      return { ...part } satisfies ContextPart
  }
}

function waitForNextFrame(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve()
  return new Promise((resolve) => {
    window.requestAnimationFrame(() => resolve())
  })
}

type SendOptions = {
  displayMessage?: string
}

function buildAssistantMessage(
  content: string,
  toolCalls: StreamToolCall[],
  orderedParts?: MessagePart[],
): Message {
  const parts: MessagePart[] = orderedParts?.length ? [...orderedParts] : []

  if (parts.length > 0) {
    if (content && !parts.some((part) => part.type === 'text')) {
      parts.push({ type: 'text', text: content } satisfies TextPart)
    }
    return {
      id: createBrowserId('msg'),
      role: 'assistant',
      parts,
      created_at: new Date().toISOString(),
    }
  }

  // Tool parts first (matches backend ordering)
  for (const tc of toolCalls) {
    const toolPart: ToolPart = {
      type: 'tool',
      tool_id: '',
      tool_name: tc.name,
      tool_uri: '',
      skill_uri: '',
      tool_status: isToolErrorResult(tc.result) ? 'error' : 'completed',
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
    id: createBrowserId('msg'),
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
  streamingParts: MessagePart[]
  iteration: number
  send: (message: string, options?: SendOptions) => Promise<void>
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
  const [streamingToolCalls, setStreamingToolCalls] = useState<
    StreamToolCall[]
  >([])
  const [streamingReasoning, setStreamingReasoning] = useState('')
  const [streamingParts, setStreamingParts] = useState<MessagePart[]>([])
  const [iteration, setIteration] = useState(0)

  const abortRef = useRef<AbortController | null>(null)
  const messagesRef = useRef<Message[]>(messages)
  const lastSyncedInitialMessagesRef = useRef<Message[] | undefined>(undefined)
  const pendingInitialMessagesRef = useRef<Message[] | undefined>(undefined)
  messagesRef.current = messages

  // Reset state when sessionId changes
  useEffect(() => {
    abortRef.current?.abort()
    abortRef.current = null
    lastSyncedInitialMessagesRef.current = undefined
    pendingInitialMessagesRef.current = undefined
    setMessages([])
    setStatus('idle')
    setError(undefined)
    setStreamingContent('')
    setStreamingToolCalls([])
    setStreamingReasoning('')
    setStreamingParts([])
    setIteration(0)
  }, [sessionId])

  // Sync initialMessages into state when they load or when switching sessions.
  useEffect(() => {
    if (!initialMessages) return

    if (status === 'streaming') {
      pendingInitialMessagesRef.current = initialMessages
      return
    }

    const nextInitialMessages =
      pendingInitialMessagesRef.current ?? initialMessages
    // Consume the deferred snapshot on every non-streaming run so it can never
    // permanently shadow later initialMessages updates.
    pendingInitialMessagesRef.current = undefined
    if (lastSyncedInitialMessagesRef.current === nextInitialMessages) return

    lastSyncedInitialMessagesRef.current = nextInitialMessages
    setMessages(nextInitialMessages)
  }, [initialMessages, sessionId, status])

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
    setStreamingParts([])
    setIteration(0)
  }, [abort, initialMessages])

  const send = useCallback(
    async (message: string, sendOptions?: SendOptions) => {
      if (status === 'streaming') return

      const isFirstExchange = messagesRef.current.length === 0
      const displayMessage = sendOptions?.displayMessage ?? message

      const userMsg = createUserMessage(displayMessage)
      setMessages((prev) => [...prev, userMsg])
      setStatus('streaming')
      setError(undefined)
      setStreamingContent('')
      setStreamingToolCalls([])
      setStreamingReasoning('')
      setStreamingParts([])
      setIteration(0)

      const controller = new AbortController()
      abortRef.current = controller

      // Accumulators (mutable for performance during streaming)
      let accContent = ''
      let accReasoning = ''
      const accToolCalls: StreamToolCall[] = []
      const accParts: MessagePart[] = []
      let lastToolCall: StreamToolCall | null = null
      let currentReasoningPart: ReasoningPart | null = null
      let currentTextPart: TextPart | null = null
      let currentIteration = 0
      let lastPaintAt = 0
      let publishScheduled = false
      let publishFrameId: number | null = null

      const publishStreamingPartsNow = () => {
        if (publishFrameId !== null && typeof window !== 'undefined') {
          window.cancelAnimationFrame(publishFrameId)
        }
        publishFrameId = null
        publishScheduled = false
        setStreamingParts(accParts.map(clonePart))
      }

      const publishStreamingParts = () => {
        if (publishScheduled) return
        publishScheduled = true
        if (typeof window === 'undefined') {
          queueMicrotask(publishStreamingPartsNow)
          return
        }
        publishFrameId = window.requestAnimationFrame(publishStreamingPartsNow)
      }

      const yieldToRenderer = async () => {
        const now =
          typeof performance !== 'undefined' ? performance.now() : Date.now()
        if (now - lastPaintAt < 16) return
        lastPaintAt = now
        await waitForNextFrame()
      }

      const appendReasoning = (text: string, replaceIfEmpty: boolean) => {
        if (!text) return
        if (!currentReasoningPart || accParts.at(-1) !== currentReasoningPart) {
          currentReasoningPart = {
            type: 'reasoning',
            reasoning: '',
            is_running: true,
          }
          accParts.push(currentReasoningPart)
        }
        currentReasoningPart.reasoning =
          replaceIfEmpty && !currentReasoningPart.reasoning
            ? text
            : currentReasoningPart.reasoning + text
        publishStreamingParts()
      }

      const appendText = (text: string) => {
        if (!text) return
        if (!currentTextPart || accParts.at(-1) !== currentTextPart) {
          currentTextPart = { type: 'text', text: '' }
          accParts.push(currentTextPart)
        }
        currentTextPart.text += text
        currentReasoningPart = null
        publishStreamingParts()
      }

      const setFinalText = (text: string) => {
        if (!text) return
        if (currentTextPart) {
          currentTextPart.text = text
        } else {
          currentTextPart = { type: 'text', text }
          accParts.push(currentTextPart)
        }
        currentReasoningPart = null
        publishStreamingPartsNow()
      }

      try {
        const response = await sendChatStream(
          { message, session_id: sessionId },
          controller.signal,
        )

        for await (const event of parseSseStream(response)) {
          if (controller.signal.aborted) break

          switch (event.event) {
            case 'iteration': {
              const data = streamEventDataToText(event.data)
              const match = data.match(/(\d+)/)
              if (match) {
                currentIteration = Number(match[1])
                setIteration(currentIteration)
              }
              break
            }

            case 'content_delta': {
              const delta = streamEventDataToText(event.data)
              accContent += delta
              setStreamingContent(accContent)
              appendText(delta)
              await yieldToRenderer()
              break
            }

            case 'reasoning_delta': {
              const delta = streamEventDataToText(event.data)
              accReasoning += delta
              setStreamingReasoning(accReasoning)
              appendReasoning(delta, false)
              await yieldToRenderer()
              break
            }

            case 'reasoning': {
              // Complete reasoning block (fallback if no deltas were sent)
              if (!accReasoning) {
                accReasoning = streamEventDataToText(event.data)
                setStreamingReasoning(accReasoning)
                appendReasoning(accReasoning, true)
                await yieldToRenderer()
              }
              break
            }

            case 'tool_call': {
              // Format: "tool_name({...args})"
              const raw = streamEventDataToText(event.data)
              const parenIdx = raw.indexOf('(')
              const name = parenIdx > 0 ? raw.slice(0, parenIdx) : raw
              const args = parenIdx > 0 ? raw.slice(parenIdx + 1, -1) : ''
              const duplicate = accToolCalls.find(
                (tc) =>
                  tc.iteration === currentIteration &&
                  tc.name === name &&
                  tc.arguments === args &&
                  !tc.result,
              )
              if (duplicate) {
                lastToolCall = duplicate
                setStreamingToolCalls(dedupeToolCalls(accToolCalls))
                break
              }
              lastToolCall = {
                name,
                arguments: args,
                iteration: currentIteration,
              }
              accToolCalls.push(lastToolCall)
              const toolPart: ToolPart = {
                type: 'tool',
                tool_id: createBrowserId('tool'),
                tool_name: name,
                tool_uri: '',
                skill_uri: '',
                tool_status: 'running',
              }
              try {
                toolPart.tool_input = JSON.parse(args) as Record<string, unknown>
              } catch {
                if (args) toolPart.tool_input = { raw: args }
              }
              accParts.push(toolPart)
              currentReasoningPart = null
              currentTextPart = null
              setStreamingToolCalls(dedupeToolCalls(accToolCalls))
              publishStreamingParts()
              await yieldToRenderer()
              break
            }

            case 'tool_result': {
              const pendingToolCall = accToolCalls.find((tc) => !tc.result)
              const pendingToolPart = accParts.find(
                (part): part is ToolPart =>
                  part.type === 'tool' &&
                  (part.tool_status === 'running' || part.tool_status === 'pending'),
              )
              if (pendingToolCall) {
                const result = streamEventDataToText(event.data)
                pendingToolCall.result = result
                if (pendingToolPart) {
                  pendingToolPart.tool_output = result
                  pendingToolPart.tool_status = isToolErrorResult(result)
                    ? 'error'
                    : 'completed'
                }
                setStreamingToolCalls(dedupeToolCalls(accToolCalls))
                publishStreamingParts()
                await yieldToRenderer()
              }
              break
            }

            case 'response': {
              // Final complete response — overrides accumulated deltas
              accContent = streamEventDataToText(event.data)
              setStreamingContent(accContent)
              setFinalText(accContent)
              break
            }
          }
        }

        // Build assistant message and finalize
        const assistantMsg = buildAssistantMessage(
          accContent,
          dedupeToolCalls(accToolCalls),
          accParts,
        )
        setStreamingContent('')
        setStreamingToolCalls([])
        setStreamingReasoning('')
        setStreamingParts([])
        setStatus('idle')
        setMessages((prev) => [...prev, assistantMsg])

        // Persist to openviking session (bot doesn't do this automatically)
        if (persistMessages) {
          try {
            // Sequential: user message must precede assistant message
            await addMessage(sessionId, 'user', displayMessage)
            await addMessage(
              sessionId,
              'assistant',
              undefined,
              serializeParts(assistantMsg.parts),
            )
          } catch {
            // Persistence failure is non-blocking
          }
        }

        // Generate session title on first exchange
        if (sessionId && isFirstExchange) {
          // Immediate: use first user message as temp title
          setSessionTitle(sessionId, displayMessage.slice(0, 20))
        }
      } catch (err) {
        if (controller.signal.aborted) {
          // Aborted intentionally — still finalize any partial content
          if (accContent) {
            const partialMsg = buildAssistantMessage(
              accContent,
              dedupeToolCalls(accToolCalls),
              accParts,
            )
            setStreamingContent('')
            setStreamingToolCalls([])
            setStreamingReasoning('')
            setStreamingParts([])
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
    },
    [status, sessionId, persistMessages],
  )

  return {
    messages,
    status,
    error,
    streamingContent,
    streamingToolCalls,
    streamingReasoning,
    streamingParts,
    iteration,
    send,
    abort,
    reset,
    setMessages,
  }
}
