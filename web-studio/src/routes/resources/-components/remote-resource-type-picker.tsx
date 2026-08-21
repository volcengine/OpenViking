import type { TFunction } from 'i18next'

import { cn } from '#/lib/utils'
import { getRemoteResourceCapabilities } from '../-lib/resource-source-strategy'
import type { RemoteResourceTypeSelection } from '../-lib/resource-source'
import {
  getRemoteResourceDescriptor,
  VISIBLE_REMOTE_RESOURCE_DESCRIPTORS,
} from './remote-resource-descriptors'

type RemoteResourceTypePickerProps = {
  disabled: boolean
  onChange: (value: RemoteResourceTypeSelection) => void
  t: TFunction<'addResource'>
  value: RemoteResourceTypeSelection
  watchRequired?: boolean
}

export function RemoteResourceTypePicker({
  disabled,
  onChange,
  t,
  value,
  watchRequired = false,
}: RemoteResourceTypePickerProps) {
  const selectedDescriptor =
    value === 'auto' ? undefined : getRemoteResourceDescriptor(value)

  return (
    <div className="space-y-2.5 rounded-xl border border-border/60 bg-muted/10 p-3">
      <div className="flex min-w-0 items-baseline gap-2">
        <p className="shrink-0 text-sm font-medium">
          {t('sourcePicker.title')}
        </p>
        <p className="truncate text-xs text-muted-foreground">
          {t('sourcePicker.hint')}
        </p>
      </div>

      <div
        role="group"
        aria-label={t('sourcePicker.title')}
        className="grid grid-cols-2 gap-1.5 sm:grid-cols-4"
      >
        {VISIBLE_REMOTE_RESOURCE_DESCRIPTORS.map(({ type, icon: Icon }) => {
          const selected = value === type
          const optionUnavailable =
            watchRequired && !getRemoteResourceCapabilities(type).watch
          return (
            <button
              key={type}
              type="button"
              aria-pressed={selected}
              data-unavailable={optionUnavailable || undefined}
              title={optionUnavailable ? t('watch.unsupported') : undefined}
              className={cn(
                'group flex min-w-0 items-center justify-center gap-2 rounded-md border px-2.5 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                selected
                  ? 'border-primary/50 bg-primary/8 shadow-sm'
                  : 'border-border/50 bg-background/60 hover:border-border hover:bg-background',
              )}
              disabled={disabled}
              onClick={() => onChange(selected ? 'auto' : type)}
            >
              <span
                className={cn(
                  'flex size-6 shrink-0 items-center justify-center rounded border transition-colors',
                  selected
                    ? 'border-primary/30 bg-primary/10 text-primary'
                    : 'border-border/50 bg-muted/40 text-muted-foreground group-hover:text-foreground',
                )}
              >
                <Icon className="size-3.5" />
              </span>
              <span className="min-w-0 truncate text-xs font-medium">
                {t(`sourcePicker.${type}`)}
              </span>
            </button>
          )
        })}
      </div>

      {selectedDescriptor ? (
        <div className="flex min-h-7 min-w-0 items-center gap-2 rounded-md bg-muted/40 px-2.5 py-1.5 text-xs text-muted-foreground">
          <span className="min-w-0 flex-1 truncate">
            {t(`sourcePicker.${value}Hint`)}
          </span>
          <code className="hidden max-w-[42%] shrink-0 truncate text-[11px] text-muted-foreground/80 sm:block">
            {t(selectedDescriptor.exampleKey)}
          </code>
        </div>
      ) : null}
    </div>
  )
}
