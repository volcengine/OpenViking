import type * as React from 'react'
import { CircleAlertIcon, LoaderCircleIcon, RefreshCwIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { cn } from '#/lib/utils'

import { gatewayErrorMessage } from '../-lib/localize'

type EmptyStateProps = {
  icon: React.ReactNode
  title: React.ReactNode
  description?: React.ReactNode
  /** Buttons under the text. */
  action?: React.ReactNode
  className?: string
}

/** Centered empty state with an icon tile, for card bodies and pages. */
export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex min-h-64 flex-col items-center justify-center gap-3 px-6 py-10 text-center',
        className,
      )}
    >
      <div className="flex size-11 items-center justify-center rounded-lg border bg-muted/30 text-muted-foreground [&_svg]:size-5">
        {icon}
      </div>
      <div className="grid max-w-md gap-1">
        <p className="font-medium">{title}</p>
        {description ? (
          <p className="text-sm leading-6 text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {action ? (
        <div className="mt-1 flex flex-wrap justify-center gap-2">{action}</div>
      ) : null}
    </div>
  )
}

/** Centered spinner with "Loading…", the same size as the other states. */
export function LoadingState({ className }: { className?: string }) {
  const { t } = useTranslation('gateway')
  return (
    <div
      className={cn(
        'flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground',
        className,
      )}
    >
      <LoaderCircleIcon className="size-4 animate-spin" />
      {t('states.loading')}
    </div>
  )
}

type ErrorStateProps = {
  /** What failed, e.g. "Couldn't load upstreams". */
  title: string
  error: unknown
  /** Shows a Retry button. */
  onRetry?: () => void
  retrying?: boolean
  className?: string
}

/** A failed load: what failed, why, and a Retry button. */
export function ErrorState({
  title,
  error,
  onRetry,
  retrying = false,
  className,
}: ErrorStateProps) {
  const { t } = useTranslation('gateway')
  return (
    <EmptyState
      className={className}
      icon={<CircleAlertIcon />}
      title={title}
      description={gatewayErrorMessage(t, error)}
      action={
        onRetry ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={retrying}
            onClick={onRetry}
          >
            <RefreshCwIcon className={retrying ? 'animate-spin' : undefined} />
            {t('actions.retry')}
          </Button>
        ) : undefined
      }
    />
  )
}
