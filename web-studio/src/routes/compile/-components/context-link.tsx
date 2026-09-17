import type { ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useAppConnection } from '#/hooks/use-app-connection'
import { fetchFsStat } from '#/routes/resources/-lib/api'
import { parentUri } from '#/lib/viking-uri'
import { cn } from '#/lib/utils'
import { fetchCompileSkills } from '../-lib/api'

export const contextName = (uri: string) =>
  uri.split('/').filter(Boolean).pop() || uri

export function useCompileSkillName() {
  const { identityScopeKey } = useAppConnection()
  const { data } = useQuery({
    queryKey: ['compile-skills', identityScopeKey],
    queryFn: ({ signal }) => fetchCompileSkills(signal),
    staleTime: 30_000,
  })
  return (uri: string) =>
    data?.find(
      (skill) => skill.uri.replace(/\/$/, '') === uri.replace(/\/$/, ''),
    )?.name || contextName(uri)
}

export function ContextLink({
  uri,
  children,
  className,
  resolveFile = false,
}: {
  uri: string
  children?: ReactNode
  className?: string
  resolveFile?: boolean
}) {
  const { identityScopeKey } = useAppConnection()
  const { t } = useTranslation('compile')
  const {
    data: entry,
    isFetching,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['compile-context-stat', identityScopeKey, uri],
    queryFn: () => fetchFsStat(uri, { throwOnError: true }),
    enabled: resolveFile,
    staleTime: 30_000,
    retry: false,
  })
  const linkClassName = cn(
    'rounded-sm text-foreground/80 transition-colors underline-offset-4 hover:text-primary hover:underline focus-visible:outline-2 focus-visible:outline-ring [overflow-wrap:anywhere]',
    className,
  )
  if (resolveFile && !entry) {
    const canRetry = isError && !isFetching
    return (
      <button
        type="button"
        disabled={!canRetry}
        aria-busy={isFetching}
        aria-label={canRetry ? `${t('retry')}: ${uri}` : undefined}
        title={`${uri}: ${t(canRetry ? 'failedLoad' : 'loading')}`}
        className={cn(linkClassName, 'text-left disabled:opacity-60')}
        onClick={() => void refetch()}
      >
        {children ?? contextName(uri)}
        {canRetry && <span className="text-xs"> {t('retry')}</span>}
      </button>
    )
  }
  return (
    <Link
      to="/playground"
      search={
        entry && !entry.isDir ? { uri: parentUri(uri), file: uri } : { uri }
      }
      title={uri}
      className={linkClassName}
    >
      {children ?? contextName(uri)}
    </Link>
  )
}
