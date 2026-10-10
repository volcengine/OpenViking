import type * as React from 'react'
import {
  BoxesIcon,
  CornerDownRightIcon,
  GlobeIcon,
  KeyRoundIcon,
  RouteIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Checkbox } from '#/components/ui/checkbox'
import { Input } from '#/components/ui/input'
import { RadioGroup, RadioGroupItem } from '#/components/ui/radio-group'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { PLAIN_INPUT_PROPS } from '#/lib/form-input'
import { cn } from '#/lib/utils'

import type {
  AuthMode,
  Protocol,
  Upstream,
  UpstreamInput,
  Vendor,
} from '../-lib/api'
import { formatNumber } from '../-lib/format'
import { protocolLabel, vendorLabel } from '../-lib/localize'
import {
  ARK_VENDORS,
  PROTOCOLS,
  PROTOCOL_PATHS,
  UPSTREAM_DEFAULTS,
  UPSTREAM_KEY_HEADER,
  VENDORS,
  baseUrlAfterSwitch,
  defaultBaseUrl,
  endpointPreview,
  protocolFor,
  replayReasoningSetting,
  replaysReasoning,
  supportsProtocol,
  usesDefaultBaseUrl,
} from '../-lib/upstream-schema'
import { KeyValueEditor } from './key-value-editor'
import { Notice } from './notice'
import { NumberInput } from './number-input'
import { SettingField } from './setting-field'
import { SettingSection } from './setting-section'
import { ToneBadge } from './status-badges'
import { TagInput } from './tag-input'
import { ToggleRow } from './toggle-row'

export type UpstreamField = keyof UpstreamInput

/** Base URL placeholder for providers without a default, such as Generic. */
const BASE_URL_EXAMPLE = 'https://api.example.com/v1'

/** Providers with a hint of their own under `upstreams.form.vendor.hints`. */
const VENDOR_HINTS: Vendor[] = ['deepseek', 'ark', 'byteplus']

const AUTH_MODES: AuthMode[] = ['managed', 'passthrough']

/** Translation key under `upstreams.form` of each field's label. */
export const UPSTREAM_FIELD_LABELS: Partial<Record<UpstreamField, string>> = {
  name: 'name',
  protocol: 'protocol',
  base_url: 'baseUrl',
  api_key: 'apiKey',
  headers: 'headers',
  aliases: 'aliases',
  context_windows: 'contextWindows',
  priority: 'priority',
  cache_min_tokens: 'cacheMinTokens',
}

export type UpstreamFormProps = {
  draft: UpstreamInput
  /** The saved upstream when editing; its secrets count as kept when left blank. */
  stored?: Upstream
  onChange: <TField extends UpstreamField>(
    field: TField,
    value: UpstreamInput[TField],
  ) => void
  /**
   * Sets a value the form fills in itself, such as a provider's default base
   * URL, without marking the field as visited.
   */
  onFill: <TField extends UpstreamField>(
    field: TField,
    value: UpstreamInput[TField],
  ) => void
  /** Marks a field as visited so its validation message shows. */
  onBlur: (field: UpstreamField) => void
  /** Translated validation message for a field, once it should be shown. */
  error: (field: UpstreamField) => string | undefined
}

/** Selectable card for one option of a `RadioGroup`. */
function ChoiceCard({
  value,
  title,
  description,
  detail,
  unavailable,
}: {
  value: string
  title: React.ReactNode
  description: React.ReactNode
  /** Monospace line under the description, e.g. an API path. */
  detail?: string
  /** Why the option can't be picked; disables it and replaces `detail`. */
  unavailable?: string
}) {
  return (
    <label
      className={cn(
        'flex items-start gap-3 rounded-lg border p-3 transition-colors has-data-checked:border-primary/40 has-data-checked:bg-primary/[0.04] dark:has-data-checked:bg-primary/[0.08]',
        unavailable
          ? 'cursor-not-allowed bg-muted/30'
          : 'cursor-pointer hover:bg-muted/30',
      )}
    >
      <RadioGroupItem
        value={value}
        disabled={Boolean(unavailable)}
        className="mt-0.5 data-disabled:opacity-50"
      />
      <span className="grid min-w-0 gap-1">
        <span
          className={cn(
            'text-sm font-medium',
            unavailable && 'text-muted-foreground',
          )}
        >
          {title}
        </span>
        <span className="text-xs leading-5 text-muted-foreground">
          {description}
        </span>
        {unavailable ? (
          <span className="text-xs text-muted-foreground">{unavailable}</span>
        ) : detail ? (
          <code className="truncate font-mono text-[11px] text-muted-foreground">
            {detail}
          </code>
        ) : null}
      </span>
    </label>
  )
}

/** Checkbox with a title and a description, clickable as a whole. */
function CheckRow({
  checked,
  onCheckedChange,
  label,
  description,
}: {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  label: string
  description: string
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <Checkbox
        checked={checked}
        className="mt-0.5"
        onCheckedChange={(value) => onCheckedChange(value)}
      />
      <span className="grid gap-1">
        <span className="text-sm leading-none font-medium">{label}</span>
        <span className="text-xs leading-5 text-muted-foreground">
          {description}
        </span>
      </span>
    </label>
  )
}

/** The four setting groups of the upstream editor. */
export function UpstreamForm({
  draft,
  stored,
  onChange,
  onFill,
  onBlur,
  error,
}: UpstreamFormProps) {
  const { t, i18n } = useTranslation('gateway')
  const preview = endpointPreview(draft)
  const defaultUrl = defaultBaseUrl(draft.vendor, draft.protocol)
  const keyStored = Boolean(stored?.has_api_key)
  const vendorHint = VENDOR_HINTS.includes(draft.vendor)
    ? t(`upstreams.form.vendor.hints.${draft.vendor}`)
    : t('upstreams.form.vendor.description')

  /**
   * Applies a provider or protocol choice. The calls are batched into one
   * render, so no render pairs a provider with a protocol it doesn't offer or
   * with the previous provider's default URL. The URL is filled in, not
   * entered, so clearing it for Generic shows no error until the admin edits it.
   */
  function switchEndpoint(vendor: Vendor, protocol: Protocol) {
    if (vendor !== draft.vendor) onChange('vendor', vendor)
    if (protocol !== draft.protocol) onChange('protocol', protocol)
    const baseUrl = baseUrlAfterSwitch(draft, vendor, protocol)
    if (baseUrl !== draft.base_url) onFill('base_url', baseUrl)
  }

  return (
    <div className="grid gap-5">
      <SettingSection
        icon={<GlobeIcon />}
        title={t('upstreams.sections.endpoint.title')}
        description={t('upstreams.sections.endpoint.description')}
      >
        <SettingField
          label={t('upstreams.form.name.label')}
          htmlFor="upstream-name"
          description={t('upstreams.form.name.description')}
          error={error('name')}
        >
          <Input
            id="upstream-name"
            value={draft.name}
            placeholder={t('upstreams.form.name.placeholder')}
            aria-invalid={Boolean(error('name'))}
            className="sm:max-w-md"
            onChange={(event) => onChange('name', event.target.value)}
            onBlur={() => onBlur('name')}
          />
        </SettingField>
        <SettingField
          label={t('upstreams.form.vendor.label')}
          htmlFor="upstream-vendor"
          description={vendorHint}
        >
          <Select
            value={draft.vendor}
            onValueChange={(value) => {
              const vendor = VENDORS.find((item) => item === value)
              if (!vendor) return
              switchEndpoint(vendor, protocolFor(vendor, draft.protocol))
            }}
          >
            <SelectTrigger id="upstream-vendor" className="w-full sm:w-72">
              <SelectValue>{vendorLabel(t, draft.vendor)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {VENDORS.map((vendor) => (
                <SelectItem key={vendor} value={vendor}>
                  {vendorLabel(t, vendor)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingField>
        <SettingField
          label={t('upstreams.form.protocol.label')}
          description={t('upstreams.form.protocol.description')}
          error={error('protocol')}
        >
          {/* Base UI sets the Tab stop from the checked card only on mount,
              so remount when the provider may have switched the protocol. */}
          <RadioGroup
            key={draft.vendor}
            value={draft.protocol}
            aria-label={t('upstreams.form.protocol.label')}
            aria-invalid={Boolean(error('protocol'))}
            className="grid gap-2 md:grid-cols-3"
            onValueChange={(value) => {
              const protocol = PROTOCOLS.find((item) => item === value)
              if (protocol) switchEndpoint(draft.vendor, protocol)
            }}
          >
            {PROTOCOLS.map((protocol) => (
              <ChoiceCard
                key={protocol}
                value={protocol}
                title={protocolLabel(t, protocol)}
                description={t(`upstreams.form.protocol.options.${protocol}`)}
                detail={PROTOCOL_PATHS[protocol]}
                unavailable={
                  supportsProtocol(draft.vendor, protocol)
                    ? undefined
                    : t('upstreams.form.protocol.unsupported', {
                        vendor: vendorLabel(t, draft.vendor),
                      })
                }
              />
            ))}
          </RadioGroup>
        </SettingField>
        <SettingField
          label={t('upstreams.form.baseUrl.label')}
          htmlFor="upstream-base-url"
          description={t('upstreams.form.baseUrl.description')}
          error={error('base_url')}
        >
          <div className="grid gap-1.5">
            <Input
              {...PLAIN_INPUT_PROPS}
              id="upstream-base-url"
              type="url"
              inputMode="url"
              value={draft.base_url}
              placeholder={defaultUrl || BASE_URL_EXAMPLE}
              aria-invalid={Boolean(error('base_url'))}
              className="font-mono"
              onChange={(event) => onChange('base_url', event.target.value)}
              onBlur={() => onBlur('base_url')}
            />
            {preview ? (
              <p
                aria-live="polite"
                className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground"
              >
                <CornerDownRightIcon className="size-3.5 shrink-0" />
                <span className="shrink-0">
                  {t('upstreams.form.baseUrl.preview')}
                </span>
                <code
                  className="min-w-0 truncate font-mono text-foreground/80"
                  title={preview}
                >
                  {preview}
                </code>
              </p>
            ) : null}
            {defaultUrl && !usesDefaultBaseUrl(draft) ? (
              <button
                type="button"
                className="flex max-w-full min-w-0 gap-1 justify-self-start text-xs font-medium whitespace-nowrap text-foreground underline underline-offset-4"
                onClick={() => onChange('base_url', defaultUrl)}
              >
                {t('upstreams.form.baseUrl.useDefault')}{' '}
                <code className="min-w-0 truncate font-mono">{defaultUrl}</code>
              </button>
            ) : null}
          </div>
        </SettingField>
      </SettingSection>

      <SettingSection
        icon={<KeyRoundIcon />}
        title={t('upstreams.sections.credentials.title')}
        description={t('upstreams.sections.credentials.description')}
      >
        <SettingField label={t('upstreams.form.authMode.label')}>
          <RadioGroup
            value={draft.auth_mode}
            aria-label={t('upstreams.form.authMode.label')}
            className="grid gap-2 md:grid-cols-2"
            onValueChange={(value) => {
              const mode = AUTH_MODES.find((item) => item === value)
              if (mode) onChange('auth_mode', mode)
            }}
          >
            {AUTH_MODES.map((mode) => (
              <ChoiceCard
                key={mode}
                value={mode}
                title={t(`upstreams.form.authMode.${mode}.title`)}
                description={t(`upstreams.form.authMode.${mode}.description`, {
                  header: UPSTREAM_KEY_HEADER,
                })}
              />
            ))}
          </RadioGroup>
        </SettingField>
        {draft.auth_mode === 'managed' ? (
          <SettingField
            label={
              <>
                {t('upstreams.form.apiKey.label')}
                {keyStored ? (
                  <ToneBadge tone="success">
                    {t('upstreams.form.apiKey.stored')}
                  </ToneBadge>
                ) : null}
              </>
            }
            htmlFor="upstream-api-key"
            description={t(
              keyStored
                ? 'upstreams.form.apiKey.storedDescription'
                : 'upstreams.form.apiKey.description',
            )}
            error={error('api_key')}
          >
            <Input
              {...PLAIN_INPUT_PROPS}
              id="upstream-api-key"
              type="password"
              autoComplete="new-password"
              value={draft.api_key}
              placeholder={t(
                keyStored
                  ? 'field.storedSecret'
                  : 'upstreams.form.apiKey.placeholder',
              )}
              aria-invalid={Boolean(error('api_key'))}
              className="font-mono sm:max-w-md"
              onChange={(event) => onChange('api_key', event.target.value)}
              onBlur={() => onBlur('api_key')}
            />
          </SettingField>
        ) : null}
        <SettingField
          label={t('upstreams.form.headers.label')}
          description={t('upstreams.form.headers.description')}
          error={error('headers')}
        >
          <KeyValueEditor
            value={draft.headers}
            storedKeys={stored?.header_names}
            maskValues
            keyPlaceholder={t('upstreams.form.headers.name')}
            valuePlaceholder={t('upstreams.form.headers.value')}
            addLabel={t('upstreams.form.headers.add')}
            aria-invalid={Boolean(error('headers'))}
            onChange={(headers) => onChange('headers', headers)}
          />
        </SettingField>
        <div className="grid gap-3">
          <CheckRow
            checked={draft.coding_plan}
            label={t('upstreams.form.codingPlan.label')}
            description={t('upstreams.form.codingPlan.description')}
            onCheckedChange={(checked) => {
              onChange('coding_plan', checked)
              if (!checked) onChange('allow_coding_plan', false)
            }}
          />
          {draft.coding_plan ? (
            <Notice tone="warning" className="ml-7">
              <p>{t('upstreams.form.codingPlan.warning')}</p>
              <div className="mt-2">
                <CheckRow
                  checked={draft.allow_coding_plan}
                  label={t('upstreams.form.codingPlan.allow')}
                  description={t('upstreams.form.codingPlan.allowDescription')}
                  onCheckedChange={(checked) =>
                    onChange('allow_coding_plan', checked)
                  }
                />
              </div>
            </Notice>
          ) : null}
        </div>
      </SettingSection>

      <SettingSection
        icon={<BoxesIcon />}
        title={t('upstreams.sections.models.title')}
        description={t('upstreams.sections.models.description')}
      >
        <SettingField
          label={t('upstreams.form.models.label')}
          htmlFor="upstream-models"
          description={t('upstreams.form.models.description')}
        >
          <TagInput
            id="upstream-models"
            value={draft.models}
            placeholder={t('upstreams.form.models.placeholder')}
            aria-label={t('upstreams.form.models.label')}
            onChange={(models) => onChange('models', models)}
          />
        </SettingField>
        <SettingField
          label={t('upstreams.form.aliases.label')}
          description={t('upstreams.form.aliases.description')}
          error={error('aliases')}
        >
          <KeyValueEditor
            value={draft.aliases}
            keyPlaceholder={t('upstreams.form.aliases.name')}
            valuePlaceholder={t('upstreams.form.aliases.target')}
            addLabel={t('upstreams.form.aliases.add')}
            aria-invalid={Boolean(error('aliases'))}
            onChange={(aliases) => onChange('aliases', aliases)}
          />
        </SettingField>
        <SettingField
          label={t('upstreams.form.contextWindows.label')}
          description={t('upstreams.form.contextWindows.description')}
          error={error('context_windows')}
        >
          <KeyValueEditor
            numeric
            value={draft.context_windows}
            keyPlaceholder={t('upstreams.form.contextWindows.model')}
            valuePlaceholder={t('upstreams.form.contextWindows.tokens')}
            addLabel={t('upstreams.form.contextWindows.add')}
            aria-invalid={Boolean(error('context_windows'))}
            onChange={(windows) => onChange('context_windows', windows)}
          />
        </SettingField>
      </SettingSection>

      <SettingSection
        icon={<RouteIcon />}
        title={t('upstreams.sections.routing.title')}
        description={t('upstreams.sections.routing.description')}
      >
        <SettingField
          label={t('upstreams.form.priority.label')}
          htmlFor="upstream-priority"
          description={t('upstreams.form.priority.description')}
          defaultValue={formatNumber(
            UPSTREAM_DEFAULTS.priority,
            i18n.resolvedLanguage,
          )}
          error={error('priority')}
        >
          <NumberInput
            id="upstream-priority"
            value={draft.priority}
            step={1}
            aria-invalid={Boolean(error('priority'))}
            className="sm:max-w-xs"
            onChange={(priority) => onChange('priority', priority)}
            onBlur={() => onBlur('priority')}
          />
        </SettingField>
        <ToggleRow
          id="upstream-enabled"
          label={t('upstreams.form.enabled.label')}
          description={t('upstreams.form.enabled.description')}
          checked={draft.enabled}
          onCheckedChange={(enabled) => onChange('enabled', enabled)}
        />
        <ToggleRow
          id="upstream-gateway-tools"
          label={t('upstreams.form.gatewayTools.label')}
          description={t('upstreams.form.gatewayTools.description')}
          checked={draft.allow_gateway_tools}
          onCheckedChange={(allowed) =>
            onChange('allow_gateway_tools', allowed)
          }
        />
        <ToggleRow
          id="upstream-replay-reasoning"
          label={t('upstreams.form.replayReasoning.label')}
          description={t('upstreams.form.replayReasoning.description')}
          checked={replaysReasoning(draft)}
          onCheckedChange={(checked) =>
            onChange(
              'replay_reasoning',
              replayReasoningSetting(draft.vendor, checked),
            )
          }
        />
        {ARK_VENDORS.includes(draft.vendor) ? (
          <SettingField
            label={t('upstreams.form.cacheMinTokens.label')}
            htmlFor="upstream-cache-min-tokens"
            description={t('upstreams.form.cacheMinTokens.description')}
            unit={t('units.tokens')}
            defaultValue={formatNumber(
              UPSTREAM_DEFAULTS.cache_min_tokens,
              i18n.resolvedLanguage,
            )}
            error={error('cache_min_tokens')}
            className="sm:max-w-xs"
          >
            <NumberInput
              id="upstream-cache-min-tokens"
              value={draft.cache_min_tokens}
              min={0}
              step={1}
              aria-invalid={Boolean(error('cache_min_tokens'))}
              onChange={(tokens) => onChange('cache_min_tokens', tokens)}
              onBlur={() => onBlur('cache_min_tokens')}
            />
          </SettingField>
        ) : null}
      </SettingSection>
    </div>
  )
}
