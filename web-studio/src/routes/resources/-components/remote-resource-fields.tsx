import type { ReactNode } from 'react'
import type { TFunction } from 'i18next'

import { Badge } from '#/components/ui/badge'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { Switch } from '#/components/ui/switch'
import type {
  RemoteResourceKind,
  RemoteResourceTypeSelection,
} from '../-lib/resource-source'
import { getRemoteResourceDescriptor } from './remote-resource-descriptors'
import { RemoteResourceTypePicker } from './remote-resource-type-picker'

type RemoteResourceFieldsProps = {
  children?: ReactNode
  disabled: boolean
  onUrlChange: (value: string) => void
  onResourceTypeChange: (value: RemoteResourceTypeSelection) => void
  onWatchEnabledChange: (enabled: boolean) => void
  onWatchIntervalChange: (value: string) => void
  resourceKind: RemoteResourceKind
  resourceType: RemoteResourceTypeSelection
  t: TFunction<'addResource'>
  url: string
  watchEnabled: boolean
  watchInterval: string
  watchRequired?: boolean
  watchSupported?: boolean
}

function getRemoteUrlPlaceholder(
  resourceKind: RemoteResourceKind,
  t: TFunction<'addResource'>,
): string {
  const descriptor = getRemoteResourceDescriptor(resourceKind)
  return descriptor ? t(descriptor.exampleKey) : t('remoteUrl.placeholder')
}

export function RemoteResourceFields({
  children,
  disabled,
  onUrlChange,
  onResourceTypeChange,
  onWatchEnabledChange,
  onWatchIntervalChange,
  resourceKind,
  resourceType,
  t,
  url,
  watchEnabled,
  watchInterval,
  watchRequired = false,
  watchSupported = true,
}: RemoteResourceFieldsProps) {
  const remoteUrlPlaceholder = getRemoteUrlPlaceholder(resourceKind, t)

  return (
    <div className="space-y-4">
      <RemoteResourceTypePicker
        disabled={disabled}
        onChange={onResourceTypeChange}
        t={t}
        value={resourceType}
        watchRequired={watchRequired}
      />

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="add-resource-remote-url">{t('remoteUrl')}</Label>
          {resourceKind !== 'unknown' ? (
            <Badge variant="secondary">{t(`sourceKind.${resourceKind}`)}</Badge>
          ) : null}
        </div>
        <Input
          id="add-resource-remote-url"
          placeholder={remoteUrlPlaceholder}
          value={url}
          onChange={(event) => onUrlChange(event.target.value)}
          disabled={disabled || (watchRequired && !watchSupported)}
        />
        <p className="text-xs text-muted-foreground">
          {resourceKind === 'unknown'
            ? t('remoteUrl.hint')
            : t(`sourceKind.${resourceKind}Hint`)}
        </p>
      </div>

      {children}

      <div className="rounded-lg border border-border/60 bg-muted/10 p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="grid gap-1">
            <Label htmlFor="add-resource-watch-enabled">
              {t('watch.enabled')}
            </Label>
            <p className="text-xs leading-5 text-muted-foreground">
              {watchSupported ? t('watch.hint') : t('watch.unsupported')}
            </p>
          </div>
          <Switch
            id="add-resource-watch-enabled"
            checked={watchSupported && watchEnabled}
            disabled={disabled || !watchSupported || watchRequired}
            onCheckedChange={onWatchEnabledChange}
          />
        </div>
        {watchRequired && !watchSupported ? (
          <p className="mt-3 text-xs text-destructive" role="alert">
            {t('watch.requiredUnsupported')}
          </p>
        ) : null}
        {watchEnabled && watchSupported ? (
          <div className="mt-4 grid gap-2 border-t border-border/50 pt-4">
            <Label htmlFor="add-resource-watch-interval">
              {t('watch.interval')}
            </Label>
            <Input
              id="add-resource-watch-interval"
              type="number"
              min="1"
              step="1"
              value={watchInterval}
              disabled={disabled}
              onChange={(event) => onWatchIntervalChange(event.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {t('watch.intervalHint')}
            </p>
          </div>
        ) : null}
      </div>
    </div>
  )
}
