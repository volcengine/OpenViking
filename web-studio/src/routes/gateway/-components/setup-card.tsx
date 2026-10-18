import type * as React from 'react'
import { ServerOffIcon } from 'lucide-react'

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'

/** Full-page card that explains why the gateway can't be shown and what to do. */
export function SetupCard({
  title,
  description,
  children,
  actions,
}: {
  title: string
  description: string
  children?: React.ReactNode
  actions?: React.ReactNode
}) {
  return (
    <Card className="mx-auto mt-6 w-full max-w-2xl">
      <CardHeader className="gap-3">
        <ServerOffIcon className="size-6 text-muted-foreground" />
        <CardTitle>{title}</CardTitle>
        <CardDescription className="leading-6">{description}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {children}
        <div className="flex flex-wrap gap-2">{actions}</div>
      </CardContent>
    </Card>
  )
}
