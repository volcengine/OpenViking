import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import { Input } from '#/shared/ui/input'
import { Label } from '#/shared/ui/label'
import { useMutation } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { LoaderCircleIcon, PlugZapIcon, Trash2Icon } from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { cn } from '#/shared/lib/utils'

import { DocsLink } from '#/features/admin/gateway/components/docs-link'
import { ExplainedButton } from '#/features/admin/gateway/components/explained-button'
import { Notice } from '#/features/admin/gateway/components/notice'
import type { Tone } from '#/features/admin/gateway/components/status-badges'
import { ToneBadge } from '#/features/admin/gateway/components/status-badges'
import type {
  Upstream,
  UpstreamTestResult,
} from '#/features/admin/gateway/lib/api'
import {
  deleteUpstream,
  saveUpstream,
  testUpstream,
  toGatewayError,
} from '#/features/admin/gateway/lib/api'
import { toastGatewayError } from '#/features/admin/gateway/lib/error-toast'
import type { Translate } from '#/features/admin/gateway/lib/localize'
import { gatewayErrorMessage } from '#/features/admin/gateway/lib/localize'
import {
  servedModels,
  toUpstreamInput,
} from '#/features/admin/gateway/lib/upstream-schema'
import { useGateway } from '#/features/admin/gateway/lib/use-gateway'

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

function TestExplanation({ text }: { text: string }) {
  const [message, url] = text.split('\n', 2)
  return (
    <div className="grid min-w-0 gap-1 text-muted-foreground">
      <p className="text-sm">{message}</p>
      {url ? (
        <p className="break-all font-mono text-xs leading-5">{url}</p>
      ) : null}
    </div>
  )
}

function readableResponse(
  response: NonNullable<UpstreamTestResult['response']>,
): string {
  let body: unknown
  try {
    body = JSON.parse(response.body)
  } catch {
    body = response.body
  }
  if (
    typeof body === 'string' &&
    /<(?:!doctype|html|head|body|title|h[1-6]|p|pre|li|div|br)\b/i.test(body)
  ) {
    const document = new DOMParser().parseFromString(body, 'text/html')
    document.querySelectorAll('script, style, noscript').forEach((node) => {
      node.remove()
    })
    const sections = Array.from(
      document.body.querySelectorAll('h1, h2, h3, p, li, pre'),
    )
      .map((node) => node.textContent)
      .map((text) => text.replace(/\s+/g, ' ').trim())
      .filter(Boolean)
    body = {
      ...(document.title ? { title: document.title } : {}),
      body: sections.length
        ? sections.join('\n')
        : document.body.textContent.replace(/\s+/g, ' ').trim(),
    }
  }
  return JSON.stringify({ ...response, body }, null, 2)
}

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
  const withUrl = (message: string) =>
    result.url ? `${message}\n${result.url}` : message
  if (result.ok) {
    if (result.kind === 'connection') {
      const unverified =
        result.reason === 'models_unavailable' ||
        result.reason === 'rate_limited'
      return {
        tone: unverified ? 'neutral' : 'success',
        label: t(
          unverified
            ? 'upstreams.test.result.connectionUnverified'
            : 'upstreams.test.result.connected',
          { status: result.status },
        ),
        explanation: withUrl(
          t(
            unverified
              ? 'upstreams.test.explain.connectionUnverified'
              : 'upstreams.test.explain.connected',
          ),
        ),
      }
    }
    return {
      tone: 'success',
      label: t('upstreams.test.result.ok', { status: result.status }),
      explanation: t('upstreams.test.explain.ok'),
    }
  }
  if (result.reason === 'rate_limited' || result.status === 429) {
    return {
      tone: 'neutral',
      label: t('upstreams.test.result.failed', { status: result.status }),
      explanation: withUrl(t('upstreams.test.explain.rateLimited')),
    }
  }
  if (result.reason === 'model_request_rejected') {
    return {
      tone: 'danger',
      label: t('upstreams.test.result.failed', { status: result.status }),
      explanation: withUrl(
        t('upstreams.test.explain.requestRejected', {
          status: result.status,
        }),
      ),
    }
  }
  if (result.reason === 'upstream_key_missing') {
    return {
      tone: 'danger',
      label: t('upstreams.credentials.keyMissing'),
      explanation: t('errors.reasons.upstream_key_missing'),
    }
  }
  if (result.reason === 'coding_plan_blocked') {
    return {
      tone: 'danger',
      label: t('upstreams.credentials.blocked'),
      explanation: t('upstreams.test.codingPlan'),
    }
  }
  if (result.status === undefined) {
    return {
      tone: 'danger',
      label: t('upstreams.test.result.unreachable'),
      explanation: withUrl(t('upstreams.test.explain.unreachable')),
    }
  }
  const { status } = result
  if (result.reason === 'invalid_response') {
    return {
      tone: 'danger',
      label: t('upstreams.test.result.failed', { status }),
      explanation: withUrl(t('upstreams.test.explain.invalidResponse')),
    }
  }
  const explanation =
    result.reason === 'upstream_key_rejected' ||
    status === 401 ||
    status === 403
      ? t('upstreams.test.explain.auth')
      : t('upstreams.test.explain.status', { status })
  return {
    tone: 'danger',
    label: t('upstreams.test.result.failed', { status }),
    explanation: withUrl(explanation),
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
  const { t } = useTranslation('gateway')
  const { connection, readOnly } = useGateway()
  const configuredModels = React.useMemo(
    () => servedModels(upstream),
    [upstream],
  )
  const [open, setOpen] = React.useState(false)
  const [model, setModel] = React.useState(configuredModels[0] ?? '')
  const inputId = React.useId()
  const listId = `${inputId}-models`
  const connectionTest = useMutation<UpstreamTestResult, unknown>({
    mutationFn: () => testUpstream(connection, upstream.id),
  })
  const modelTest = useMutation<UpstreamTestResult, unknown, string>({
    mutationFn: (selectedModel) =>
      testUpstream(connection, upstream.id, selectedModel),
  })
  const blocked = testBlockedReason(upstream)
  // An exact endpoint has no model list; upstreams saved before the setting append the path.
  const exact = upstream.append_endpoint_path === false
  const explanation = blocked
    ? t(`upstreams.test.${blocked}`)
    : [t('upstreams.test.hint'), note].filter(Boolean).join(' ')
  const connectionSummary = connectionTest.isSuccess
    ? describeTest(t, { result: connectionTest.data })
    : connectionTest.isError
      ? describeTest(t, { error: connectionTest.error })
      : undefined
  const modelSummary = modelTest.isSuccess
    ? describeTest(t, { result: modelTest.data })
    : modelTest.isError
      ? describeTest(t, { error: modelTest.error })
      : undefined
  const label = t(
    variant === 'row' ? 'upstreams.test.action' : 'upstreams.test.connection',
  )
  // In the list, below `md`, the button shows only its icon and the result
  // only its dot; the text stays for screen readers.
  const compact = variant === 'row' ? 'max-md:sr-only' : undefined
  if (readOnly) return null
  const start = () => {
    connectionTest.reset()
    modelTest.reset()
    setModel(configuredModels[0] ?? '')
    setOpen(true)
    if (!exact) connectionTest.mutate()
  }
  const submit = (event: React.SubmitEvent<HTMLFormElement>) => {
    event.preventDefault()
    const selectedModel = model.trim()
    if (selectedModel) modelTest.mutate(selectedModel)
  }
  const pending = connectionTest.isPending || modelTest.isPending
  const connected =
    exact || (connectionTest.isSuccess && connectionTest.data.ok)
  const modelBlocked = upstream.coding_plan && !upstream.allow_coding_plan

  return (
    <div className="flex items-center gap-2">
      <span aria-live="polite" className="contents">
        {modelSummary && !pending ? (
          <ToneBadge
            tone={modelSummary.tone}
            dot
            title={modelSummary.explanation}
            className="max-w-48"
          >
            <span className={cn('min-w-0 truncate', compact)}>
              {modelSummary.label}
            </span>
          </ToneBadge>
        ) : null}
      </span>
      <ExplainedButton
        type="button"
        variant={variant === 'row' ? 'ghost' : 'outline'}
        size="sm"
        className={variant === 'row' ? 'h-8 px-2 text-xs' : undefined}
        disabled={Boolean(blocked) || pending}
        explanation={explanation}
        onClick={start}
      >
        <PlugZapIcon />
        <span className={compact}>{label}</span>
      </ExplainedButton>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!pending) setOpen(next)
        }}
      >
        <DialogContent showCloseButton={!pending} className="gap-5 sm:max-w-lg">
          <DialogHeader className="min-w-0 pr-8">
            <DialogTitle>
              {t('upstreams.test.dialog.title', { name: upstream.name })}
            </DialogTitle>
            <DialogDescription>
              {t('upstreams.test.dialog.description')}{' '}
              <DocsLink section="testUpstream" variant="text" />
            </DialogDescription>
          </DialogHeader>
          <form className="grid min-w-0 gap-5" onSubmit={submit}>
            <div
              className="grid min-w-0 gap-2 rounded-md border border-border p-3"
              aria-live="polite"
            >
              <p className="text-xs font-medium">
                {t('upstreams.test.dialog.connection')}
              </p>
              {exact ? (
                <p className="text-sm text-muted-foreground">
                  {t('upstreams.test.dialog.exactEndpoint')}
                </p>
              ) : connectionTest.isPending ? (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <LoaderCircleIcon className="size-4 animate-spin" />
                  {t('upstreams.test.dialog.checkingConnection')}
                </p>
              ) : connectionSummary ? (
                <div className="grid min-w-0 gap-1">
                  <ToneBadge tone={connectionSummary.tone} dot>
                    {connectionSummary.label}
                  </ToneBadge>
                  <TestExplanation text={connectionSummary.explanation} />
                </div>
              ) : null}
            </div>
            <div className="grid min-w-0 gap-2">
              <Label htmlFor={inputId}>
                {t('upstreams.test.dialog.model')}
              </Label>
              <Input
                id={inputId}
                list={configuredModels.length ? listId : undefined}
                value={model}
                required
                autoComplete="off"
                disabled={!connected || modelTest.isPending}
                placeholder={t('upstreams.test.dialog.modelPlaceholder')}
                onChange={(event) => {
                  setModel(event.target.value)
                  modelTest.reset()
                }}
              />
              {configuredModels.length ? (
                <datalist id={listId}>
                  {configuredModels.map((item) => (
                    <option key={item} value={item} />
                  ))}
                </datalist>
              ) : null}
              <p className="text-xs text-muted-foreground">
                {modelBlocked
                  ? t('upstreams.test.codingPlan')
                  : t(
                      configuredModels.length
                        ? 'upstreams.test.dialog.configuredModel'
                        : 'upstreams.test.dialog.unconfiguredModel',
                    )}
              </p>
            </div>
            {modelSummary && !modelTest.isPending ? (
              <div
                className="grid min-w-0 gap-3 rounded-md border border-border p-3"
                aria-live="polite"
              >
                <ToneBadge tone={modelSummary.tone} dot>
                  {modelSummary.label}
                </ToneBadge>
                <TestExplanation text={modelSummary.explanation} />
                {!modelTest.data?.ok && modelTest.data?.response ? (
                  <div className="grid gap-1">
                    <p className="text-xs font-medium">
                      {t('upstreams.test.dialog.response')}
                    </p>
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted px-3 py-2 text-xs">
                      {readableResponse(modelTest.data.response)}
                    </pre>
                  </div>
                ) : null}
              </div>
            ) : null}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={pending}
                onClick={() => setOpen(false)}
              >
                {t('actions.cancel')}
              </Button>
              <Button
                type="submit"
                disabled={
                  !connected ||
                  modelBlocked ||
                  modelTest.isPending ||
                  !model.trim()
                }
              >
                {modelTest.isPending ? (
                  <LoaderCircleIcon className="animate-spin" />
                ) : (
                  <PlugZapIcon />
                )}
                {modelTest.isPending
                  ? t('upstreams.test.running')
                  : t('upstreams.test.dialog.run')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}

type DeleteUpstreamButtonProps = {
  /** Known keys that use the upstream; the server confirms conflicts. */
  usedBy: number | undefined
  onClick: () => void
}

/** Header Delete button; the server checks whether keys still use it. */
export function DeleteUpstreamButton({
  usedBy,
  onClick,
}: DeleteUpstreamButtonProps) {
  const { t } = useTranslation('gateway')
  const blocked = Boolean(usedBy)
  return (
    <ExplainedButton
      type="button"
      variant="destructive"
      size="sm"
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
  const { t } = useTranslation('gateway')
  const { connection, invalidate } = useGateway()
  const [conflict, setConflict] = React.useState<number | null>(null)
  React.useEffect(() => setConflict(null), [upstream?.id])
  const disable = useMutation({
    mutationFn: (target: Upstream) =>
      saveUpstream(connection, target.id, {
        ...toUpstreamInput(target),
        enabled: false,
      }),
    onSuccess: async () => {
      onOpenChange(false)
      await invalidate('upstreams')
    },
    onError: (error) => toastGatewayError(t, error),
  })
  const remove = useMutation({
    mutationFn: (target: Upstream) => deleteUpstream(connection, target.id),
    onSuccess: async (_result, target) => {
      toast.success(t('upstreams.toast.deleted', { name: target.name }))
      onOpenChange(false)
      onDeleted?.()
      await invalidate('upstreams')
    },
    onError: async (error) => {
      const failure = toGatewayError(error)
      if (failure.reason === 'in_use') {
        setConflict(failure.keyCount ?? 0)
        await invalidate('keys')
      } else toastGatewayError(t, error)
    },
  })
  const pending = remove.isPending || disable.isPending
  return (
    <Dialog
      open={upstream !== null}
      onOpenChange={(open) => {
        if (!pending) onOpenChange(open)
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t('upstreams.delete.title', { name: upstream?.name ?? '' })}
          </DialogTitle>
          <DialogDescription>
            {t('upstreams.delete.description')}
          </DialogDescription>
        </DialogHeader>
        {conflict !== null ? (
          <Notice tone="warning">
            <p>{t('upstreams.delete.inUse', { count: conflict })}</p>
            {upstream?.enabled ? (
              <p>{t('upstreams.delete.disableHint')}</p>
            ) : null}
            <Link
              to="/admin/gateway/keys"
              className="justify-self-start font-medium underline underline-offset-4"
            >
              {t('actions.viewKeys')}
            </Link>
          </Notice>
        ) : null}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={pending}
            onClick={() => onOpenChange(false)}
          >
            {t('actions.cancel')}
          </Button>
          {conflict !== null ? (
            upstream?.enabled ? (
              <Button
                type="button"
                disabled={pending}
                onClick={() => disable.mutate(upstream)}
              >
                {t('upstreams.delete.disable')}
              </Button>
            ) : null
          ) : (
            <Button
              type="button"
              variant="destructive"
              disabled={pending}
              onClick={() => {
                if (upstream) remove.mutate(upstream)
              }}
            >
              <Trash2Icon />
              {t('upstreams.delete.confirm')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
