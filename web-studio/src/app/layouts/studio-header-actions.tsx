import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'

/** Route actions keep their own data context while rendering in the top bar. */
export const StudioHeaderActionsContext = createContext<HTMLDivElement | null>(
  null,
)

export function StudioHeaderActions({ children }: { children: ReactNode }) {
  const container = useContext(StudioHeaderActionsContext)
  return container ? createPortal(children, container) : null
}
