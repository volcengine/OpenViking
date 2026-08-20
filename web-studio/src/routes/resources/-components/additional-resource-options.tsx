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
} from '../-lib/resource-import-types'

export type ResourceParseMode = 'default' | 'no_split'

export type AdditionalResourceOptionsValue = {
  parseMode: ResourceParseMode
  preserveStructure?: boolean
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
  value: AdditionalResourceOptionsValue
  nativeOptions?: boolean
}

export function AdditionalResourceOptions({
  disabled,
  isRemote,
  onChange,
  t,
  value,
  nativeOptions = true,
}: AdditionalResourceOptionsProps) {
  const preserveStructureMode =
    value.preserveStructure === undefined
      ? 'server_default'
      : value.preserveStructure
        ? 'preserve'
        : 'flatten'

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
            disabled={disabled || !nativeOptions}
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
            disabled={disabled || !nativeOptions}
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
        <div className="grid min-w-48 gap-2">
          <Label htmlFor="add-resource-preserve-structure">
            {t('preserveStructure')}
          </Label>
          <Select
            value={preserveStructureMode}
            onValueChange={(mode) =>
              onChange({
                ...value,
                preserveStructure:
                  mode === 'server_default' ? undefined : mode === 'preserve',
              })
            }
            disabled={disabled || !nativeOptions}
          >
            <SelectTrigger
              id="add-resource-preserve-structure"
              className="w-full"
            >
              <SelectValue>
                {t(`preserveStructure.${preserveStructureMode}`)}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="server_default">
                {t('preserveStructure.serverDefault')}
              </SelectItem>
              <SelectItem value="preserve">
                {t('preserveStructure.preserve')}
              </SelectItem>
              <SelectItem value="flatten">
                {t('preserveStructure.flatten')}
              </SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Label className="flex items-center gap-2">
          <Checkbox
            checked={value.wait}
            disabled={disabled || !nativeOptions}
            onCheckedChange={(checked) =>
              onChange({ ...value, wait: Boolean(checked) })
            }
          />
          {t('waitForProcessing')}
        </Label>
      </div>

      {value.wait && nativeOptions ? (
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

      {isRemote && nativeOptions ? (
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
            value={value.tags}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...value, tags: event.target.value })
            }
            placeholder={t('tags.placeholder')}
          />
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
