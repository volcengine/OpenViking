import type * as React from 'react'
import { Link } from '@tanstack/react-router'
import { LoaderCircleIcon, SaveIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { cn } from '#/lib/utils'

import type { EditorListPath } from './section-header'

type EditorFooterProps = {
  /** Why Save is disabled, or that there are unsaved changes. */
  status?: React.ReactNode
  /** Shows the status as a problem to fix. */
  invalid?: boolean
  /** Where Cancel goes. */
  cancelTo: EditorListPath
  /** Label of the submit button while idle. */
  saveLabel: string
  saving: boolean
  disabled: boolean
}

/** Sticky bar at the bottom of an editor form: status, Cancel and Save (submit). */
export function EditorFooter({
  status,
  invalid = false,
  cancelTo,
  saveLabel,
  saving,
  disabled,
}: EditorFooterProps) {
  const { t } = useTranslation('contextGateway')
  return (
    <div className="sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center justify-end gap-3 border-t bg-background/95 px-4 py-3 backdrop-blur md:-mx-6 md:px-6">
      {status ? (
        <p
          role="status"
          className={cn(
            'mr-auto text-sm',
            invalid ? 'text-destructive' : 'text-muted-foreground',
          )}
        >
          {status}
        </p>
      ) : null}
      <Button
        variant="outline"
        nativeButton={false}
        render={<Link to={cancelTo} />}
      >
        {t('actions.cancel')}
      </Button>
      <Button type="submit" disabled={disabled}>
        {saving ? <LoaderCircleIcon className="animate-spin" /> : <SaveIcon />}
        {saving ? t('states.saving') : saveLabel}
      </Button>
    </div>
  )
}
