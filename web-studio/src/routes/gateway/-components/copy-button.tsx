import * as React from 'react'
import { CheckIcon, CopyIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '#/components/ui/tooltip'
import { copyTextToClipboard } from '#/lib/clipboard'

type CopyButtonProps = {
  value: string
  /** Tooltip and accessible name, e.g. "Copy gateway address". */
  label?: string
  size?: 'icon-xs' | 'icon-sm'
  className?: string
}

/** Icon button that copies `value`, with a tooltip and a toast. */
export function CopyButton({
  value,
  label,
  size = 'icon-xs',
  className,
}: CopyButtonProps) {
  const { t } = useTranslation('contextGateway')
  const [copied, setCopied] = React.useState(false)
  const name = label ?? t('actions.copy')

  React.useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(timer)
  }, [copied])

  async function copy() {
    try {
      await copyTextToClipboard(value)
      setCopied(true)
      toast.success(t('copy.done'))
    } catch {
      toast.error(t('copy.failed'))
    }
  }

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size={size}
            aria-label={name}
            className={className}
            onClick={() => void copy()}
          />
        }
      >
        {copied ? <CheckIcon /> : <CopyIcon />}
      </TooltipTrigger>
      <TooltipContent>{name}</TooltipContent>
    </Tooltip>
  )
}
