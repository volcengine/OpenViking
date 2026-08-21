import {
  ChevronDown,
  FolderOpen,
  RotateCcw,
  SlidersHorizontal,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import type { TFunction } from 'i18next'

import { Button } from '#/components/ui/button'
import { Checkbox } from '#/components/ui/checkbox'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { Switch } from '#/components/ui/switch'
import { cn } from '#/lib/utils'
import type { FindContextType } from '#/lib/retrieval'

import {
  RESULT_COUNT_OPTIONS,
  RETRIEVAL_MODES,
  RETRIEVAL_SCOPES,
} from '../-constants/retrieval'
import type {
  RetrievalMode,
  RetrievalRequestOptions,
} from '../-types/retrieval'
import { DateRangePicker } from './date-range-picker'
import { TagFilterInput } from './tag-filter-input'

const CONTEXT_TYPES: FindContextType[] = ['resource', 'memory', 'skill']
const LEVELS = [0, 1, 2] as const

export function RetrievalControls({
  mode,
  onModeChange,
  onOptionsChange,
  options,
  t,
}: {
  mode: RetrievalMode
  onModeChange: (value: RetrievalMode) => void
  onOptionsChange: (patch: Partial<RetrievalRequestOptions>) => void
  options: RetrievalRequestOptions
  t: TFunction<'retrieval'>
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const semanticMode = mode === 'find' || mode === 'search'
  const advancedCount = useMemo(
    () =>
      [
        options.contextTypes.length > 0,
        options.tags.length > 0,
        options.scoreThreshold !== undefined,
        options.levels.length > 0,
        Boolean(options.since || options.until),
        options.includeProvenance,
      ].filter(Boolean).length,
    [
      options.contextTypes.length,
      options.includeProvenance,
      options.levels.length,
      options.scoreThreshold,
      options.since,
      options.tags.length,
      options.until,
    ],
  )

  const toggleContextType = (type: FindContextType, checked: boolean) => {
    onOptionsChange({
      contextTypes: checked
        ? [...options.contextTypes, type]
        : options.contextTypes.filter((item) => item !== type),
    })
  }

  const toggleLevel = (level: number, checked: boolean) => {
    onOptionsChange({
      levels: checked
        ? [...options.levels, level].toSorted()
        : options.levels.filter((item) => item !== level),
    })
  }

  const resetAdvanced = () => {
    onOptionsChange({
      contextTypes: [],
      includeProvenance: false,
      levels: [],
      scoreThreshold: undefined,
      since: undefined,
      tags: [],
      timeField: 'updated_at',
      until: undefined,
    })
  }

  return (
    <div className="space-y-3">
      <div
        aria-label={t('controls.function')}
        className="inline-flex flex-wrap gap-1 rounded-lg border bg-muted/30 p-1"
        role="tablist"
      >
        {RETRIEVAL_MODES.map((item) => (
          <Button
            aria-selected={mode === item}
            className="h-7 px-3 font-mono text-xs"
            key={item}
            onClick={() => onModeChange(item)}
            role="tab"
            size="sm"
            variant={mode === item ? 'secondary' : 'ghost'}
          >
            {t(`controls.modes.${item}`)}
          </Button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={String(options.resultCount)}
          onValueChange={(value) =>
            onOptionsChange({ resultCount: Number(value) })
          }
        >
          <SelectTrigger size="sm" aria-label={t('controls.resultCount')}>
            <SelectValue>
              {t('controls.resultCount')} {options.resultCount}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {RESULT_COUNT_OPTIONS.map((option) => (
              <SelectItem key={option} value={String(option)}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={options.scope}
          onValueChange={(value) =>
            onOptionsChange({
              scope: value as RetrievalRequestOptions['scope'],
            })
          }
        >
          <SelectTrigger size="sm" aria-label={t('controls.scope')}>
            <SelectValue>
              {t(`controls.scopes.${options.scope}.label`)}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {RETRIEVAL_SCOPES.map((item) => (
              <SelectItem key={item} value={item}>
                {t(`controls.scopes.${item}.label`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {options.scope === 'custom' ? (
          <Input
            aria-label={t('controls.customScope')}
            className="h-8 w-64 font-mono text-sm"
            onChange={(event) =>
              onOptionsChange({ customPathInput: event.target.value })
            }
            placeholder={t('controls.customScopePlaceholder')}
            value={options.customPathInput}
          />
        ) : null}

        <div className="inline-flex h-8 max-w-full items-center gap-1.5 rounded-md border bg-muted/30 px-2.5 text-xs text-muted-foreground">
          <FolderOpen className="size-3.5 shrink-0" />
          <span className="shrink-0">{t('controls.effectiveScope')}</span>
          <span className="max-w-64 truncate font-mono text-foreground">
            {options.targetUri ?? t('controls.allContexts')}
          </span>
        </div>

        {mode === 'search' ? (
          <Input
            aria-label={t('controls.sessionId')}
            className="h-8 w-52 font-mono text-sm"
            onChange={(event) =>
              onOptionsChange({ sessionId: event.target.value })
            }
            placeholder={t('controls.sessionPlaceholder')}
            value={options.sessionId ?? ''}
          />
        ) : null}

        {mode === 'grep' ? (
          <label className="inline-flex h-8 cursor-pointer items-center gap-2 rounded-md border bg-muted/20 px-2.5 text-xs text-foreground">
            <Checkbox
              checked={options.ignoreCase}
              onCheckedChange={(checked) =>
                onOptionsChange({ ignoreCase: checked === true })
              }
            />
            {t('controls.ignoreCase')}
          </label>
        ) : null}

        {semanticMode ? (
          <Button
            className="h-8 gap-1.5"
            onClick={() => setAdvancedOpen((open) => !open)}
            size="sm"
            variant="outline"
          >
            <SlidersHorizontal className="size-3.5" />
            {t('advanced.title')}
            {advancedCount > 0 ? (
              <span className="rounded-full bg-primary/10 px-1.5 text-[10px] text-primary">
                {advancedCount}
              </span>
            ) : null}
            <ChevronDown
              className={cn(
                'size-3.5 transition-transform',
                advancedOpen && 'rotate-180',
              )}
            />
          </Button>
        ) : null}
      </div>

      {semanticMode && advancedOpen ? (
        <SemanticAdvancedControls
          onOptionsChange={onOptionsChange}
          onReset={resetAdvanced}
          options={options}
          t={t}
          toggleContextType={toggleContextType}
          toggleLevel={toggleLevel}
        />
      ) : null}
    </div>
  )
}

function SemanticAdvancedControls({
  onOptionsChange,
  onReset,
  options,
  t,
  toggleContextType,
  toggleLevel,
}: {
  onOptionsChange: (patch: Partial<RetrievalRequestOptions>) => void
  onReset: () => void
  options: RetrievalRequestOptions
  t: TFunction<'retrieval'>
  toggleContextType: (type: FindContextType, checked: boolean) => void
  toggleLevel: (level: number, checked: boolean) => void
}) {
  return (
    <div className="rounded-xl border bg-card/50 p-4">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <ControlGroup label={t('advanced.contextTypes')}>
          <div className="flex flex-wrap gap-2">
            {CONTEXT_TYPES.map((type) => (
              <CheckPill
                checked={options.contextTypes.includes(type)}
                key={type}
                label={t(`types.${type}`)}
                onCheckedChange={(checked) => toggleContextType(type, checked)}
              />
            ))}
          </div>
        </ControlGroup>

        <ControlGroup label={t('advanced.levels')}>
          <div className="flex gap-2">
            {LEVELS.map((level) => (
              <CheckPill
                checked={options.levels.includes(level)}
                key={level}
                label={`L${level}`}
                onCheckedChange={(checked) => toggleLevel(level, checked)}
              />
            ))}
          </div>
        </ControlGroup>

        <ControlGroup label={t('advanced.scoreThreshold')}>
          <Input
            aria-label={t('advanced.scoreThreshold')}
            className="h-8"
            max={1}
            min={0}
            onChange={(event) => {
              const value = event.target.value
              onOptionsChange({
                scoreThreshold: value === '' ? undefined : Number(value),
              })
            }}
            placeholder="0.1"
            step={0.05}
            type="number"
            value={options.scoreThreshold ?? ''}
          />
        </ControlGroup>

        <ControlGroup label={t('advanced.tags')}>
          <TagFilterInput
            ariaLabel={t('advanced.tags')}
            invalidMessage={t('advanced.tagsInvalid')}
            onChange={(tags) => onOptionsChange({ tags })}
            placeholder={t('advanced.tagsPlaceholder')}
            value={options.tags}
          />
        </ControlGroup>

        <ControlGroup label={t('advanced.timeRange')}>
          <DateRangePicker
            onChange={onOptionsChange}
            since={options.since}
            t={t}
            until={options.until}
          />
        </ControlGroup>

        <ControlGroup label={t('advanced.timeField')}>
          <Select
            onValueChange={(value) =>
              onOptionsChange({
                timeField: value as RetrievalRequestOptions['timeField'],
              })
            }
            value={options.timeField}
          >
            <SelectTrigger
              aria-label={t('advanced.timeField')}
              className="h-8 w-full"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="updated_at">
                {t('advanced.updatedAt')}
              </SelectItem>
              <SelectItem value="created_at">
                {t('advanced.createdAt')}
              </SelectItem>
            </SelectContent>
          </Select>
        </ControlGroup>
      </div>

      <div className="mt-4 flex items-center justify-between border-t pt-3">
        <label className="flex items-center gap-2 text-xs">
          <Switch
            checked={options.includeProvenance}
            onCheckedChange={(checked) =>
              onOptionsChange({ includeProvenance: checked })
            }
          />
          {t('advanced.provenance')}
        </label>
        <Button className="gap-1.5" onClick={onReset} size="sm" variant="ghost">
          <RotateCcw className="size-3.5" />
          {t('advanced.reset')}
        </Button>
      </div>
    </div>
  )
}

function ControlGroup({
  children,
  label,
}: {
  children: React.ReactNode
  label: string
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  )
}

function CheckPill({
  checked,
  label,
  onCheckedChange,
}: {
  checked: boolean
  label: string
  onCheckedChange: (checked: boolean) => void
}) {
  return (
    <label
      className={cn(
        'flex h-8 cursor-pointer items-center gap-2 rounded-md border px-2.5 text-xs',
        checked
          ? 'border-primary/30 bg-primary/5 text-primary'
          : 'bg-background',
      )}
    >
      <Checkbox
        checked={checked}
        onCheckedChange={(value) => onCheckedChange(value === true)}
      />
      {label}
    </label>
  )
}
