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
        'animate-home-panel-in rounded-2xl border border-border/70 bg-muted/80 p-6 shadow-sm transition-[background-color,border-color,box-shadow,transform] duration-200 ease-out hover:-translate-y-0.5 hover:border-border hover:bg-muted hover:shadow-md dark:border-white/10 dark:bg-white/[0.12] dark:hover:border-white/15 dark:hover:bg-white/[0.16]',
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
    <div className="flex min-h-8 items-center justify-between gap-2 rounded-lg border border-[oklch(0.68_0.12_232/0.1)] bg-background/55 px-2.5 py-1.5 text-xs shadow-xs dark:border-white/10 dark:bg-white/[0.05]">
      <span className="min-w-0 truncate text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums text-[oklch(0.46_0.13_242)] dark:text-[oklch(0.74_0.12_232)]">
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
