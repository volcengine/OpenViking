import type * as React from 'react'

import { Card, CardContent } from '#/components/ui/card'

type MetricCardProps = {
  icon: React.ReactNode
  label: string
  value: React.ReactNode
}

export function MetricCard({ icon, label, value }: MetricCardProps) {
  return (
    <Card className="bg-card/70">
      <CardContent className="flex items-center justify-between gap-4 p-4">
        <div>
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
        </div>
        <div className="flex size-9 items-center justify-center rounded-md border bg-background/70 text-muted-foreground">
          {icon}
        </div>
      </CardContent>
    </Card>
  )
}
