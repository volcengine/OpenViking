import { Outlet, createRootRoute, useLocation } from '@tanstack/react-router'
import { TanStackRouterDevtoolsPanel } from '@tanstack/react-router-devtools'
import { TanStackDevtools } from '@tanstack/react-devtools'
import { GlobeIcon, MoonIcon, PanelLeftIcon, SunIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from 'next-themes'

import { AppSidebar } from '#/components/app-sidebar'
import { Button } from '#/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '#/components/ui/dropdown-menu'
import { SidebarInset, SidebarProvider, useSidebar } from '#/components/ui/sidebar'
import { routeItems } from '#/lib/legacy/routes'

import '../styles.css'

export const Route = createRootRoute({
  component: RootComponent,
})

const languages = [
  { code: 'zh-CN', label: '中文' },
  { code: 'en', label: 'English' },
] as const

function AppHeader() {
  const { toggleSidebar } = useSidebar()
  const { t, i18n } = useTranslation()
  const { setTheme, resolvedTheme } = useTheme()
  const location = useLocation()
  const currentRoute = routeItems.find((item) => location.pathname.startsWith(item.to))
  const currentLabel = currentRoute ? t(`sidebar.${currentRoute.key}`) : ''

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b px-4">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" className="size-8" onClick={toggleSidebar}>
          <PanelLeftIcon className="size-4" />
        </Button>
        <span className="text-base font-semibold tracking-tight">{t('app.title')}</span>
        {currentLabel ? (
          <span className="text-sm text-muted-foreground">/ {currentLabel}</span>
        ) : null}
      </div>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
        >
          <SunIcon className="size-5 text-muted-foreground rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
          <MoonIcon className="absolute size-5 text-muted-foreground rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
        </Button>
        <DropdownMenu>
        <DropdownMenuTrigger
          render={<Button variant="ghost" size="icon" className="size-8" />}
        >
          <GlobeIcon className="size-5 text-muted-foreground" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {languages.map((lang) => (
            <DropdownMenuItem
              key={lang.code}
              onClick={() => i18n.changeLanguage(lang.code)}
            >
              {lang.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
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
