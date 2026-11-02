import type { ReactNode } from 'react'
import { useRouterState } from '@tanstack/react-router'
import { ConnectedStudioLayout } from '#/app/layouts/connected-studio-layout'

declare module '@tanstack/react-router' {
  interface StaticDataRouteOption {
    studioPublic?: boolean
    studioTheme?: 'workbench'
  }
}

export function StudioRouteLayout({ children }: { children: ReactNode }) {
  const isPublic = useRouterState({
    select: (state) =>
      state.matches.some((match) => match.staticData.studioPublic),
  })
  return isPublic ? (
    children
  ) : (
    <ConnectedStudioLayout>{children}</ConnectedStudioLayout>
  )
}
