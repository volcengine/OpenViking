import type * as React from 'react'
import { TriangleAlertIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { cn } from '#/lib/utils'

import { EMPTY_VALUE } from '../-lib/format'
import {
  captureReasonLabel,
  captureStatusLabel,
  degradationInfo,
  healthLabel,
  kindLabel,
  protocolLabel,
} from '../-lib/localize'

/**
 * Muted dash for a missing value. It always uses the body font and size, so it
 * matches across columns, including monospace ones such as Model.
 */
export function EmptyValue() {
  return (
    <span className="font-sans text-sm text-muted-foreground">
      {EMPTY_VALUE}
    </span>
  )
}

export type Tone = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

const TONES: Record<Tone, string> = {
  success:
    'border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  warning:
    'border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-300',
  danger: 'border-destructive/25 bg-destructive/10 text-destructive',
  info: 'border-sky-500/25 bg-sky-500/10 text-sky-700 dark:text-sky-300',
  neutral: 'border-border bg-muted/40 text-muted-foreground',
}

const DOTS: Record<Tone, string> = {
  success: 'bg-emerald-500',
  warning: 'bg-amber-500',
  danger: 'bg-destructive',
  info: 'bg-sky-500',
  neutral: 'bg-muted-foreground/60',
}

type ToneBadgeProps = {
  tone: Tone
  /** Leading status dot. */
  dot?: boolean
  /** Shown as a native tooltip. */
  title?: string
  className?: string
  children: React.ReactNode
}

/** Outline pill in one of the Studio status tones; the base of the badges below. */
export function ToneBadge({
  tone,
  dot,
  title,
  className,
  children,
}: ToneBadgeProps) {
  return (
    <Badge
      variant="outline"
      title={title}
      className={cn('gap-1.5 font-normal', TONES[tone], className)}
    >
      {dot ? (
        <span className={cn('size-1.5 shrink-0 rounded-full', DOTS[tone])} />
      ) : null}
      {children}
    </Badge>
  )
}

const HEALTH_TONES: Record<string, Tone> = {
  ok: 'success',
  degraded: 'danger',
  starting: 'info',
}

/** OpenViking connection state from the overview: Connected / Degraded / Starting. */
export function HealthBadge({ status }: { status: string }) {
  const { t } = useTranslation('contextGateway')
  return (
    <ToneBadge tone={HEALTH_TONES[status] ?? 'neutral'} dot>
      {healthLabel(t, status)}
    </ToneBadge>
  )
}

/** Upstream HTTP status; green below 400, red otherwise, a dash when missing. */
export function HttpStatusBadge({ status }: { status?: number }) {
  if (status === undefined) return <EmptyValue />
  return (
    <ToneBadge tone={status < 400 ? 'success' : 'danger'} className="font-mono">
      {status}
    </ToneBadge>
  )
}

const CAPTURE_TONES: Record<string, Tone> = {
  active: 'success',
  disabled: 'neutral',
  retrying: 'warning',
  paused: 'danger',
}

/** Whether the conversation is being saved to OpenViking; the reason shows on hover. */
export function CaptureStatusBadge({
  status,
  reason,
}: {
  status?: string
  reason?: string
}) {
  const { t } = useTranslation('contextGateway')
  if (!status) return <EmptyValue />
  return (
    <ToneBadge
      tone={CAPTURE_TONES[status] ?? 'neutral'}
      dot
      title={captureReasonLabel(t, reason) || undefined}
    >
      {captureStatusLabel(t, status)}
    </ToneBadge>
  )
}

const KIND_TONES: Record<string, Tone> = {
  user: 'info',
  capture: 'success',
}

/** Request type: new message, tool step, housekeeping, memory sync… */
export function KindBadge({ kind }: { kind?: string }) {
  const { t } = useTranslation('contextGateway')
  if (!kind) return <EmptyValue />
  return (
    <ToneBadge tone={KIND_TONES[kind] ?? 'neutral'}>
      {kindLabel(t, kind)}
    </ToneBadge>
  )
}

const PROTOCOL_TINTS: Record<string, string> = {
  anthropic:
    'border-orange-500/25 bg-orange-500/10 text-orange-700 dark:text-orange-300',
  chat: 'border-sky-500/25 bg-sky-500/10 text-sky-700 dark:text-sky-300',
  responses:
    'border-violet-500/25 bg-violet-500/10 text-violet-700 dark:text-violet-300',
}

/** Tint classes per protocol, for chips that should match `ProtocolBadge`. */
export function protocolTint(protocol: string): string {
  return PROTOCOL_TINTS[protocol] ?? TONES.neutral
}

/** Protocol name in its tint: Anthropic Messages, Chat Completions, Responses. */
export function ProtocolBadge({
  protocol,
  className,
}: {
  protocol: string
  className?: string
}) {
  const { t } = useTranslation('contextGateway')
  return (
    <Badge
      variant="outline"
      className={cn('font-normal', protocolTint(protocol), className)}
    >
      {protocolLabel(t, protocol)}
    </Badge>
  )
}

/**
 * Degradation of a request, in amber; the explanation shows on hover.
 * `iconOnly` keeps the label for screen readers only.
 */
export function IssueBadge({
  degradation,
  iconOnly = false,
}: {
  degradation: string
  iconOnly?: boolean
}) {
  const { t } = useTranslation('contextGateway')
  const info = degradationInfo(t, degradation)
  return (
    <ToneBadge tone="warning" title={info.explanation}>
      <TriangleAlertIcon />
      <span className={iconOnly ? 'sr-only' : undefined}>{info.label}</span>
    </ToneBadge>
  )
}

/**
 * HTTP status of a request. Below `md`, where request tables hide the column
 * with the full issue badge, an issue icon also shows under the status.
 */
export function RequestStatus({
  status,
  degradation,
}: {
  status?: number
  degradation?: string
}) {
  return (
    <div className="flex flex-col items-start gap-1">
      <HttpStatusBadge status={status} />
      {degradation ? (
        <span className="md:hidden">
          <IssueBadge degradation={degradation} iconOnly />
        </span>
      ) : null}
    </div>
  )
}
