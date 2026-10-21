import type * as React from 'react'

import { Button } from '#/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '#/components/ui/tooltip'

type ExplainedButtonProps = React.ComponentProps<typeof Button> & {
  /**
   * Tooltip text: what the button does, or why it is disabled. Without one
   * the button renders plain.
   */
  explanation?: string
}

/**
 * A button with a tooltip that also shows while it is disabled. Disabled
 * buttons don't emit hover events, so a span carries the tooltip and a
 * native `title` then.
 */
export function ExplainedButton({
  explanation,
  disabled,
  children,
  ...props
}: ExplainedButtonProps) {
  if (!explanation) {
    return (
      <Button {...props} disabled={disabled}>
        {children}
      </Button>
    )
  }
  if (disabled) {
    return (
      <Tooltip>
        <TooltipTrigger
          render={<span className="inline-flex" title={explanation} />}
        >
          <Button {...props} disabled>
            {children}
          </Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-72">{explanation}</TooltipContent>
      </Tooltip>
    )
  }
  return (
    <Tooltip>
      <TooltipTrigger render={<Button {...props} />}>{children}</TooltipTrigger>
      <TooltipContent className="max-w-72">{explanation}</TooltipContent>
    </Tooltip>
  )
}
