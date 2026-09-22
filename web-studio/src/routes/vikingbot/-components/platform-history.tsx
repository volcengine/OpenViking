import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { UsersIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { getConversations, getMessages } from '../-api'

export function PlatformHistory({
  connection,
  conversation,
  scope,
}: {
  connection: string
  conversation: string
  scope: string
}) {
  const { t } = useTranslation('vikingbot')
  const conversations = useQuery({
    queryKey: ['vikingbot', scope, connection, 'conversations'],
    queryFn: () => getConversations(connection),
    refetchInterval: 5000,
  })
  const title = conversations.data?.find(
    (item) => item.conversation === conversation,
  )?.title
  const query = useInfiniteQuery({
    queryKey: ['vikingbot', scope, connection, conversation, 'messages'],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      getMessages(connection, conversation, pageParam),
    getNextPageParam: (page) => (page.length > 100 ? page[99].id : undefined),
    refetchInterval: 5000,
  })
  const messages = [
    ...(query.data?.pages.flatMap((page) => page.slice(0, 100)) ?? []),
  ].reverse()
  return (
    <section className="flex h-full flex-col">
      <div className="border-b p-4">
        <h2 className="flex items-center gap-2 font-medium">
          <UsersIcon className="size-4 shrink-0 text-muted-foreground" />
          <span className="truncate">{title || t('newChat')}</span>
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">{t('historyHint')}</p>
      </div>
      <div className="flex-1 space-y-5 overflow-auto p-4 md:p-8">
        {query.hasNextPage && (
          <Button
            variant="outline"
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {t('earlier')}
          </Button>
        )}
        {query.isPending && <p role="status">{t('loading')}</p>}
        {query.error && (
          <p role="alert" className="text-destructive">
            {t('operationFailed')} {query.error.message}
          </p>
        )}
        {!query.isPending && !messages.length && (
          <p className="text-sm text-muted-foreground">{t('noHistory')}</p>
        )}
        {messages.map((message) => (
          <article
            key={message.id}
            className="mx-auto max-w-3xl rounded-xl border p-4"
          >
            <div className="mb-2 flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
              <span>{message.sender || t('unknownSender')}</span>
              <time>{new Date(message.time).toLocaleString()}</time>
            </div>
            <p className="whitespace-pre-wrap break-words text-sm leading-7">
              {message.content}
            </p>
            <p
              className={`mt-2 text-xs ${message.status === 'send_failed' ? 'text-destructive' : 'text-muted-foreground'}`}
            >
              {t(
                `delivery.${message.status as 'received' | 'sent' | 'send_failed'}`,
              )}
            </p>
          </article>
        ))}
      </div>
      <p className="border-t p-4 text-center text-sm text-muted-foreground">
        {t('readonly')}
      </p>
    </section>
  )
}
