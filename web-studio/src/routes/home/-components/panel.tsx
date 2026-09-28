import type { ReactNode } from 'react'

import { cn } from '#/lib/utils'

export function Panel({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={cn(
        'animate-home-panel-in rounded-2xl border border-border/70 bg-card p-6 dark:border-[#363638] dark:bg-[#262628]',
        className,
      )}
    >
      {children}
    </section>
  )
}

export function SectionHeading({
  action,
  description,
  title,
}: {
  action?: ReactNode
  description?: string
  title: string
}) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h2 className="text-lg font-semibold tracking-normal">{title}</h2>
        {description ? (
          <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {action}
    </div>
  )
}

export function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-h-8 flex-wrap items-center gap-x-2 py-1.5 text-xs">
      <span className="min-w-0 grow text-muted-foreground">{label}</span>
      <span className="ml-auto font-medium tabular-nums text-foreground">
        {value}
      </span>
    </div>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-24 items-center justify-center rounded-xl border border-dashed border-border/60 bg-background/45 px-4 text-center text-sm text-muted-foreground dark:bg-white/[0.04]">
      {children}
    </div>
  )
}
