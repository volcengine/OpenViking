import { Outlet, createRootRoute, useLocation } from '@tanstack/react-router'
import { TanStackRouterDevtoolsPanel } from '@tanstack/react-router-devtools'
import { TanStackDevtools } from '@tanstack/react-devtools'
import { PanelLeftIcon } from 'lucide-react'

import { AppSidebar } from '#/components/app-sidebar'
import { Button } from '#/components/ui/button'
import { SidebarInset, SidebarProvider, useSidebar } from '#/components/ui/sidebar'
import { routeItems } from '#/lib/legacy/routes'

import '../styles.css'

export const Route = createRootRoute({
  component: RootComponent,
})

function AppHeader() {
  const { toggleSidebar } = useSidebar()
  const location = useLocation()
  const currentLabel =
    routeItems.find((item) => location.pathname.startsWith(item.to))?.label ?? ''

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b px-4">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" className="size-8" onClick={toggleSidebar}>
          <PanelLeftIcon className="size-4" />
        </Button>
        <span className="text-base font-semibold tracking-tight">OpenViking Console</span>
        {currentLabel ? (
          <span className="text-sm text-muted-foreground">/ {currentLabel}</span>
        ) : null}
      </div>
    </header>
  )
}

function RootComponent() {
  return (
    <>
      <SidebarProvider>
        <div className="flex h-screen w-full flex-col">
          <AppHeader />
          <div className="relative flex flex-1 overflow-hidden [--header-height:3.5rem]">
            <AppSidebar />
            <SidebarInset>
              <div className="flex flex-1 flex-col gap-4 overflow-auto p-4">
                <Outlet />
              </div>
            </SidebarInset>
          </div>
        </div>
      </SidebarProvider>
      <TanStackDevtools
        config={{
          position: 'bottom-right',
        }}
        plugins={[
          {
            name: 'TanStack Router',
            render: <TanStackRouterDevtoolsPanel />,
          },
        ]}
      />
    </>
  )
}
