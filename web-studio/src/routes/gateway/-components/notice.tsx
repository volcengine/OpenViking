import type * as React from 'react'
import { CircleAlertIcon, InfoIcon, TriangleAlertIcon } from 'lucide-react'

import { cn } from '#/lib/utils'

const TONES = {
  info: {
    icon: InfoIcon,
    className: 'border-sky-500/25 bg-sky-500/10 text-sky-800 dark:text-sky-200',
  },
  warning: {
    icon: TriangleAlertIcon,
    className:
      'border-amber-500/25 bg-amber-500/10 text-amber-800 dark:text-amber-200',
  },
  danger: {
    icon: CircleAlertIcon,
    className: 'border-destructive/25 bg-destructive/5 text-destructive',
  },
}

type NoticeProps = {
  /** Sky for information, amber for something to fix, red for a failure. */
  tone: keyof typeof TONES
  title?: React.ReactNode
  /** Button or link under the text. */
  action?: React.ReactNode
  className?: string
  children?: React.ReactNode
}

/** Tinted box with an icon for a note, a setup gap or a problem worth reading. */
export function Notice({
  tone,
  title,
  action,
  className,
  children,
}: NoticeProps) {
  const { icon: Icon, className: toneClass } = TONES[tone]
  return (
    <div
      className={cn(
        'flex gap-2.5 rounded-lg border px-3 py-2.5 text-sm leading-6',
        toneClass,
        className,
      )}
    >
      <Icon className="mt-1 size-4 shrink-0" />
      <div className="grid min-w-0 flex-1 gap-1">
        {title ? <p className="font-medium">{title}</p> : null}
        {children}
        {action ? <div className="mt-1">{action}</div> : null}
      </div>
    </div>
  )
}
