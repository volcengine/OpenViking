import { QUOTA_BUCKETS } from './api'
import type { GatewayTool, Profile, ProfileSettings, QuotaBucket } from './api'
import { checkNumber, checkRequired, collect } from './validation'
import type { NumberRule, ValidationErrors } from './validation'

/** OpenViking MCP tools that change data; mirrors `WRITE_TOOLS` in `models.py`. */
export const WRITE_TOOLS = [
  'remember',
  'write',
  'edit',
  'add_resource',
  'add_skill',
  'forget',
  'set_acl',
  'cancel_watch',
]

/** Recommended settings; identical to the gateway's own defaults. */
export const PROFILE_DEFAULTS: ProfileSettings = {
  name: 'Default',
  recall: true,
  profile: true,
  profile_max_tokens: 4000,
  capture: true,
  context_types: ['memory', 'resource', 'skill'],
  quotas: {},
  max_tokens: 1600,
  session_max_tokens: 30000,
  score_threshold: 0.35,
  recall_timeout: 2,
  query_max_chars: 8000,
  show_recall: false,
  commit_tokens: 20000,
  keep_recent_messages: 10,
  idle_seconds: 600,
  compaction: true,
  compaction_threshold: 0.9,
  summary_max_tokens: 8000,
  context_window: null,
  agent_windows: false,
  window_soft_ratio: 0.7,
  window_hard_ratio: 0.85,
  gateway_tools: true,
  show_tool_calls: true,
  disabled_tools: WRITE_TOOLS,
  tool_max_rounds: null,
  tool_timeout_seconds: 30,
  tool_result_bytes: 65536,
  tool_total_seconds: null,
  tool_total_tokens: null,
}

type NumericField = {
  [K in keyof ProfileSettings]: ProfileSettings[K] extends number | null
    ? K
    : never
}[keyof ProfileSettings]

/** Limits and units of every numeric profile field (mirrors `models.py`). */
export const PROFILE_LIMITS: Record<NumericField, NumberRule> = {
  profile_max_tokens: { min: 0, max: 32000, integer: true, unit: 'tokens' },
  max_tokens: { min: 64, max: 32000, integer: true, unit: 'tokens' },
  session_max_tokens: { min: 0, integer: true, unit: 'tokens' },
  score_threshold: { min: 0, max: 1, step: 0.05 },
  recall_timeout: { min: 0, max: 30, exclusiveMin: true, unit: 'seconds' },
  query_max_chars: { min: 3, max: 32000, integer: true, unit: 'characters' },
  commit_tokens: { min: 1, integer: true, unit: 'tokens' },
  keep_recent_messages: { min: 0, max: 1000, integer: true, unit: 'messages' },
  idle_seconds: { min: 1, unit: 'seconds' },
  compaction_threshold: { min: 0.5, max: 0.98, step: 0.01 },
  summary_max_tokens: { min: 1000, max: 32000, integer: true, unit: 'tokens' },
  context_window: { min: 1024, integer: true, optional: true, unit: 'tokens' },
  window_soft_ratio: { min: 0.3, max: 0.95, step: 0.01 },
  window_hard_ratio: { min: 0.4, max: 0.97, step: 0.01 },
  tool_max_rounds: { min: 1, integer: true, unlimited: true, unit: 'rounds' },
  tool_timeout_seconds: {
    min: 0,
    max: 120,
    exclusiveMin: true,
    unit: 'seconds',
  },
  tool_result_bytes: { min: 1024, max: 1048576, integer: true, unit: 'bytes' },
  tool_total_seconds: {
    min: 0,
    exclusiveMin: true,
    unlimited: true,
    unit: 'seconds',
  },
  tool_total_tokens: { min: 1024, integer: true, unlimited: true, unit: 'tokens' },
}

/** Per-category entry limit used by "Limit by category". */
export const QUOTA_LIMIT: NumberRule = {
  min: 0,
  integer: true,
  unit: 'entries',
}

/** Save body for `PUT policies/{id}`: known profile fields only. */
export function toProfileSettings(
  profile: Profile | ProfileSettings,
): ProfileSettings {
  return Object.fromEntries(
    Object.keys(PROFILE_DEFAULTS).map((field) => [
      field,
      profile[field as keyof ProfileSettings],
    ]),
  ) as ProfileSettings
}

/** A copy of `profile` for "Duplicate", named by `name`. */
export function duplicateProfile(
  profile: Profile | ProfileSettings,
  name: string,
): ProfileSettings {
  return { ...toProfileSettings(profile), name }
}

/**
 * Checks every field against the gateway's limits. Keys are field names, plus
 * `quotas.<category>` for category limits. Hidden sections are checked too,
 * because the server validates the whole profile.
 */
export function validateProfile(settings: ProfileSettings): ValidationErrors {
  const errors: ValidationErrors = {}
  collect(errors, 'name', checkRequired(settings.name))
  for (const [field, rule] of Object.entries(PROFILE_LIMITS)) {
    collect(errors, field, checkNumber(settings[field as NumericField], rule))
  }
  if (
    !errors.window_hard_ratio &&
    settings.window_soft_ratio >= settings.window_hard_ratio
  ) {
    collect(errors, 'window_soft_ratio', { key: 'validation.softBelowHard' })
  }
  if (settings.recall && settings.context_types.length === 0) {
    collect(errors, 'context_types', { key: 'validation.selectOneSource' })
  }
  const quotas = Object.entries(settings.quotas)
  for (const [bucket, value] of quotas) {
    collect(
      errors,
      `quotas.${bucket}`,
      QUOTA_BUCKETS.includes(bucket as QuotaBucket)
        ? checkNumber(value, QUOTA_LIMIT)
        : { key: 'validation.unknownCategory', values: { name: bucket } },
    )
  }
  if (quotas.length > 0 && quotas.every(([, value]) => value === 0)) {
    collect(errors, 'quotas', { key: 'validation.quotasAllZero' })
  }
  return errors
}

/** OpenViking tools a new conversation can be offered under these settings. */
export function offeredTools(
  settings: ProfileSettings,
  tools: GatewayTool[],
): GatewayTool[] {
  if (!settings.gateway_tools) return []
  return tools.filter((tool) => !settings.disabled_tools.includes(tool.name))
}
