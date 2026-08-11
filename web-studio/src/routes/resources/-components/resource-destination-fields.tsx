import { FolderOpen } from 'lucide-react'
import type { TFunction } from 'i18next'

import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { RadioGroup, RadioGroupItem } from '#/components/ui/radio-group'

export type ResourceDestinationMode = 'parent' | 'to'

type ResourceDestinationFieldsProps = {
  disabled: boolean
  mode: ResourceDestinationMode
  onBrowse: () => void
  onModeChange: (mode: ResourceDestinationMode) => void
  onUriChange: (uri: string) => void
  t: TFunction<'addResource'>
  uri: string
  exactOnly?: boolean
}

export function ResourceDestinationFields({
  disabled,
  mode,
  onBrowse,
  onModeChange,
  onUriChange,
  t,
  uri,
  exactOnly = false,
}: ResourceDestinationFieldsProps) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label htmlFor="add-resource-target">{t('targetUri')}</Label>
        <RadioGroup
          value={mode}
          onValueChange={(value) =>
            onModeChange(value === 'to' ? 'to' : 'parent')
          }
          disabled={disabled}
          className="flex gap-3"
        >
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
            <RadioGroupItem value="parent" disabled={exactOnly} />
            {t('destination.parent')}
          </label>
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
            <RadioGroupItem value="to" />
            {t('destination.exact')}
          </label>
        </RadioGroup>
      </div>
      <div className="flex gap-2">
        <Input
          id="add-resource-target"
          placeholder={t('targetUri.placeholder')}
          value={uri}
          disabled={disabled}
          onChange={(event) => onUriChange(event.target.value)}
          className="flex-1"
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0"
          disabled={disabled}
          onClick={onBrowse}
        >
          <FolderOpen className="mr-1.5 size-4" />
          {t('targetUri.browse')}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        {exactOnly
          ? t('destination.tosHint')
          : mode === 'parent'
            ? t('destination.parentHint')
            : t('destination.exactHint')}
      </p>
    </div>
  )
}
