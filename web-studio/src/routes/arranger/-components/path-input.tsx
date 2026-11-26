import { Combobox } from '@base-ui/react/combobox'
import { ChevronDownIcon } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/shared/ui/button'
import { Input } from '#/shared/ui/input'

export function PathInput({
  history,
  onChange,
  onSubmit,
  value,
}: {
  history: string[]
  onChange: (value: string) => void
  onSubmit: () => void
  value: string
}) {
  const { t } = useTranslation('arranger')
  const [open, setOpen] = useState(false)
  return (
    <form
      className="flex min-w-0 flex-1 gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit()
      }}
    >
      <Combobox.Root
        items={history}
        filter={null}
        inputValue={value}
        value={value || null}
        open={open && history.length > 0}
        onOpenChange={setOpen}
        onInputValueChange={onChange}
        onValueChange={(uri) => {
          if (uri) onChange(uri)
        }}
      >
        <div className="relative min-w-0 flex-1">
          <Combobox.Input
            render={<Input />}
            aria-label={t('pathLabel')}
            placeholder={t('start.pathPlaceholder')}
            className="pr-9 font-mono text-xs"
          />
          <Combobox.Trigger
            type="button"
            disabled={!history.length}
            aria-label={t('start.pathHistory')}
            className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-muted-foreground disabled:opacity-30"
          >
            <ChevronDownIcon className="size-4" />
          </Combobox.Trigger>
        </div>
        <Combobox.Portal>
          <Combobox.Positioner sideOffset={4} align="start" className="z-50">
            <Combobox.Popup className="max-h-64 w-[var(--anchor-width)] min-w-64 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md">
              <Combobox.List>
                {(uri: string) => (
                  <Combobox.Item
                    key={uri}
                    value={uri}
                    className="cursor-pointer break-all rounded-sm px-2 py-2 font-mono text-xs data-highlighted:bg-accent data-highlighted:text-accent-foreground"
                  >
                    {uri}
                  </Combobox.Item>
                )}
              </Combobox.List>
            </Combobox.Popup>
          </Combobox.Positioner>
        </Combobox.Portal>
      </Combobox.Root>
      <Button type="submit" size="sm" disabled={!value.trim()}>
        {t('actions.load')}
      </Button>
    </form>
  )
}
