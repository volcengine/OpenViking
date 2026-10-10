import type { TFunction } from 'i18next'

import { toGatewayError } from './api'

/** `t` from `useTranslation('gateway')`. */
export type Translate = TFunction<'gateway'>

/** Degradation reasons with a label, explanation and suggested action. */
export const DEGRADATIONS = [
  'memory_store_failure',
  'unsafe_json',
  'upstream_changed',
  'missing_injection_record',
  'plugin_present',
  'ark_cache_parameters_changed',
  'hidden_tool_loop_failed',
  'hidden_tool_history_unavailable',
  'hidden_reply_without_anchor',
  'capture_parse_failure',
] as const

function label(t: Translate, group: string, value: string): string {
  return t(`enums.${group}.${value}`, { defaultValue: value })
}

export const protocolLabel = (t: Translate, protocol: string) =>
  label(t, 'protocol', protocol)

export const vendorLabel = (t: Translate, vendor: string) =>
  label(t, 'vendor', vendor)

export const authModeLabel = (t: Translate, mode: string) =>
  label(t, 'authMode', mode)

/** Request type: new message, tool step, housekeeping, memory sync… */
export const kindLabel = (t: Translate, kind: string) => label(t, 'kind', kind)

export const captureStatusLabel = (t: Translate, status: string) =>
  label(t, 'captureStatus', status)

/** OpenViking connection state: connected, degraded, starting. */
export const healthLabel = (t: Translate, status: string) =>
  label(t, 'health', status)

export const toolSkipReasonLabel = (t: Translate, reason: string) =>
  label(t, 'toolSkipReason', reason)

export const toolStopReasonLabel = (t: Translate, reason: string) =>
  label(t, 'toolStopReason', reason)

/** Reminder the model got to start a new context window: soft or hard. */
export const windowReminderLabel = (t: Translate, reminder: string) =>
  label(t, 'windowReminder', reminder)

const OPENVIKING_REASONS: Record<string, string> = {
  openviking_unavailable: 'unavailable',
  openviking_http_401: 'unauthorized',
  openviking_version_mismatch: 'versionMismatch',
  openviking_identity_missing: 'identityMissing',
  root_key_not_allowed: 'rootKey',
}

/**
 * Explains a reason reported for an OpenViking call (`openviking_unavailable`,
 * `openviking_http_<status>`, `openviking_<CODE>`, …); undefined otherwise.
 */
export function openVikingReason(
  t: Translate,
  reason: string,
): string | undefined {
  if (reason in OPENVIKING_REASONS) {
    return t(`enums.openviking.${OPENVIKING_REASONS[reason]}`)
  }
  const http = /^openviking_http_(\d{3})$/.exec(reason)
  if (http) return t('enums.openviking.httpStatus', { status: http[1] })
  const code = /^openviking_(.+)$/.exec(reason)
  if (code) return t('enums.openviking.errorCode', { code: code[1] })
  return undefined
}

/** Why saving a conversation is in its current state; '' for the normal case. */
export function captureReasonLabel(t: Translate, reason?: string): string {
  if (!reason) return ''
  return openVikingReason(t, reason) ?? label(t, 'captureReason', reason)
}

/** Outcome of recalling memory for a new message. */
export function recallReasonLabel(t: Translate, reason?: string): string {
  if (!reason) return ''
  return openVikingReason(t, reason) ?? label(t, 'recallReason', reason)
}

/** Why the gateway reports OpenViking as degraded. */
export function healthReasonLabel(t: Translate, reason?: string): string {
  if (!reason) return ''
  return openVikingReason(t, reason) ?? reason
}

export type DegradationInfo = {
  label: string
  /** What happened, in one sentence; absent for unknown reasons. */
  explanation?: string
  /** What the admin can do about it; absent for unknown reasons. */
  action?: string
}

/** Label, explanation and suggested action for a degraded request. */
export function degradationInfo(t: Translate, reason: string): DegradationInfo {
  if (!(DEGRADATIONS as readonly string[]).includes(reason)) {
    return { label: reason }
  }
  const key = `enums.degradation.${reason}`
  return {
    label: t(`${key}.label`),
    explanation: t(`${key}.explanation`),
    action: t(`${key}.action`),
  }
}

/** Gateway messages the UI recognizes, mapped to friendlier copy. */
const ERROR_MESSAGES: Array<[RegExp, string]> = [
  [/^Revoke or update dependent keys/i, 'errors.inUse'],
  [/^Invalid gateway configuration/i, 'errors.invalidSettings'],
  [/^Invalid key configuration/i, 'errors.invalidKeyRequest'],
  [/^Unknown context policy/i, 'errors.unknownProfile'],
  [/^Unknown upstream/i, 'errors.unknownUpstream'],
  [/^Gateway key not found/i, 'errors.keyNotFound'],
  [/^Gateway session not found/i, 'errors.sessionNotFound'],
  [/^Upstream API key is missing/i, 'errors.upstreamKeyMissing'],
  [/subscription OAuth credentials/i, 'errors.subscriptionKey'],
  [
    /^OpenViking key belongs to another account/i,
    'enums.openviking.otherAccount',
  ],
]

/**
 * A user-facing message for anything a gateway call threw: known server
 * messages and OpenViking reasons first, then a summary by error category,
 * and the raw message when nothing more specific is known.
 */
export function gatewayErrorMessage(t: Translate, error: unknown): string {
  const { detail, reason } = toGatewayError(error)
  const known = ERROR_MESSAGES.find(([pattern]) => pattern.test(detail))
  if (known) return t(known[1])
  const openViking = openVikingReason(t, detail)
  if (openViking) return openViking
  if (reason === 'other') return detail || t('errors.reasons.other')
  return t(`errors.reasons.${reason}`)
}
