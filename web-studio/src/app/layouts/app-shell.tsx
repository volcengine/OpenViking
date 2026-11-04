import type { ReactNode } from 'react'
import { useRouterState } from '@tanstack/react-router'

/** Shared visual shell; routes own connection requirements and theme scope. */
export function AppShell({ children }: { children: ReactNode }) {
  const theme = useRouterState({
    select: (state) =>
      [...state.matches].reverse().find((match) => match.staticData.studioTheme)
        ?.staticData.studioTheme,
  })
  return (
    <div className="h-full" data-studio-ux={theme}>
      {children}
    </div>
  )
}
