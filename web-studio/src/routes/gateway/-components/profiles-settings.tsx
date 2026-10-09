import * as React from 'react'
import {
  BrainIcon,
  SaveIcon,
  ScrollTextIcon,
  WrenchIcon,
  XIcon,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { Badge } from '#/components/ui/badge'
import { Checkbox } from '#/components/ui/checkbox'
import { FieldError } from '#/components/ui/field'
import { Label } from '#/components/ui/label'

import { CONTEXT_TYPES, QUOTA_BUCKETS } from '../-lib/api'
import type {
  ContextType,
  GatewayTool,
  ProfileSettings,
  QuotaBucket,
} from '../-lib/api'
import { formatBytes, formatNumber, humanizeSeconds } from '../-lib/format'
import {
  PROFILE_DEFAULTS,
  PROFILE_LIMITS,
  QUOTA_LIMIT,
} from '../-lib/profile-schema'
import { useTools } from '../-lib/use-gateway'
import type { Unit, ValidationErrors } from '../-lib/validation'
import { ErrorState, LoadingState } from './empty-state'
import { Notice } from './notice'
import { NumberInput } from './number-input'
import { SettingField } from './setting-field'
import { SettingSection } from './setting-section'
import { ToggleRow } from './toggle-row'

/** The four groups of a context profile, in page order. */
export type ProfileSection =
  | 'recall'
  | 'capture'
  | 'longConversations'
  | 'tools'

export const PROFILE_SECTIONS: Array<{ id: ProfileSection; icon: LucideIcon }> =
  [
    { id: 'recall', icon: BrainIcon },
    { id: 'capture', icon: SaveIcon },
    { id: 'longConversations', icon: ScrollTextIcon },
    { id: 'tools', icon: WrenchIcon },
  ]

/** Element id of a section card, for the editor's section navigation. */
export const sectionAnchor = (section: ProfileSection) => `profile-${section}`

/** Whether a section takes effect; long conversations count while either switch is on. */
export function isSectionOn(
  settings: ProfileSettings,
  section: ProfileSection,
): boolean {
  switch (section) {
    case 'recall':
      return settings.recall
    case 'capture':
      return settings.capture
    case 'longConversations':
      return settings.compaction || settings.agent_windows
    case 'tools':
      return settings.gateway_tools
  }
}

type NumericField = keyof typeof PROFILE_LIMITS

/** Copy of each numeric setting under `profiles.*`; the prefix is its section. */
const FIELD_COPY: Record<NumericField, `${ProfileSection}.${string}`> = {
  profile_max_tokens: 'recall.profileMaxTokens',
  max_tokens: 'recall.maxTokens',
  session_max_tokens: 'recall.sessionMaxTokens',
  score_threshold: 'recall.scoreThreshold',
  recall_timeout: 'recall.recallTimeout',
  query_max_chars: 'recall.queryMaxChars',
  idle_seconds: 'capture.idleSeconds',
  commit_tokens: 'capture.commitTokens',
  keep_recent_messages: 'capture.keepRecentMessages',
  compaction_threshold: 'longConversations.threshold',
  summary_max_tokens: 'longConversations.summaryMaxTokens',
  context_window: 'longConversations.contextWindow',
  window_soft_ratio: 'longConversations.softRatio',
  window_hard_ratio: 'longConversations.hardRatio',
  tool_max_rounds: 'tools.maxRounds',
  tool_timeout_seconds: 'tools.timeoutSeconds',
  tool_result_bytes: 'tools.resultBytes',
  tool_total_seconds: 'tools.totalSeconds',
  tool_total_tokens: 'tools.totalTokens',
}

const LIST_FIELD_SECTIONS: Partial<Record<string, ProfileSection>> = {
  context_types: 'recall',
  quotas: 'recall',
}

/** Fields under each section's "Advanced settings". */
const ADVANCED_FIELDS: Partial<Record<ProfileSection, string[]>> = {
  recall: ['query_max_chars', 'quotas'],
  longConversations: ['context_window'],
  tools: [
    'tool_max_rounds',
    'tool_timeout_seconds',
    'tool_result_bytes',
    'tool_total_seconds',
    'tool_total_tokens',
  ],
}

/** Whether an error key (`field` or `quotas.<category>`) is an advanced setting of `section`. */
function isAdvancedError(key: string, section: ProfileSection): boolean {
  return ADVANCED_FIELDS[section]?.includes(key.split('.')[0]) ?? false
}

/** Sections holding at least one validation problem (`name` belongs to none). */
export function sectionsWithErrors(
  errors: ValidationErrors,
): Set<ProfileSection> {
  const sections = new Set<ProfileSection>()
  for (const key of Object.keys(errors)) {
    const field = key.split('.')[0]
    const section =
      LIST_FIELD_SECTIONS[field] ??
      (field in FIELD_COPY
        ? (FIELD_COPY[field as NumericField].split('.')[0] as ProfileSection)
        : undefined)
    if (section) sections.add(section)
  }
  return sections
}

/** Entries per category when "Limit by category" is first turned on. */
const QUOTA_PRESET = 3

const QUOTA_SOURCE: Record<QuotaBucket, ContextType> = {
  events: 'memory',
  entities: 'memory',
  preferences: 'memory',
  experiences: 'memory',
  resources: 'resource',
  skills: 'skill',
}

/** Starting limits: a few entries from every category whose source is searched. */
function presetQuotas(
  sources: readonly ContextType[],
): Partial<Record<QuotaBucket, number>> {
  return Object.fromEntries(
    QUOTA_BUCKETS.map((bucket) => [
      bucket,
      sources.includes(QUOTA_SOURCE[bucket]) ? QUOTA_PRESET : 0,
    ]),
  )
}

/** Adds or removes `item`, keeping known items in display order. */
function toggleItem<T extends string>(
  list: T[],
  item: T,
  on: boolean,
  order: readonly T[],
): T[] {
  const others = list.filter((x) => x !== item)
  const next = on ? [...others, item] : others
  return [
    ...order.filter((x) => next.includes(x)),
    ...next.filter((x) => !order.includes(x)),
  ]
}

/** A friendlier reading of large seconds and bytes values ("10 min", "64 kB"). */
function humanize(
  unit: Unit | undefined,
  value: number | null,
  locale?: string,
): string | undefined {
  if (value === null || !Number.isFinite(value)) return undefined
  if (unit === 'seconds' && value >= 60) return humanizeSeconds(value, locale)
  if (unit === 'bytes' && value >= 1024) return formatBytes(value, locale)
  return undefined
}

/** Labeled group of checkboxes with a description and an error line. */
function OptionGroup({
  id,
  label,
  description,
  error,
  children,
}: {
  id: string
  label: string
  description?: string
  error?: string
  children: React.ReactNode
}) {
  return (
    <div role="group" aria-labelledby={id} className="grid gap-2">
      <p id={id} className="text-sm font-medium">
        {label}
      </p>
      {children}
      {description ? (
        <p className="text-xs text-muted-foreground">{description}</p>
      ) : null}
      {error ? <FieldError>{error}</FieldError> : null}
    </div>
  )
}

function OptionCheckbox({
  checked,
  onCheckedChange,
  label,
  description,
  badge,
}: {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  label: string
  description?: string
  /** Shown after the label, such as a tool's read-only mark. */
  badge?: React.ReactNode
}) {
  if (!description && !badge) {
    return (
      <Label className="font-normal">
        <Checkbox
          checked={checked}
          aria-label={label}
          onCheckedChange={(value) => onCheckedChange(Boolean(value))}
        />
        {label}
      </Label>
    )
  }
  return (
    <Label className="cursor-pointer items-start gap-3 rounded-lg border p-3 leading-normal font-normal transition-colors hover:bg-muted/30">
      <Checkbox
        checked={checked}
        aria-label={label}
        className="mt-0.5"
        onCheckedChange={(value) => onCheckedChange(Boolean(value))}
      />
      <span className="grid min-w-0 gap-1">
        <span className="flex flex-wrap items-center gap-2 font-medium">
          <span className="break-all">{label}</span>
          {badge}
        </span>
        <span className="text-xs whitespace-pre-wrap text-muted-foreground">
          {description}
        </span>
      </span>
    </Label>
  )
}

type ProfileSettingsFormProps = {
  value: ProfileSettings
  /** Receives the changed fields only; merge them into the draft. */
  onChange: (patch: Partial<ProfileSettings>) => void
  errors: ValidationErrors
}

/**
 * The four setting sections of a context profile. Settings of a section are
 * hidden while its switch is off; every other field of `value` is left as is.
 */
export function ProfileSettingsForm({
  value,
  onChange,
  errors,
}: ProfileSettingsFormProps) {
  const { t, i18n } = useTranslation('gateway')
  const tools = useTools({ enabled: value.gateway_tools })
  const locale = i18n.resolvedLanguage
  // Limits the admin turned off, restored if they turn the limit back on.
  const [lastQuotas, setLastQuotas] = React.useState(value.quotas)

  const message = (key: string) => {
    const issue = errors[key]
    return issue ? t(issue.key, issue.values) : undefined
  }
  const invalidSections = sectionsWithErrors(errors)
  // Error markers and visibility for one section card.
  const validity = (section: ProfileSection) => ({
    invalid: invalidSections.has(section),
    advancedInvalid: Object.keys(errors).some((key) =>
      isAdvancedError(key, section),
    ),
  })

  const numberField = (field: NumericField) => {
    const rule = PROFILE_LIMITS[field]
    const copy = `profiles.${FIELD_COPY[field]}`
    const id = `profile-${field}`
    const current = value[field]
    const fallback = PROFILE_DEFAULTS[field]
    const unit = rule.unit ? t(`units.${rule.unit}`) : undefined
    const hint = humanize(rule.unit, current, locale)
    return (
      <SettingField
        label={t(`${copy}.label`)}
        htmlFor={id}
        description={t(`${copy}.description`)}
        unit={
          unit && hint
            ? t('profiles.editor.unitHint', { unit, value: hint })
            : unit
        }
        defaultValue={
          fallback === null ? t('field.notSet') : formatNumber(fallback, locale)
        }
        error={message(field)}
      >
        <NumberInput
          id={id}
          value={current}
          min={rule.min}
          max={rule.max}
          step={rule.step ?? (rule.integer ? 1 : 'any')}
          placeholder={rule.optional ? t('field.notSet') : undefined}
          aria-invalid={Boolean(errors[field])}
          onChange={(next) =>
            onChange({
              [field]: rule.optional && Number.isNaN(next) ? null : next,
            } as Partial<ProfileSettings>)
          }
        />
      </SettingField>
    )
  }

  const toolOption = (tool: GatewayTool) => (
    <OptionCheckbox
      key={tool.name}
      checked={!value.disabled_tools.includes(tool.name)}
      label={tool.name}
      description={tool.description}
      badge={
        typeof tool.annotations?.readOnlyHint === 'boolean' ? (
          <Badge variant="secondary">
            {t(
              tool.annotations.readOnlyHint
                ? 'profiles.tools.readOnly'
                : 'profiles.tools.modifiesData',
            )}
          </Badge>
        ) : undefined
      }
      onCheckedChange={(checked) =>
        onChange({
          disabled_tools: toggleItem(
            value.disabled_tools,
            tool.name,
            !checked,
            (tools.data ?? []).map((entry) => entry.name),
          ),
        })
      }
    />
  )

  const limitByCategory = Object.keys(value.quotas).length > 0
  const quotasError = message('quotas')
  const unknownBuckets = Object.keys(value.quotas).filter(
    (bucket) => !QUOTA_BUCKETS.includes(bucket as QuotaBucket),
  )

  const quotas = (
    <ToggleRow
      id="profile-quotas"
      label={t('profiles.recall.quotas.label')}
      description={t('profiles.recall.quotas.description')}
      checked={limitByCategory}
      onCheckedChange={(on) => {
        if (on) {
          onChange({
            quotas: Object.keys(lastQuotas).length
              ? lastQuotas
              : presetQuotas(value.context_types),
          })
        } else {
          setLastQuotas(value.quotas)
          onChange({ quotas: {} })
        }
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {QUOTA_BUCKETS.map((bucket) => {
          const id = `profile-quotas-${bucket}`
          return (
            <SettingField
              key={bucket}
              label={t(`profiles.recall.quotas.categories.${bucket}`)}
              htmlFor={id}
              unit={t(`units.${QUOTA_LIMIT.unit ?? 'entries'}`)}
              error={message(`quotas.${bucket}`)}
            >
              <NumberInput
                id={id}
                value={value.quotas[bucket] ?? 0}
                min={QUOTA_LIMIT.min}
                step={1}
                aria-invalid={Boolean(errors[`quotas.${bucket}`])}
                onChange={(next) =>
                  onChange({ quotas: { ...value.quotas, [bucket]: next } })
                }
              />
            </SettingField>
          )
        })}
      </div>
      {unknownBuckets.map((bucket) => (
        <div key={bucket} className="flex items-center justify-between gap-2">
          <FieldError>{message(`quotas.${bucket}`)}</FieldError>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              const { [bucket]: _removed, ...rest } = value.quotas as Record<
                string,
                number
              >
              onChange({ quotas: rest })
            }}
          >
            <XIcon />
            {t('actions.remove')}
          </Button>
        </div>
      ))}
      {quotasError ? <FieldError>{quotasError}</FieldError> : null}
    </ToggleRow>
  )

  return (
    <>
      <SettingSection
        id={sectionAnchor('recall')}
        {...validity('recall')}
        icon={<BrainIcon />}
        title={t('profiles.recall.title')}
        description={t('profiles.recall.description')}
        checked={value.recall}
        onCheckedChange={(recall) => onChange({ recall })}
        note={
          <div className="grid gap-4">
            <ToggleRow
              id="profile-profile"
              label={t('profiles.recall.profile.label')}
              description={t('profiles.recall.profile.description')}
              checked={value.profile}
              onCheckedChange={(profile) => onChange({ profile })}
            />
            {numberField('profile_max_tokens')}
            <ToggleRow
              id="profile-show_recall"
              label={t('profiles.recall.showRecall.label')}
              description={t('profiles.recall.showRecall.description')}
              checked={value.show_recall}
              onCheckedChange={(show_recall) => onChange({ show_recall })}
            />
          </div>
        }
        advanced={
          <>
            <div className="grid gap-5 md:grid-cols-2">
              {numberField('query_max_chars')}
            </div>
            {quotas}
          </>
        }
      >
        <OptionGroup
          id="profile-context_types"
          label={t('profiles.recall.sources.label')}
          description={t('profiles.recall.sources.description')}
          error={message('context_types')}
        >
          <div className="flex flex-wrap gap-x-6 gap-y-3">
            {CONTEXT_TYPES.map((type) => (
              <OptionCheckbox
                key={type}
                checked={value.context_types.includes(type)}
                label={t(`profiles.recall.sources.${type}`)}
                onCheckedChange={(checked) =>
                  onChange({
                    context_types: toggleItem(
                      value.context_types,
                      type,
                      checked,
                      CONTEXT_TYPES,
                    ),
                  })
                }
              />
            ))}
          </div>
        </OptionGroup>
        <div className="grid gap-5 md:grid-cols-2">
          {numberField('max_tokens')}
          {numberField('session_max_tokens')}
          {numberField('score_threshold')}
          {numberField('recall_timeout')}
        </div>
      </SettingSection>

      <SettingSection
        id={sectionAnchor('capture')}
        {...validity('capture')}
        icon={<SaveIcon />}
        title={t('profiles.capture.title')}
        description={t('profiles.capture.description')}
        checked={value.capture}
        onCheckedChange={(capture) => onChange({ capture })}
      >
        <div className="grid gap-5 md:grid-cols-2">
          {numberField('idle_seconds')}
          {numberField('commit_tokens')}
          {numberField('keep_recent_messages')}
        </div>
      </SettingSection>

      <SettingSection
        id={sectionAnchor('longConversations')}
        {...validity('longConversations')}
        icon={<ScrollTextIcon />}
        title={t('profiles.longConversations.title')}
        description={t('profiles.longConversations.description')}
        advanced={
          <div className="grid gap-5 md:grid-cols-2">
            {numberField('context_window')}
          </div>
        }
      >
        <ToggleRow
          id="profile-compaction"
          label={t('profiles.longConversations.compaction.label')}
          description={t('profiles.longConversations.compaction.description')}
          checked={value.compaction}
          invalid={Boolean(
            errors.compaction_threshold || errors.summary_max_tokens,
          )}
          onCheckedChange={(compaction) => onChange({ compaction })}
        >
          <div className="grid gap-5 md:grid-cols-2">
            {numberField('compaction_threshold')}
            {numberField('summary_max_tokens')}
          </div>
        </ToggleRow>
        <ToggleRow
          id="profile-agent_windows"
          label={t('profiles.longConversations.agentWindows.label')}
          badge={
            <Badge variant="outline">
              {t('profiles.longConversations.agentWindows.experimental')}
            </Badge>
          }
          description={t('profiles.longConversations.agentWindows.description')}
          checked={value.agent_windows}
          invalid={Boolean(
            errors.window_soft_ratio || errors.window_hard_ratio,
          )}
          onCheckedChange={(agent_windows) => onChange({ agent_windows })}
        >
          {value.gateway_tools ? null : (
            <Notice tone="info">
              {t('profiles.longConversations.agentWindows.needsTools')}
            </Notice>
          )}
          <div className="grid gap-5 md:grid-cols-2">
            {numberField('window_soft_ratio')}
            {numberField('window_hard_ratio')}
          </div>
        </ToggleRow>
      </SettingSection>

      <SettingSection
        id={sectionAnchor('tools')}
        {...validity('tools')}
        icon={<WrenchIcon />}
        title={t('profiles.tools.title')}
        description={t('profiles.tools.description')}
        checked={value.gateway_tools}
        onCheckedChange={(gateway_tools) => onChange({ gateway_tools })}
        advanced={
          <div className="grid gap-5 md:grid-cols-2">
            {numberField('tool_max_rounds')}
            {numberField('tool_timeout_seconds')}
            {numberField('tool_result_bytes')}
            {numberField('tool_total_seconds')}
            {numberField('tool_total_tokens')}
          </div>
        }
      >
        <OptionGroup
          id="profile-disabled_tools"
          label={t('profiles.tools.available.label')}
          description={t('profiles.tools.available.description')}
        >
          {tools.isPending ? (
            <LoadingState className="min-h-24" />
          ) : tools.isError ? (
            <ErrorState
              className="min-h-24"
              title={t('profiles.tools.loadFailed')}
              error={tools.error}
              retrying={tools.isFetching}
              onRetry={() => void tools.refetch()}
            />
          ) : tools.data.length === 0 ? (
            <Notice tone="info">{t('profiles.tools.empty')}</Notice>
          ) : (
            <div className="grid gap-2 md:grid-cols-2">
              {tools.data.map(toolOption)}
            </div>
          )}
        </OptionGroup>
        <Notice tone="warning">{t('profiles.tools.executionNotice')}</Notice>
        <ToggleRow
          id="profile-show_tool_calls"
          label={t('profiles.tools.showCalls.label')}
          description={t('profiles.tools.showCalls.description')}
          checked={value.show_tool_calls}
          onCheckedChange={(show_tool_calls) => onChange({ show_tool_calls })}
        />
      </SettingSection>
    </>
  )
}
