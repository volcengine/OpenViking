import type { TFunction } from 'i18next'

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
import type {
  ResourceProcessingMode,
  ResourceTagMode,
} from '@ov-server/api/v1/resources'

export type ResourceParseMode = 'default' | 'no_split'

export type AdditionalResourceOptionsValue = {
  parseMode: ResourceParseMode
  preserveStructure: boolean
  processingMode: ResourceProcessingMode
  sourceName: string
  tagMode: ResourceTagMode
  tags: string
  timeout: string
  wait: boolean
}

type AdditionalResourceOptionsProps = {
  disabled: boolean
  isRemote: boolean
  onChange: (value: AdditionalResourceOptionsValue) => void
  t: TFunction<'addResource'>
  tagsValid: boolean
  value: AdditionalResourceOptionsValue
  tosMode?: boolean
}

export function AdditionalResourceOptions({
  disabled,
  isRemote,
  onChange,
  t,
  tagsValid,
  value,
  tosMode = false,
}: AdditionalResourceOptionsProps) {
  return (
    <div className="space-y-4 border-t border-border/50 pt-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label>{t('parseMode')}</Label>
          <Select
            value={value.parseMode}
            onValueChange={(parseMode) =>
              onChange({
                ...value,
                parseMode: parseMode === 'no_split' ? 'no_split' : 'default',
              })
            }
            disabled={disabled || tosMode}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="default">{t('parseMode.default')}</SelectItem>
              <SelectItem value="no_split">{t('parseMode.noSplit')}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-2">
          <Label>{t('processingMode')}</Label>
          <Select
            value={value.processingMode}
            onValueChange={(processingMode) =>
              onChange({
                ...value,
                processingMode:
                  processingMode === 'vectors_only'
                    ? 'vectors_only'
                    : 'semantic_and_vectors',
              })
            }
            disabled={disabled || tosMode}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="semantic_and_vectors">
                {t('processingMode.semantic')}
              </SelectItem>
              <SelectItem value="vectors_only">
                {t('processingMode.vectorsOnly')}
              </SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="flex flex-wrap gap-x-5 gap-y-3">
        <Label className="flex items-center gap-2">
          <Checkbox
            checked={value.preserveStructure}
            disabled={disabled || tosMode}
            onCheckedChange={(checked) =>
              onChange({ ...value, preserveStructure: Boolean(checked) })
            }
          />
          {t('preserveStructure')}
        </Label>
        <Label className="flex items-center gap-2">
          <Checkbox
            checked={value.wait}
            disabled={disabled || tosMode}
            onCheckedChange={(checked) =>
              onChange({ ...value, wait: Boolean(checked) })
            }
          />
          {t('waitForProcessing')}
        </Label>
      </div>

      {value.wait && !tosMode ? (
        <div className="grid gap-2 sm:max-w-xs">
          <Label htmlFor="add-resource-timeout">{t('timeout')}</Label>
          <Input
            id="add-resource-timeout"
            type="number"
            min="0.1"
            step="0.1"
            value={value.timeout}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...value, timeout: event.target.value })
            }
            placeholder={t('timeout.placeholder')}
          />
        </div>
      ) : null}

      {isRemote && !tosMode ? (
        <div className="grid gap-2">
          <Label htmlFor="add-resource-source-name">{t('sourceName')}</Label>
          <Input
            id="add-resource-source-name"
            value={value.sourceName}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...value, sourceName: event.target.value })
            }
            placeholder={t('sourceName.placeholder')}
          />
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-[1fr_10rem]">
        <div className="grid gap-2">
          <Label htmlFor="add-resource-tags">{t('tags')}</Label>
          <Input
            id="add-resource-tags"
            aria-invalid={!tagsValid}
            value={value.tags}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...value, tags: event.target.value })
            }
            placeholder={t('tags.placeholder')}
          />
          {!tagsValid ? (
            <p className="text-xs text-destructive" role="alert">
              {t('tags.invalid')}
            </p>
          ) : null}
        </div>
        <div className="grid gap-2">
          <Label>{t('tagMode')}</Label>
          <Select
            value={value.tagMode}
            onValueChange={(tagMode) =>
              onChange({
                ...value,
                tagMode: tagMode === 'append' ? 'append' : 'replace',
              })
            }
            disabled={disabled}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="replace">{t('tagMode.replace')}</SelectItem>
              <SelectItem value="append">{t('tagMode.append')}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
    </div>
  )
}
