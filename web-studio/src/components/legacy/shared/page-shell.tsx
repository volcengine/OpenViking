import type { ReactNode } from 'react'

import { Badge } from '#/components/ui/badge'

type LegacyPageShellProps = {
  children: ReactNode
  description: string
  title: string
}

export function LegacyPageShell({ children, description, title }: LegacyPageShellProps) {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <Badge variant="outline" className="w-fit">{`Legacy ${title}`}</Badge>
        <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{description}</p>
      </header>
      {children}
    </div>
  )
}
