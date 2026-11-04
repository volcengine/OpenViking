import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  useSyncExternalStore,
} from 'react'
import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useRouterState } from '@tanstack/react-router'
import type { AppConnectionContextValue } from '../lib/connection-contract'
import { ConnectionController } from '../lib/connection-controller'

const AppConnectionContext = createContext<AppConnectionContextValue | null>(
  null,
)

/** Internal/full configuration access. Business features use connection/access. */
export function useAppConnection(): AppConnectionContextValue {
  const context = useContext(AppConnectionContext)
  if (!context) {
    // eslint-disable-next-line studio-i18n/no-literal-message -- Developer invariant.
    throw new Error(
      'useAppConnection must be used within AppConnectionProvider.',
    )
  }
  return context
}

export function AppConnectionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const [controller] = useState(
    () =>
      new ConnectionController(pathname, {
        clearCache: () => queryClient.clear(),
        navigate: (destination) => navigate(destination),
      }),
  )
  controller.setNavigation((destination) => navigate(destination))
  const snapshot = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  )
  const changingSurface = controller.prepareRoute(pathname)
  useLayoutEffect(() => controller.setRoute(pathname), [controller, pathname])
  useEffect(() => controller.start(), [controller])
  return (
    <AppConnectionContext.Provider value={snapshot}>
      {changingSurface ? null : children}
    </AppConnectionContext.Provider>
  )
}
