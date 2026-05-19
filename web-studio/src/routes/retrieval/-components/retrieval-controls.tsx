import { FolderOpen } from 'lucide-react'
import type { TFunction } from 'i18next'

import { Input } from '#/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'

import {
  RESULT_COUNT_OPTIONS,
  RETRIEVAL_MODES,
  RETRIEVAL_SCOPES,
} from '../-constants/retrieval'
import type { RetrievalMode, RetrievalScope } from '../-types/retrieval'

export function RetrievalControls({
  customPathInput,
  mode,
  onCustomPathInputChange,
  onModeChange,
  onResultCountChange,
  onScopeChange,
  onSessionIdInputChange,
  resultCount,
  scope,
  sessionIdInput,
  t,
  targetUri,
}: {
  customPathInput: string
  mode: RetrievalMode
  onCustomPathInputChange: (value: string) => void
  onModeChange: (value: RetrievalMode) => void
  onResultCountChange: (value: number) => void
  onScopeChange: (value: RetrievalScope) => void
  onSessionIdInputChange: (value: string) => void
  resultCount: number
  scope: RetrievalScope
  sessionIdInput: string
  t: TFunction<'retrieval'>
  targetUri?: string
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        value={mode}
        onValueChange={(value) => onModeChange(value as RetrievalMode)}
      >
        <SelectTrigger size="sm" aria-label={t('controls.function')}>
          <SelectValue>{t(`controls.modes.${mode}`)}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {RETRIEVAL_MODES.map((item) => (
            <SelectItem key={item} value={item}>
              {t(`controls.modes.${item}`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={String(resultCount)}
        onValueChange={(value) => onResultCountChange(Number(value))}
      >
        <SelectTrigger size="sm" aria-label={t('controls.resultCount')}>
          <SelectValue>
            {t('controls.resultCount')} {resultCount}
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
        value={scope}
        onValueChange={(value) => onScopeChange(value as RetrievalScope)}
      >
        <SelectTrigger size="sm" aria-label={t('controls.scope')}>
          <SelectValue>{t(`controls.scopes.${scope}.label`)}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {RETRIEVAL_SCOPES.map((item) => (
            <SelectItem key={item} value={item}>
              {t(`controls.scopes.${item}.label`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {scope === 'custom' && (
        <Input
          value={customPathInput}
          onChange={(event) => onCustomPathInputChange(event.target.value)}
          placeholder={t('controls.customScopePlaceholder')}
          aria-label={t('controls.customScope')}
          className="h-8 w-64 font-mono text-sm"
        />
      )}

      <div className="inline-flex h-8 max-w-full items-center gap-1.5 rounded-md border bg-muted/30 px-2.5 text-xs text-muted-foreground">
        <FolderOpen className="size-3.5 shrink-0" />
        <span className="shrink-0">{t('controls.effectiveScope')}</span>
        <span className="max-w-64 truncate font-mono text-foreground">
          {targetUri ?? t('controls.allContexts')}
        </span>
      </div>

      {mode === 'search' && (
        <Input
          value={sessionIdInput}
          onChange={(event) => onSessionIdInputChange(event.target.value)}
          placeholder={t('controls.sessionPlaceholder')}
          aria-label={t('controls.sessionId')}
          className="h-8 w-52 font-mono text-sm"
        />
      )}
    </div>
  )
}
