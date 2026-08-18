import type { TFunction } from 'i18next'

import { Checkbox } from '#/components/ui/checkbox'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { RadioGroup } from '#/components/ui/radio-group'
import { Textarea } from '#/components/ui/textarea'
import { ResourceOptionRadioCard } from './resource-option-radio-card'

export type WebImportMode = 'auto' | 'single' | 'recursive' | 'site'

const WEB_IMPORT_MODES: WebImportMode[] = [
  'auto',
  'single',
  'recursive',
  'site',
]

export type WebResourceOptionsValue = {
  allowExternalLinks: boolean
  depth: string
  excludePaths: string
  includePaths: string
  maxPages: string
  mode: WebImportMode
  skipDownloadLinks: boolean
}

type WebResourceOptionsProps = {
  disabled: boolean
  onChange: (value: WebResourceOptionsValue) => void
  t: TFunction<'addResource'>
  value: WebResourceOptionsValue
}

export function WebResourceOptions({
  disabled,
  onChange,
  t,
  value,
}: WebResourceOptionsProps) {
  const showCrawlLimits = ['recursive', 'site'].includes(value.mode)

  return (
    <div className="space-y-4 rounded-lg border border-border/60 bg-muted/10 p-4">
      <div className="space-y-2">
        <Label>{t('web.mode.title')}</Label>
        <RadioGroup
          value={value.mode}
          onValueChange={(mode) =>
            onChange({ ...value, mode: mode as WebImportMode })
          }
          disabled={disabled}
          className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4"
        >
          {WEB_IMPORT_MODES.map((mode) => (
            <ResourceOptionRadioCard
              key={mode}
              description={t(`web.mode.${mode}Hint`)}
              title={t(`web.mode.${mode}`)}
              value={mode}
            />
          ))}
        </RadioGroup>
      </div>

      {showCrawlLimits ? (
        <div className="grid gap-3 border-t border-border/50 pt-3 sm:grid-cols-2">
          {value.mode === 'recursive' ? (
            <div className="grid gap-2">
              <Label htmlFor="add-resource-web-depth">{t('web.depth')}</Label>
              <Input
                id="add-resource-web-depth"
                type="number"
                min="0"
                step="1"
                value={value.depth}
                disabled={disabled}
                onChange={(event) =>
                  onChange({ ...value, depth: event.target.value })
                }
              />
            </div>
          ) : null}
          <div className="grid gap-2">
            <Label htmlFor="add-resource-web-max-pages">
              {t('web.maxPages')}
            </Label>
            <Input
              id="add-resource-web-max-pages"
              type="number"
              min="1"
              step="1"
              value={value.maxPages}
              disabled={disabled}
              onChange={(event) =>
                onChange({ ...value, maxPages: event.target.value })
              }
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="add-resource-web-include-paths">
              {t('web.includePaths')}
            </Label>
            <Textarea
              id="add-resource-web-include-paths"
              rows={2}
              value={value.includePaths}
              disabled={disabled}
              onChange={(event) =>
                onChange({ ...value, includePaths: event.target.value })
              }
              placeholder={t('web.includePaths.placeholder')}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="add-resource-web-exclude-paths">
              {t('web.excludePaths')}
            </Label>
            <Textarea
              id="add-resource-web-exclude-paths"
              rows={2}
              value={value.excludePaths}
              disabled={disabled}
              onChange={(event) =>
                onChange({ ...value, excludePaths: event.target.value })
              }
              placeholder={t('web.excludePaths.placeholder')}
            />
          </div>
          {value.mode === 'recursive' ? (
            <div className="flex flex-wrap gap-x-5 gap-y-3 sm:col-span-2">
              <Label className="flex items-center gap-2">
                <Checkbox
                  checked={value.allowExternalLinks}
                  disabled={disabled}
                  onCheckedChange={(checked) =>
                    onChange({ ...value, allowExternalLinks: Boolean(checked) })
                  }
                />
                {t('web.allowExternalLinks')}
              </Label>
              <Label className="flex items-center gap-2">
                <Checkbox
                  checked={value.skipDownloadLinks}
                  disabled={disabled}
                  onCheckedChange={(checked) =>
                    onChange({ ...value, skipDownloadLinks: Boolean(checked) })
                  }
                />
                {t('web.skipDownloadLinks')}
              </Label>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
