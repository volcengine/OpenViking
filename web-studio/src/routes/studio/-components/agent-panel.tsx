import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  BotIcon,
  CheckCircle2Icon,
  CircleAlertIcon,
  HistoryIcon,
  Loader2Icon,
  SparklesIcon,
  SquarePenIcon,
} from 'lucide-react'

import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { cn } from '#/lib/utils'
import { useChat } from '#/lib/sessions/use-chat'
import {
  useBotHealth,
  useCreateSession,
  useSessionList,
  useSessionMessages,
} from '#/lib/sessions/use-sessions'
import {
  setSessionTitle,
  useSessionTitles,
} from '#/lib/sessions/use-session-titles'
import { Composer } from '#/routes/sessions/-components/composer'
import { MessageList } from '#/routes/sessions/-components/message-list'

import type { ResourceOpenHandler } from '../-lib/types'
import {
  getErrorMessage,
  readStudioAgentSessionIds,
  registerStudioAgentSessionId,
  withTimeout,
} from '../-lib/utils'

export function AgentPanel({
  initialSessionId,
  onOpenResource,
  onSessionChange,
}: {
  initialSessionId?: string
  onOpenResource: ResourceOpenHandler
  onSessionChange: (sessionId: string) => void
}) {
  const { t } = useTranslation('studio')
  const [sessionId, setSessionId] = useState(initialSessionId ?? '')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [sessionError, setSessionError] = useState<string | null>(null)
  const [isCreatingSession, setIsCreatingSession] = useState(false)
  const creationStartedRef = useRef(false)
  const botHealth = useBotHealth()
  const createSession = useCreateSession()
  const { data: sessions, isLoading: isLoadingSessions } = useSessionList()
  const { getTitle } = useSessionTitles()
  const [studioSessionIds, setStudioSessionIds] = useState<string[]>(() =>
    readStudioAgentSessionIds(),
  )
  const { data: historyMessages } = useSessionMessages(sessionId || undefined)
  const chat = useChat({
    initialMessages: historyMessages,
    persistMessages: Boolean(sessionId),
    sessionId: sessionId || 'studio-preview',
  })
  const scrollRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const startSession = useCallback(async () => {
    if (sessionId || creationStartedRef.current) return

    creationStartedRef.current = true
    setIsCreatingSession(true)
    setSessionError(null)

    try {
      const result = await withTimeout(
        createSession.mutateAsync(undefined),
        12_000,
        t('agent.createTimeout'),
      )
      setStudioSessionIds(registerStudioAgentSessionId(result.session_id))
      setSessionId(result.session_id)
      onSessionChange(result.session_id)
    } catch (error) {
      creationStartedRef.current = false
      setSessionError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsCreatingSession(false)
    }
  }, [createSession, onSessionChange, sessionId, t])

  const handleNewSession = useCallback(async () => {
    if (isCreatingSession) return

    chat.abort()
    chat.setMessages([])
    creationStartedRef.current = true
    setIsCreatingSession(true)
    setSessionError(null)

    try {
      const result = await withTimeout(
        createSession.mutateAsync(undefined),
        12_000,
        t('agent.createTimeout'),
      )
      setStudioSessionIds(registerStudioAgentSessionId(result.session_id))
      setSessionTitle(result.session_id, t('agent.newSessionTitle'))
      setSessionId(result.session_id)
      onSessionChange(result.session_id)
      setHistoryOpen(false)
    } catch (error) {
      creationStartedRef.current = false
      setSessionError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsCreatingSession(false)
    }
  }, [chat, createSession, isCreatingSession, onSessionChange, t])

  const handleSwitchSession = useCallback(
    (nextSessionId: string) => {
      chat.abort()
      creationStartedRef.current = true
      setSessionError(null)
      setIsCreatingSession(false)
      setStudioSessionIds(registerStudioAgentSessionId(nextSessionId))
      setSessionId(nextSessionId)
      onSessionChange(nextSessionId)
      setHistoryOpen(false)
    },
    [chat, onSessionChange],
  )

  useEffect(() => {
    // Only create a session once bot mode is confirmed available, otherwise we
    // leave orphan sessions behind and flash "creating" before the bot-mode
    // prompt.
    if (!sessionId && !sessionError && botHealth.isSuccess) {
      void startSession()
    }
  }, [botHealth.isSuccess, sessionError, sessionId, startSession])

  useEffect(() => {
    if (sessionId) {
      setStudioSessionIds(registerStudioAgentSessionId(sessionId))
    }
  }, [sessionId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [
    chat.messages.length,
    chat.streamingContent,
    chat.streamingReasoning,
    chat.streamingToolCalls,
  ])

  const send = useCallback(
    (message: string) => {
      void chat.send(message)
    },
    [chat],
  )

  const isStreaming = chat.status === 'streaming'
  const botModeError = botHealth.isError ? getErrorMessage(botHealth.error) : ''
  const isCreating = !sessionId && isCreatingSession
  const reversedSessions = useMemo(() => {
    const sessionById = new Map(
      (sessions ?? []).map((session) => [session.session_id, session]),
    )

    return studioSessionIds
      .map((sessionId) => sessionById.get(sessionId))
      .filter((session): session is NonNullable<typeof session> =>
        Boolean(session),
      )
  }, [sessions, studioSessionIds])

  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="border-b bg-background/70 px-4 py-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <BotIcon className="size-3.5 shrink-0" />
            <span className="min-w-0 flex-1 truncate">
              {t('agent.autoRetrieve')}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="size-7 shrink-0"
              title={t('agent.history')}
              onClick={() => setHistoryOpen(true)}
            >
              <HistoryIcon className="size-3.5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="size-7 shrink-0"
              title={t('agent.newSession')}
              disabled={isCreatingSession}
              onClick={() => void handleNewSession()}
            >
              {isCreatingSession ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : (
                <SquarePenIcon className="size-3.5" />
              )}
            </Button>
          </div>
        </div>

        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
        >
          {isCreating ? (
            <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2Icon className="size-4 animate-spin" />
              {t('agent.creating')}
            </div>
          ) : botHealth.isLoading ? (
            <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2Icon className="size-4 animate-spin" />
              {t('agent.detectingBot')}
            </div>
          ) : botModeError ? (
            <BotModePrompt
              detail={botModeError}
              onRetry={() => void botHealth.refetch()}
            />
          ) : sessionError ? (
            <div className="grid gap-3 rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-sm text-destructive">
              <div>{t('agent.createFailed', { error: sessionError })}</div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="w-fit"
                onClick={() => {
                  setSessionError(null)
                  void startSession()
                }}
              >
                {t('agent.retry')}
              </Button>
            </div>
          ) : chat.messages.length === 0 && !isStreaming ? (
            <AgentEmptyState onSend={send} />
          ) : (
            <MessageList
              messages={chat.messages}
              onResourceClick={onOpenResource}
              streaming={
                isStreaming
                  ? {
                      content: chat.streamingContent,
                      iteration: chat.iteration,
                      reasoning: chat.streamingReasoning,
                      toolCalls: chat.streamingToolCalls,
                    }
                  : undefined
              }
            />
          )}
          <div ref={bottomRef} />
        </div>

        {botModeError ? (
          <div className="border-t bg-background/80 px-4 py-3 text-center text-sm text-muted-foreground">
            {t('agent.botDisabledFooter')}
          </div>
        ) : (
          <div className="border-t bg-background/80">
            <Composer
              variant="compact"
              isStreaming={isStreaming}
              onCancel={chat.abort}
              onSend={send}
            />
          </div>
        )}
      </div>

      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <DialogContent className="gap-4 sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('agent.historyTitle')}</DialogTitle>
            <DialogDescription>
              {t('agent.historyDescription')}
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[420px] overflow-y-auto pr-1">
            {isLoadingSessions ? (
              <div className="flex items-center gap-2 rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">
                <Loader2Icon className="size-4 animate-spin" />
                {t('agent.loadingSessions')}
              </div>
            ) : reversedSessions.length === 0 ? (
              <div className="rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">
                {t('agent.noSessions')}
              </div>
            ) : (
              <div className="grid gap-2">
                {reversedSessions.map((session) => {
                  const active = session.session_id === sessionId
                  const title = getTitle(session.session_id)

                  return (
                    <button
                      key={session.session_id}
                      type="button"
                      className={cn(
                        'flex min-w-0 items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors hover:border-primary/45 hover:bg-muted/45',
                        active
                          ? 'border-primary/60 bg-primary/10'
                          : 'border-border bg-background',
                      )}
                      onClick={() => handleSwitchSession(session.session_id)}
                    >
                      <HistoryIcon className="size-4 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-foreground">
                          {title}
                        </span>
                        <span className="block truncate font-mono text-[11px] text-muted-foreground">
                          {session.session_id}
                        </span>
                      </span>
                      {active ? (
                        <CheckCircle2Icon className="size-4 shrink-0 text-primary" />
                      ) : null}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
          <Button
            type="button"
            className="w-full"
            onClick={() => void handleNewSession()}
            disabled={isCreatingSession}
          >
            {isCreatingSession ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <SquarePenIcon className="size-4" />
            )}
            {t('agent.newSession')}
          </Button>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function BotModePrompt({
  detail,
  onRetry,
}: {
  detail: string
  onRetry: () => void
}) {
  const { t } = useTranslation('studio')
  return (
    <div className="flex h-full items-center justify-center">
      <div className="grid max-w-md gap-4 rounded-xl border border-primary/25 bg-primary/5 p-5 text-sm">
        <div className="flex items-start gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <CircleAlertIcon className="size-4" />
          </div>
          <div className="min-w-0">
            <div className="text-base font-semibold text-foreground">
              {t('agent.botPrompt.title')}
            </div>
            <p className="mt-1 leading-6 text-muted-foreground">
              {t('agent.botPrompt.description')}
            </p>
          </div>
        </div>
        <code className="rounded-lg border bg-background px-3 py-2 font-mono text-xs text-foreground">
          openviking-server --with-bot
        </code>
        {detail ? (
          <div className="rounded-lg bg-muted/50 px-3 py-2 text-xs leading-5 text-muted-foreground">
            {detail}
          </div>
        ) : null}
        <Button
          type="button"
          variant="outline"
          className="w-fit"
          onClick={onRetry}
        >
          {t('agent.botPrompt.retry')}
        </Button>
      </div>
    </div>
  )
}

export function AgentEmptyState({
  onSend,
}: {
  onSend: (message: string) => void
}) {
  const { t } = useTranslation('studio')
  const prompts = t('agent.empty.prompts', {
    returnObjects: true,
  }) as string[]
  return (
    <div className="flex h-full flex-col justify-end gap-4 pb-4">
      <div className="rounded-xl border bg-background p-4">
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
          <SparklesIcon className="size-4 text-primary" />
          {t('agent.empty.heading')}
        </div>
        <p className="text-sm leading-6 text-muted-foreground">
          {t('agent.empty.body')}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {prompts.map((prompt) => (
          <button
            key={prompt}
            type="button"
            className="rounded-lg border bg-background px-3 py-2 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
            onClick={() => onSend(prompt)}
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  )
}
