import i18n from '#/i18n/core'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { ChatStatus, StreamToolCall } from './types/chat'
import type {
  Message,
  MessagePart,
  ContextPart,
  IterationPart,
  ReasoningPart,
  TextPart,
  ToolPart,
  ToolResultPart,
} from './types/message'
import {
  addMessage,
  interruptChatSession,
  sendChatStream,
  steerChatSession,
  serializeParts,
} from './api'
import { parseSseStream, streamEventDataToText } from './sse'
import { setSessionTitle } from './use-session-titles'
import { createBrowserId } from '../browser-crypto'
import { ovClient } from '#/lib/ov-client'

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
    case 'iteration':
      return { ...part } satisfies IterationPart
    case 'tool':
      return {
        ...part,
        tool_input: part.tool_input ? { ...part.tool_input } : undefined,
      } satisfies ToolPart
    case 'tool_result':
      return { ...part } satisfies ToolResultPart
    case 'image_url':
      return { ...part, image_url: { ...part.image_url } }
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

type AbortOptions = {
  discardPartial?: boolean
  closeLocalStream?: boolean
}

function isSupersededRequest(
  current: AbortController | null,
  expected: AbortController,
) {
  return current !== null && current !== expected
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
  identityScopeKey: string
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
  streamingInsertIndex: number | undefined
  iteration: number
  send: (message: string, options?: SendOptions) => Promise<void>
  abort: (options?: AbortOptions) => void
  reset: () => void
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
}

export function useChat(options: UseChatOptions): UseChatReturn {
  const {
    identityScopeKey,
    sessionId,
    initialMessages,
    persistMessages = true,
  } = options

  const [messages, setMessages] = useState<Message[]>(initialMessages ?? [])
  const [status, setStatus] = useState<ChatStatus>('idle')
  const [error, setError] = useState<string>()
  const [streamingContent, setStreamingContent] = useState('')
  const [streamingToolCalls, setStreamingToolCalls] = useState<
    StreamToolCall[]
  >([])
  const [streamingReasoning, setStreamingReasoning] = useState('')
  const [streamingParts, setStreamingParts] = useState<MessagePart[]>([])
  const [streamingInsertIndex, setStreamingInsertIndex] = useState<
    number | undefined
  >(undefined)
  const [iteration, setIteration] = useState(0)

  const abortRef = useRef<AbortController | null>(null)
  const activeSessionRef = useRef<string | null>(null)
  const activeConnectionRef = useRef<string | null>(null)
  const sessionIdRef = useRef(sessionId)
  const appendInputRef = useRef<
    ((message: Message, accepted: Promise<boolean>) => Promise<boolean>) | null
  >(null)
  const persistenceTailRef = useRef<Promise<void>>(Promise.resolve())
  const discardPartialControllersRef = useRef(new WeakSet<AbortController>())
  const interruptedControllersRef = useRef(new WeakSet<AbortController>())
  const messagesRef = useRef<Message[]>(messages)
  const historyLoadedRef = useRef(Boolean(initialMessages))
  const lastSyncedInitialMessagesRef = useRef<Message[] | undefined>(undefined)
  const pendingInitialMessagesRef = useRef<Message[] | undefined>(undefined)
  messagesRef.current = messages
  sessionIdRef.current = sessionId

  const abort = useCallback((abortOptions?: AbortOptions) => {
    const controller = abortRef.current
    const activeSessionId = activeSessionRef.current
    if (!controller || !activeSessionId) return

    if (abortOptions?.discardPartial) {
      discardPartialControllersRef.current.add(controller)
    }

    const closeLocalStream = () => {
      controller.abort()
      if (abortRef.current === controller) {
        abortRef.current = null
        activeSessionRef.current = null
        activeConnectionRef.current = null
        appendInputRef.current = null
      }
    }

    const currentConnection = JSON.stringify([
      ovClient.getOptions(),
      ovClient.getConnection(),
    ])
    if (activeConnectionRef.current !== currentConnection) {
      closeLocalStream()
      return
    }

    void interruptChatSession(activeSessionId)
      .then((result) => {
        if (result.interrupted && abortRef.current === controller) {
          interruptedControllersRef.current.add(controller)
        }
        if (!result.interrupted && abortRef.current === controller) {
          closeLocalStream()
        }
      })
      .catch(() => {
        if (abortRef.current === controller) {
          closeLocalStream()
        }
      })

    if (abortOptions?.closeLocalStream) {
      closeLocalStream()
    }
  }, [])

  const ownerRef = useRef(0)

  // Cancel work when its session/identity owner changes or unmounts.
  useEffect(() => {
    ownerRef.current += 1
    return () => {
      ownerRef.current += 1
      abortRef.current?.abort()
    }
  }, [identityScopeKey, sessionId])

  // Reset state when sessionId changes
  useEffect(() => {
    abort({ closeLocalStream: true, discardPartial: true })
    historyLoadedRef.current = false
    lastSyncedInitialMessagesRef.current = undefined
    pendingInitialMessagesRef.current = undefined
    setMessages([])
    setStatus('idle')
    setError(undefined)
    setStreamingContent('')
    setStreamingToolCalls([])
    setStreamingReasoning('')
    setStreamingParts([])
    setStreamingInsertIndex(undefined)
    setIteration(0)
  }, [abort, identityScopeKey, sessionId])

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
    if (!historyLoadedRef.current) {
      setMessages((current) => [...nextInitialMessages, ...current])
      historyLoadedRef.current = true
    }
  }, [initialMessages, sessionId, status])

  const reset = useCallback(() => {
    abort({ closeLocalStream: true, discardPartial: true })
    historyLoadedRef.current = Boolean(initialMessages)
    setMessages(initialMessages ?? [])
    setStatus('idle')
    setError(undefined)
    setStreamingContent('')
    setStreamingToolCalls([])
    setStreamingReasoning('')
    setStreamingParts([])
    setStreamingInsertIndex(undefined)
    setIteration(0)
  }, [abort, initialMessages])

  const queuePersistence = useCallback((operation: () => Promise<unknown>) => {
    const queued = persistenceTailRef.current.then(async () => {
      try {
        await operation()
      } catch {
        // Session persistence is best-effort and must not fail the chat stream.
      }
    })
    persistenceTailRef.current = queued
    return queued
  }, [])

  useEffect(
    () => () => {
      abort({ closeLocalStream: true, discardPartial: true })
    },
    [abort],
  )

  const send = useCallback(
    async (message: string, sendOptions?: SendOptions) => {
      const isFirstExchange = messagesRef.current.length === 0
      const displayMessage = sendOptions?.displayMessage ?? message

      const userMsg = createUserMessage(displayMessage)
      const connectionSnapshot = JSON.stringify([
        ovClient.getOptions(),
        ovClient.getConnection(),
      ])

      if (abortRef.current) {
        const activeController = abortRef.current
        if (activeConnectionRef.current !== connectionSnapshot) {
          throw new Error(i18n.t('studio:errors.connectionChanged'))
        }
        setError(undefined)
        const appendInput = appendInputRef.current
        if (!appendInput) {
          throw new Error(i18n.t('studio:errors.noActiveRequest'))
        }
        let resolveAccepted!: (accepted: boolean) => void
        const accepted = new Promise<boolean>((resolve) => {
          resolveAccepted = resolve
        })
        const appended = appendInput(userMsg, accepted)
        try {
          const result = await steerChatSession(sessionId, { message })
          if (!result.accepted) throw new Error(i18n.t('studio:errors.inputRejected'))
          resolveAccepted(true)
          await appended
        } catch (err) {
          resolveAccepted(false)
          if (
            abortRef.current === activeController &&
            sessionIdRef.current === sessionId
          ) {
            setError(err instanceof Error ? err.message : String(err))
          }
          throw err
        }
        return
      }

      setMessages((prev) => [...prev, userMsg])
      setStatus('streaming')
      setError(undefined)
      setStreamingContent('')
      setStreamingToolCalls([])
      setStreamingReasoning('')
      setStreamingParts([])
      setStreamingInsertIndex(messagesRef.current.length + 1)
      setIteration(0)

      const controller = new AbortController()
      const owner = ownerRef.current
      abortRef.current = controller
      activeSessionRef.current = sessionId
      activeConnectionRef.current = connectionSnapshot
      let streamingInsertIndexValue = messagesRef.current.length + 1
      const isCurrent = () =>
        owner === ownerRef.current &&
        connectionSnapshot ===
          JSON.stringify([ovClient.getOptions(), ovClient.getConnection()])
      const isSameConnection = () =>
        connectionSnapshot ===
        JSON.stringify([ovClient.getOptions(), ovClient.getConnection()])

      // Accumulators (mutable for performance during streaming)
      let accContent = ''
      let accReasoning = ''
      const accToolCalls: StreamToolCall[] = []
      const accParts: MessagePart[] = []
      const completedStreamMessages: Message[] = []
      const activeAssistantMessages = new Set<Message>()
      let pendingUserMessages: Message[] = []
      let flushedStreamMessageCount = 0
      let currentReasoningPart: ReasoningPart | null = null
      let currentReasoningHasDelta = false
      let currentTextPart: TextPart | null = null
      let currentIteration = 0
      let lastPaintAt = 0
      let publishScheduled = false
      let publishFrameId: number | null = null
      let interrupted = false
      let timelineTail: Promise<void> = Promise.resolve()
      let streamStarted = false
      let initialUserPersistenceQueued = false
      let persistedInitialUserMessage = false
      const queueInitialUserPersistence = () => {
        if (
          !persistMessages ||
          !streamStarted ||
          initialUserPersistenceQueued ||
          persistedInitialUserMessage
        ) {
          return
        }
        initialUserPersistenceQueued = true
        void queuePersistence(async () => {
          if (!isCurrent() || controller.signal.aborted) return
          await addMessage(sessionId, 'user', displayMessage)
          persistedInitialUserMessage = true
        })
      }

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

      const clearStreamingSegmentView = () => {
        if (publishFrameId !== null && typeof window !== 'undefined') {
          window.cancelAnimationFrame(publishFrameId)
        }
        publishFrameId = null
        publishScheduled = false
        setStreamingContent('')
        setStreamingToolCalls([])
        setStreamingReasoning('')
        setStreamingParts([])
      }

      const clearStreamingState = () => {
        clearStreamingSegmentView()
        setStreamingInsertIndex(undefined)
      }

      const finishActiveRequest = () => {
        appendInputRef.current = null
        if (abortRef.current === controller) {
          abortRef.current = null
          activeSessionRef.current = null
          activeConnectionRef.current = null
        }
      }

      const yieldToRenderer = async () => {
        const now =
          typeof performance !== 'undefined' ? performance.now() : Date.now()
        if (now - lastPaintAt < 16) return
        lastPaintAt = now
        await waitForNextFrame()
      }

      const appendReasoning = (text: string) => {
        if (!text) return
        if (!currentReasoningPart || accParts.at(-1) !== currentReasoningPart) {
          currentReasoningPart = {
            type: 'reasoning',
            reasoning: '',
            is_running: true,
          }
          accParts.push(currentReasoningPart)
        }
        currentReasoningPart.reasoning += text
        publishStreamingParts()
      }

      const finishCurrentReasoning = () => {
        if (!currentReasoningPart) return
        currentReasoningPart.is_running = false
        currentReasoningPart = null
        currentReasoningHasDelta = false
      }

      const appendText = (text: string) => {
        if (!text) return
        finishCurrentReasoning()
        if (!currentTextPart || accParts.at(-1) !== currentTextPart) {
          currentTextPart = { type: 'text', text: '' }
          accParts.push(currentTextPart)
        }
        currentTextPart.text += text
        publishStreamingParts()
      }

      const setFinalText = (text: string) => {
        if (!text) return
        finishCurrentReasoning()
        if (currentTextPart) {
          currentTextPart.text = text
        } else {
          currentTextPart = { type: 'text', text }
          accParts.push(currentTextPart)
        }
        publishStreamingPartsNow()
      }

      const resetAccumulators = () => {
        accContent = ''
        accReasoning = ''
        accToolCalls.length = 0
        accParts.length = 0
        currentReasoningPart = null
        currentReasoningHasDelta = false
        currentTextPart = null
      }

      const hasAssistantSegment = () =>
        accContent.length > 0 || accParts.length > 0 || accToolCalls.length > 0

      const finalizeAssistantSegment = () => {
        finishCurrentReasoning()
        if (!hasAssistantSegment()) return null
        const completedMessage = buildAssistantMessage(
          accContent,
          dedupeToolCalls(accToolCalls),
          accParts,
        )
        completedStreamMessages.push(completedMessage)
        resetAccumulators()
        return completedMessage
      }

      const flushCompletedStreamMessages = () => {
        const pendingMessages = completedStreamMessages.slice(
          flushedStreamMessageCount,
        )
        if (pendingMessages.length === 0) return
        flushedStreamMessageCount = completedStreamMessages.length
        const pendingAssistantMessages = pendingMessages.filter(
          (message) => message.role === 'assistant',
        )
        if (pendingAssistantMessages.length === 0) {
          streamingInsertIndexValue += pendingMessages.length
          setStreamingInsertIndex(streamingInsertIndexValue)
          return
        }
        for (const message of pendingAssistantMessages) {
          activeAssistantMessages.add(message)
        }
        setMessages((prev) => {
          const insertAt = Math.min(streamingInsertIndexValue, prev.length)
          const next = [
            ...prev.slice(0, insertAt),
            ...pendingAssistantMessages,
            ...prev.slice(insertAt),
          ]
          streamingInsertIndexValue = insertAt + pendingMessages.length
          setStreamingInsertIndex(streamingInsertIndexValue)
          return next
        })
      }

      const appendPendingUserMessagesToTimeline = () => {
        if (pendingUserMessages.length === 0) return
        completedStreamMessages.push(...pendingUserMessages)
        pendingUserMessages = []
      }

      const commitAssistantSegment = () => {
        finalizeAssistantSegment()
        appendPendingUserMessagesToTimeline()
        flushCompletedStreamMessages()
        clearStreamingSegmentView()
      }

      const dropStreamingSegment = () => {
        appendPendingUserMessagesToTimeline()
        resetAccumulators()
        clearStreamingState()
        if (activeAssistantMessages.size > 0) {
          setMessages((prev) =>
            prev.filter((message) => !activeAssistantMessages.has(message)),
          )
        }
      }

      const persistTimeline = (userOnly: boolean) => {
        if (!persistMessages) return Promise.resolve()
        const timeline = userOnly
          ? completedStreamMessages.filter((message) => message.role === 'user')
          : [...completedStreamMessages]
        return queuePersistence(async () => {
          if (
            !persistedInitialUserMessage &&
            !initialUserPersistenceQueued &&
            streamStarted
          ) {
            if (!isCurrent() || controller.signal.aborted) return
            await addMessage(sessionId, 'user', displayMessage)
            persistedInitialUserMessage = true
          }
          if (!isSameConnection()) return
          for (const completedMessage of timeline) {
            if (!isSameConnection()) return
            if (completedMessage.role === 'user') {
              await addMessage(
                sessionId,
                'user',
                completedMessage.parts
                  .filter((part): part is TextPart => part.type === 'text')
                  .map((part) => part.text)
                  .join('\n'),
              )
            } else if (!userOnly) {
              if (!isCurrent() || controller.signal.aborted) return
              await addMessage(
                sessionId,
                'assistant',
                undefined,
                serializeParts(completedMessage.parts),
              )
            }
          }
        })
      }

      appendInputRef.current = (inputMessage, accepted) => {
        const decision = timelineTail.then(async () => {
          const wasAccepted = await accepted
          if (!wasAccepted) return false
          pendingUserMessages.push(inputMessage)
          if (abortRef.current === controller) {
            setMessages((prev) => [...prev, inputMessage])
          }
          return true
        })
        timelineTail = decision.then(() => undefined)
        return decision
      }

      try {
        const response = await sendChatStream(
          { message, session_id: sessionId },
          controller.signal,
        )

        stream: for await (const event of parseSseStream(response)) {
          if (controller.signal.aborted) break
          streamStarted = true
          queueInitialUserPersistence()

          switch (event.event) {
            case 'iteration': {
              await timelineTail
              if (pendingUserMessages.length > 0) {
                commitAssistantSegment()
              }
              const data = streamEventDataToText(event.data)
              const match = data.match(/(\d+)/)
              if (match) {
                currentIteration = Number(match[1])
                setIteration(currentIteration)
                finishCurrentReasoning()
                currentTextPart = null
                const previousPart = accParts.at(-1)
                if (
                  previousPart?.type !== 'iteration' ||
                  previousPart.iteration !== currentIteration
                ) {
                  accParts.push({
                    type: 'iteration',
                    iteration: currentIteration,
                  })
                  publishStreamingParts()
                  await yieldToRenderer()
                }
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
              appendReasoning(delta)
              currentReasoningHasDelta = true
              await yieldToRenderer()
              break
            }

            case 'reasoning': {
              // Complete reasoning block (fallback if no deltas were sent)
              if (!currentReasoningHasDelta) {
                const reasoning = streamEventDataToText(event.data)
                accReasoning += reasoning
                setStreamingReasoning(accReasoning)
                appendReasoning(reasoning)
                finishCurrentReasoning()
                publishStreamingPartsNow()
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
                  tc.result === undefined,
              )
              if (duplicate) {
                setStreamingToolCalls(dedupeToolCalls(accToolCalls))
                break
              }
              const toolCall = {
                name,
                arguments: args,
                iteration: currentIteration,
              }
              accToolCalls.push(toolCall)
              const toolPart: ToolPart = {
                type: 'tool',
                tool_id: createBrowserId('tool'),
                tool_name: name,
                tool_uri: '',
                skill_uri: '',
                tool_status: 'running',
              }
              try {
                toolPart.tool_input = JSON.parse(args) as Record<
                  string,
                  unknown
                >
              } catch {
                if (args) toolPart.tool_input = { raw: args }
              }
              finishCurrentReasoning()
              accParts.push(toolPart)
              currentTextPart = null
              setStreamingToolCalls(dedupeToolCalls(accToolCalls))
              publishStreamingParts()
              await yieldToRenderer()
              break
            }

            case 'tool_result': {
              finishCurrentReasoning()
              const pendingToolCall = accToolCalls.find(
                (tc) => tc.result === undefined,
              )
              const pendingToolPart = accParts.find(
                (part): part is ToolPart =>
                  part.type === 'tool' &&
                  (part.tool_status === 'running' ||
                    part.tool_status === 'pending'),
              )
              if (pendingToolCall) {
                const result = streamEventDataToText(event.data)
                const isError = isToolErrorResult(result)
                pendingToolCall.result = result
                if (pendingToolPart) {
                  pendingToolPart.tool_output = result
                  pendingToolPart.tool_status = isError ? 'error' : 'completed'
                  accParts.push({
                    type: 'tool_result',
                    tool_id: pendingToolPart.tool_id,
                    tool_name: pendingToolPart.tool_name,
                    tool_output: result,
                    is_error: isError,
                  })
                }
                currentTextPart = null
                setStreamingToolCalls(dedupeToolCalls(accToolCalls))
                publishStreamingParts()
                await yieldToRenderer()
              }
              break
            }

            case 'interrupt': {
              appendInputRef.current = null
              await timelineTail
              dropStreamingSegment()
              interrupted = true
              break stream
            }

            case 'response': {
              appendInputRef.current = null
              await timelineTail
              // Final complete response — overrides accumulated deltas
              accContent = streamEventDataToText(event.data)
              setStreamingContent(accContent)
              setFinalText(accContent)
              break stream
            }
          }
        }

        // Build assistant message and finalize
        await timelineTail
        if (
          discardPartialControllersRef.current.has(controller) ||
          sessionIdRef.current !== sessionId ||
          isSupersededRequest(abortRef.current, controller)
        ) {
          appendPendingUserMessagesToTimeline()
          await persistTimeline(true)
          return
        }
        if (!isCurrent()) return
        if (controller.signal.aborted)
          throw new DOMException('Aborted', 'AbortError')
        appendInputRef.current = null
        if (interruptedControllersRef.current.has(controller)) {
          dropStreamingSegment()
          interrupted = true
        }
        finishCurrentReasoning()
        if (interrupted) {
          const persistence = persistTimeline(true)
          finishActiveRequest()
          setStatus('idle')
          await persistence
          return
        }
        commitAssistantSegment()
        const persistence = persistTimeline(false)
        finishActiveRequest()
        setStatus('idle')
        clearStreamingState()

        await persistence

        // Generate session title on first exchange
        if (isCurrent() && sessionId && isFirstExchange) {
          // Immediate: use first user message as temp title
          setSessionTitle(
            identityScopeKey,
            sessionId,
            displayMessage.slice(0, 20),
          )
        }
      } catch (err) {
        if (
          discardPartialControllersRef.current.has(controller) ||
          sessionIdRef.current !== sessionId ||
          isSupersededRequest(abortRef.current, controller)
        ) {
          await timelineTail
          appendPendingUserMessagesToTimeline()
          await persistTimeline(true)
          return
        }
        if (!isCurrent()) return
        appendInputRef.current = null
        await timelineTail
        dropStreamingSegment()
        if (controller.signal.aborted) {
          setStatus('idle')
          await persistTimeline(true)
        } else {
          const msg = err instanceof Error ? err.message : String(err)
          setError(msg)
          setStatus('error')
          await persistTimeline(true)
        }
      } finally {
        discardPartialControllersRef.current.delete(controller)
        interruptedControllersRef.current.delete(controller)
        if (abortRef.current === controller) {
          abortRef.current = null
          activeSessionRef.current = null
          activeConnectionRef.current = null
          appendInputRef.current = null
        }
      }
    },
    [identityScopeKey, persistMessages, queuePersistence, sessionId],
  )

  return {
    messages,
    status,
    error,
    streamingContent,
    streamingToolCalls,
    streamingReasoning,
    streamingParts,
    streamingInsertIndex,
    iteration,
    send,
    abort,
    reset,
    setMessages,
  }
}
