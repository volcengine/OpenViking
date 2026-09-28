import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useRouter } from '@tanstack/react-router'
import { AlertCircleIcon, ArrowLeftIcon, LoaderCircleIcon } from 'lucide-react'
import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { useAppConnection } from '#/hooks/use-app-connection'
import { compileListReturn } from '../-lib/navigation'
import { compileErrorKey } from '../-lib/errors'
import { errorText } from '../-lib/api'

export function CompileShell({
  children,
  title,
  actions,
  back = true,
}: {
  children: ReactNode
  title: string
  actions?: ReactNode
  back?: boolean
}) {
  const { t } = useTranslation('compile')
  const location = useLocation()
  const router = useRouter()
  const { identityScopeKey } = useAppConnection()
  const returnTo = compileListReturn(
    location.state.compileListOrigin,
    identityScopeKey,
    location.state.__TSR_index,
  )
  return (
    <main className="flex w-full min-w-0 flex-col gap-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-2">
          {back && (
            <Link
              to="/compile"
              search={returnTo.search}
              onClick={(event) => {
                if (
                  returnTo.restoreHistory &&
                  event.button === 0 &&
                  !event.metaKey &&
                  !event.ctrlKey &&
                  !event.shiftKey &&
                  !event.altKey
                ) {
                  event.preventDefault()
                  router.history.back()
                }
              }}
              className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
            >
              <ArrowLeftIcon className="size-4" />
              {t('back')}
            </Link>
          )}
          <h1 className="text-2xl font-semibold tracking-tight [overflow-wrap:anywhere]">
            {title}
          </h1>
          {!back && (
            <p className="text-sm text-muted-foreground">{t('description')}</p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">{actions}</div>
      </header>
      {children}
    </main>
  )
}
export function CompileError({
  error,
  retry,
}: {
  error: unknown
  retry?: () => void
}) {
  const { t } = useTranslation('compile')
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm"
    >
      <AlertCircleIcon className="size-4 shrink-0" />
      <div className="min-w-0 flex-1 break-words">
        <p>{t(compileErrorKey(error))}</p>
        <details className="mt-2">
          <summary className="cursor-pointer">{t('errors.details')}</summary>
          <p className="mt-1 whitespace-pre-wrap">{errorText(error)}</p>
        </details>
      </div>
      {retry && (
        <Button variant="outline" size="sm" onClick={retry}>
          {t('retry')}
        </Button>
      )}
    </div>
  )
}
export function CompileLoading() {
  const { t } = useTranslation('compile')
  return (
    <div
      role="status"
      className="flex items-center gap-2 py-8 text-muted-foreground"
    >
      <LoaderCircleIcon className="size-4 animate-spin" />
      {t('loading')}
    </div>
  )
}
export function CompileStatus({ status }: { status?: string }) {
  const { t } = useTranslation('compile')
  const known = [
    'pending',
    'running',
    'cancelling',
    'completed',
    'failed',
    'cancelled',
  ].includes(status || '')
    ? status
    : 'unknown'
  return (
    <Badge variant={known === 'failed' ? 'destructive' : 'secondary'}>
      {t(`statuses.${known}`)}
    </Badge>
  )
}
