import type * as React from 'react'
import { Link } from '@tanstack/react-router'
import { ArrowLeftIcon } from 'lucide-react'

import { Button } from '#/components/ui/button'

type SectionHeaderProps = {
  /** `h2` title; tab pages leave it out because the active tab names them. */
  title?: React.ReactNode
  description?: React.ReactNode
  /** Buttons on the right (Refresh, primary action…). */
  actions?: React.ReactNode
  /** Rendered above the title, e.g. a `BackLink` on editor pages. */
  back?: React.ReactNode
}

/**
 * Page header inside the gateway layout. Editors show an `h2` title with a
 * description beneath; tab pages show a one-line description beside the actions.
 */
export function SectionHeader({
  title,
  description,
  actions,
  back,
}: SectionHeaderProps) {
  const actionSlot = actions ? (
    <div className="flex flex-wrap items-center gap-2">{actions}</div>
  ) : null

  if (!title) {
    return (
      <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        {description ? (
          <p className="min-w-0 flex-1 basis-80 text-sm leading-6 text-muted-foreground">
            {description}
          </p>
        ) : null}
        {actionSlot}
      </header>
    )
  }

  return (
    <header className="flex flex-col gap-2">
      {back}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid min-w-0 gap-1">
          <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
          {description ? (
            <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
              {description}
            </p>
          ) : null}
        </div>
        {actionSlot}
      </div>
    </header>
  )
}

/** List page an editor returns to. */
export type EditorListPath =
  | '/context-gateway/upstreams'
  | '/context-gateway/profiles'

/** Small "← Upstreams" link above an editor's title. */
export function BackLink({ to, label }: { to: EditorListPath; label: string }) {
  return (
    <Button
      size="xs"
      variant="ghost"
      nativeButton={false}
      render={<Link to={to} />}
      className="-ml-2 w-fit text-muted-foreground"
    >
      <ArrowLeftIcon />
      {label}
    </Button>
  )
}
