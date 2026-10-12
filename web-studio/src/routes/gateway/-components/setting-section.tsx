import * as React from 'react'
import { ChevronRightIcon, CircleAlertIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '#/components/ui/collapsible'
import { Switch } from '#/components/ui/switch'

type SettingSectionProps = {
  /** Anchor id for in-page navigation. */
  id?: string
  icon?: React.ReactNode
  title: string
  description?: React.ReactNode
  /** State of the header switch; omit for a section without one. */
  checked?: boolean
  onCheckedChange?: (checked: boolean) => void
  switchDisabled?: boolean
  /** Shown under the header, e.g. why the switch is disabled. */
  note?: React.ReactNode
  /** Settings inside a collapsed "Advanced settings" disclosure. */
  advanced?: React.ReactNode
  /**
   * A setting in this section is invalid: the header says so, and the
   * settings stay visible even while the switch is off.
   */
  invalid?: boolean
  /** An advanced setting is invalid, so "Advanced settings" stays open. */
  advancedInvalid?: boolean
  /** Settings shown while the section is on. */
  children?: React.ReactNode
}

/**
 * Card for one group of settings. With a header switch, the settings are
 * hidden while it is off, unless one of them needs fixing; rarely changed
 * settings go in `advanced`.
 */
export function SettingSection({
  id,
  icon,
  title,
  description,
  checked,
  onCheckedChange,
  switchDisabled,
  note,
  advanced,
  invalid = false,
  advancedInvalid = false,
  children,
}: SettingSectionProps) {
  const { t } = useTranslation('gateway')
  const [advancedOpen, setAdvancedOpen] = React.useState(false)
  // Save checks every setting, so one that needs fixing must stay visible.
  const open = checked !== false || invalid
  const hasBody = open && Boolean(children || advanced)
  return (
    <Card id={id} className="scroll-mt-20 gap-0 py-0">
      <CardHeader className="gap-1 py-5">
        <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-muted-foreground">
          {icon}
          {title}
        </CardTitle>
        {description ? (
          <CardDescription className="max-w-2xl leading-6">
            {description}
          </CardDescription>
        ) : null}
        {checked !== undefined ? (
          <CardAction>
            <Switch
              checked={checked}
              disabled={switchDisabled}
              aria-label={title}
              onCheckedChange={(value) => onCheckedChange?.(value)}
            />
          </CardAction>
        ) : null}
        {note ? <div className="col-span-full pt-2 text-sm">{note}</div> : null}
        {invalid ? (
          <p className="col-span-full flex items-start gap-2 pt-2 text-sm text-destructive">
            <CircleAlertIcon className="mt-0.5 size-4 shrink-0" />
            {t('field.sectionInvalid')}
          </p>
        ) : null}
      </CardHeader>
      {hasBody ? (
        <CardContent className="grid gap-5 border-t py-5">
          {children}
          {advanced ? (
            <Collapsible
              className="grid gap-5"
              open={advancedOpen || advancedInvalid}
              onOpenChange={setAdvancedOpen}
            >
              <CollapsibleTrigger className="group/advanced flex w-fit items-center gap-1.5 rounded-sm text-sm font-medium text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                <ChevronRightIcon className="size-4 transition-transform group-data-[panel-open]/advanced:rotate-90" />
                {t('field.advanced')}
              </CollapsibleTrigger>
              <CollapsibleContent className="grid gap-5">
                {advanced}
              </CollapsibleContent>
            </Collapsible>
          ) : null}
        </CardContent>
      ) : null}
    </Card>
  )
}
