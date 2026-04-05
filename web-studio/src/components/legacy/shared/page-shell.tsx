import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { legacyRouteItems, type LegacyRouteKey } from '#/lib/legacy/routes'

type LegacyPageShellProps = {
  children: ReactNode
  description: string
  section: LegacyRouteKey
  title: string
}

export function LegacyPageShell({
  children,
  description,
  section,
  title,
}: LegacyPageShellProps) {
  return (
    <main className="min-h-screen bg-gradient-to-b from-background via-background to-muted/30 px-4 py-8 sm:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6">
        <header className="flex flex-col gap-4 rounded-3xl border border-border/70 bg-background/90 p-6 shadow-sm backdrop-blur">
          <Badge variant="outline" className="w-fit">{`Legacy ${title}`}</Badge>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
            <div className="space-y-2">
              <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
              <p className="max-w-3xl text-sm text-muted-foreground">{description}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {legacyRouteItems.map((item) => (
                <Button
                  key={item.key}
                  nativeButton={false}
                  render={<Link to={item.to} />}
                  size="sm"
                  variant={item.key === section ? 'default' : 'outline'}
                >
                  {item.label}
                </Button>
              ))}
            </div>
          </div>
        </header>

        {children}
      </div>
    </main>
  )
}
