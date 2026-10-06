import { useMutation } from '@tanstack/react-query'
import { LoaderCircleIcon, PlugZapIcon, Trash2Icon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { cn } from '#/lib/utils'

import { deleteUpstream, testUpstream, toGatewayError } from '../-lib/api'
import type { Upstream, UpstreamTestResult } from '../-lib/api'
import { gatewayErrorMessage } from '../-lib/localize'
import type { Translate } from '../-lib/localize'
import { useGateway } from '../-lib/use-gateway'
import { ConfirmDialog } from './confirm-dialog'
import { ExplainedButton } from './explained-button'
import { ToneBadge } from './status-badges'
import type { Tone } from './status-badges'

/** Why the stored upstream cannot be tested, or undefined when it can. */
function testBlockedReason(
  upstream: Pick<Upstream, 'auth_mode' | 'has_api_key'>,
): 'passthrough' | 'noKey' | undefined {
  if (upstream.auth_mode === 'passthrough') return 'passthrough'
  return upstream.has_api_key ? undefined : 'noKey'
}

/** A finished test: the gateway's answer, or the error the call threw. */
type TestOutcome =
  | { result: UpstreamTestResult; error?: undefined }
  | { result?: undefined; error: unknown }

type TestSummary = { tone: Tone; label: string; explanation: string }

/** Badge text and explanation for a finished connection test. */
export function describeTest(t: Translate, outcome: TestOutcome): TestSummary {
  const { result } = outcome
  if (!result) {
    return {
      tone: 'danger',
      label: t('upstreams.test.result.error'),
      explanation: gatewayErrorMessage(t, outcome.error),
    }
  }
  if (result.status === undefined) {
    return {
      tone: 'danger',
      label: t('upstreams.test.result.unreachable'),
      explanation: t('upstreams.test.explain.unreachable'),
    }
  }
  const { status } = result
  if (result.ok) {
    return {
      tone: 'success',
      label: t('upstreams.test.result.ok', { status }),
      explanation: t('upstreams.test.explain.ok'),
    }
  }
  const explanation =
    status === 401 || status === 403
      ? t('upstreams.test.explain.auth')
      : status === 404
        ? t('upstreams.test.explain.notFound')
        : t('upstreams.test.explain.status', { status })
  return {
    tone: 'danger',
    label: t('upstreams.test.result.failed', { status }),
    explanation,
  }
}

type UpstreamTestProps = {
  upstream: Upstream
  /** `row`: compact ghost button for the list; `header`: outline button for the editor. */
  variant?: 'row' | 'header'
  /** Extra line for the tooltip, e.g. that only saved settings are tested. */
  note?: string
}

/**
 * Tests the saved upstream and shows the result next to the button: a
 * badge such as "Reachable · 200" with the explanation on hover.
 */
export function UpstreamTest({
  upstream,
  variant = 'row',
  note,
}: UpstreamTestProps) {
  const { t } = useTranslation('contextGateway')
  const { connection } = useGateway()
  const test = useMutation<UpstreamTestResult, unknown>({
    mutationFn: () => testUpstream(connection, upstream.id),
  })
  const blocked = testBlockedReason(upstream)
  const explanation = blocked
    ? t(`upstreams.test.${blocked}`)
    : [t('upstreams.test.hint'), note].filter(Boolean).join(' ')
  const summary = test.isSuccess
    ? describeTest(t, { result: test.data })
    : test.isError
      ? describeTest(t, { error: test.error })
      : undefined
  const label = t(
    variant === 'row' ? 'upstreams.test.action' : 'upstreams.test.connection',
  )
  // In the list, below `md`, the button shows only its icon and the result
  // only its dot; the text stays for screen readers.
  const compact = variant === 'row' ? 'max-md:sr-only' : undefined

  return (
    <div className="flex items-center gap-2">
      <span aria-live="polite" className="contents">
        {summary && !test.isPending ? (
          <ToneBadge
            tone={summary.tone}
            dot
            title={summary.explanation}
            className="max-w-48"
          >
            <span className={cn('min-w-0 truncate', compact)}>
              {summary.label}
            </span>
          </ToneBadge>
        ) : null}
      </span>
      <ExplainedButton
        type="button"
        variant={variant === 'row' ? 'ghost' : 'outline'}
        size="sm"
        className={variant === 'row' ? 'h-8 px-2 text-xs' : undefined}
        disabled={Boolean(blocked) || test.isPending}
        explanation={explanation}
        onClick={() => test.mutate()}
      >
        {test.isPending ? (
          <LoaderCircleIcon className="animate-spin" />
        ) : (
          <PlugZapIcon />
        )}
        <span className={compact}>
          {test.isPending ? t('upstreams.test.running') : label}
        </span>
      </ExplainedButton>
    </div>
  )
}

type DeleteUpstreamButtonProps = {
  /** Keys that use the upstream; deleting is blocked while there are any. */
  usedBy: number | undefined
  onClick: () => void
}

/** Header Delete button, disabled with the reason while keys use the upstream. */
export function DeleteUpstreamButton({
  usedBy,
  onClick,
}: DeleteUpstreamButtonProps) {
  const { t } = useTranslation('contextGateway')
  const blocked = Boolean(usedBy)
  return (
    <ExplainedButton
      type="button"
      variant="destructive"
      size="sm"
      disabled={blocked}
      explanation={
        blocked ? t('upstreams.delete.blocked', { count: usedBy }) : undefined
      }
      onClick={onClick}
    >
      <Trash2Icon />
      {t('upstreams.delete.action')}
    </ExplainedButton>
  )
}

type DeleteUpstreamDialogProps = {
  /** The upstream to delete; the dialog is open while set. */
  upstream: Upstream | null
  onOpenChange: (open: boolean) => void
  onDeleted?: () => void
}

/** Confirms and deletes an upstream; a conflict refreshes the key list. */
export function DeleteUpstreamDialog({
  upstream,
  onOpenChange,
  onDeleted,
}: DeleteUpstreamDialogProps) {
  const { t } = useTranslation('contextGateway')
  const { connection, invalidate } = useGateway()
  const remove = useMutation({
    mutationFn: (target: Upstream) => deleteUpstream(connection, target.id),
    onSuccess: async (_result, target) => {
      toast.success(t('upstreams.toast.deleted', { name: target.name }))
      onDeleted?.()
      await invalidate('upstreams')
    },
    onError: async (error) => {
      toast.error(gatewayErrorMessage(t, error))
      if (toGatewayError(error).reason === 'conflict') await invalidate('keys')
    },
  })
  return (
    <ConfirmDialog
      open={upstream !== null}
      onOpenChange={onOpenChange}
      title={t('upstreams.delete.title', { name: upstream?.name ?? '' })}
      description={t('upstreams.delete.description')}
      confirmLabel={t('upstreams.delete.confirm')}
      icon={<Trash2Icon />}
      onConfirm={() => (upstream ? remove.mutateAsync(upstream) : undefined)}
    />
  )
}
