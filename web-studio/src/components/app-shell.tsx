import * as React from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import {
  ActivityIcon,
  BlocksIcon,
  FolderTreeIcon,
  HomeIcon,
  LanguagesIcon,
  MoonIcon,
  PlugZapIcon,
  SunIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from 'next-themes'

import { ConnectionDialog } from '#/components/connection-dialog'
import { Badge } from '#/components/ui/badge'
import { buttonVariants } from '#/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '#/components/ui/dropdown-menu'
import { ScrollArea } from '#/components/ui/scroll-area'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from '#/components/ui/sidebar'
import { AppConnectionProvider, useAppConnection } from '#/hooks/use-app-connection'
import { describeServerMode } from '#/hooks/use-server-mode'

const NAV_ITEMS = [
  {
    icon: HomeIcon,
    id: 'home',
    titleKey: 'navigation.home.title',
    to: '/home',
  },
  {
    icon: FolderTreeIcon,
    id: 'resources',
    titleKey: 'navigation.resources.title',
    to: '/resources',
  },
  {
    icon: BlocksIcon,
    id: 'sessions',
    titleKey: 'navigation.sessions.title',
    to: '/sessions',
  },
  {
    icon: ActivityIcon,
    id: 'operations',
    titleKey: 'navigation.operations.title',
    to: '/operations',
  },
] as const

const LANGUAGE_OPTIONS = [
  {
    shortLabel: 'EN',
    title: 'English',
    value: 'en',
  },
  {
    shortLabel: '中文',
    title: '中文',
    value: 'zh-CN',
  },
] as const

function resolveLanguage(value: string | undefined): (typeof LANGUAGE_OPTIONS)[number]['value'] {
  if (value?.toLowerCase().startsWith('zh')) {
    return 'zh-CN'
  }

  return 'en'
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AppConnectionProvider>
      <AppShellInner>{children}</AppShellInner>
    </AppConnectionProvider>
  )
}

function AppShellInner({ children }: { children: React.ReactNode }) {
  const { i18n, t } = useTranslation(['appShell', 'common'])
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const { openConnectionDialog, serverMode } = useAppConnection()
  const { setTheme, resolvedTheme } = useTheme()
  const currentItem = NAV_ITEMS.find((item) => pathname === item.to || pathname.startsWith(`${item.to}/`))
  const serverModeBadge = describeServerMode(serverMode)
  const currentLanguage = resolveLanguage(i18n.resolvedLanguage ?? i18n.language)
  const currentLanguageOption = LANGUAGE_OPTIONS.find((item) => item.value === currentLanguage) ?? LANGUAGE_OPTIONS[0]

  return (
    <SidebarProvider
      defaultOpen
      className='flex h-svh flex-col overflow-hidden bg-sidebar'
      style={{ '--header-height': '3rem' } as React.CSSProperties}
    >
      <header className='flex h-12 shrink-0 items-center justify-between border-b border-border/70 bg-sidebar pl-2 pr-4 backdrop-blur-md md:pr-6'>
        <div className='flex min-w-0 items-center gap-4'>
          <SidebarTrigger className='shrink-0' />
        </div>

        <div className='flex items-center gap-2'>
          <Badge variant={serverModeBadge.variant}>
            {t(serverModeBadge.labelKey, { ns: 'common' })}
          </Badge>

          <button
            type='button'
            aria-label='Toggle theme'
            className={buttonVariants({ size: 'sm', variant: 'ghost' })}
            onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
          >
            <SunIcon className='size-4 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0' />
            <MoonIcon className='absolute size-4 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100' />
          </button>

          <DropdownMenu>
            <DropdownMenuTrigger
              aria-label={t('language.label', { ns: 'common' })}
              className={buttonVariants({ size: 'sm', variant: 'ghost' })}
            >
              <LanguagesIcon />
            </DropdownMenuTrigger>
            <DropdownMenuContent align='end' className='w-32 min-w-32'>
              <DropdownMenuGroup>
                <DropdownMenuLabel>{t('language.label', { ns: 'common' })}</DropdownMenuLabel>
                {LANGUAGE_OPTIONS.map((item) => {
                  const isActive = item.value === currentLanguage

                  return (
                    <DropdownMenuItem
                      key={item.value}
                      className='justify-between'
                      onClick={() => {
                        if (!isActive) {
                          void i18n.changeLanguage(item.value)
                        }
                      }}
                    >
                      <span>{item.title}</span>
                      {isActive ? <span className='text-xs text-muted-foreground'>{t('language.current', { ns: 'common' })}</span> : null}
                    </DropdownMenuItem>
                  )
                })}
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      <div className='flex min-h-0 flex-1 overflow-hidden'>
        <Sidebar variant='sidebar' collapsible='icon' className='!border-r-0'>
          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel className='text-base justify-center'>{t('sidebar.workspaceGroupLabel', { ns: 'appShell' })}</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {NAV_ITEMS.map((item) => {
                    const Icon = item.icon
                    const isActive = pathname === item.to || pathname.startsWith(`${item.to}/`)
                    const title = t(item.titleKey, { ns: 'appShell' })

                    return (
                      <SidebarMenuItem key={item.id}>
                        <SidebarMenuButton
                          render={<Link to={item.to} />}
                          isActive={isActive}
                          tooltip={title}
                          className='text-base'
                        >
                          <Icon className='size-5' />
                          <span>{title}</span>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    )
                  })}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          </SidebarContent>

          <SidebarFooter>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton onClick={openConnectionDialog} tooltip={t('footer.connection', { ns: 'appShell' })} className='text-base'>
                  <PlugZapIcon className='size-5' />
                  <span>{t('footer.connection', { ns: 'appShell' })}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarFooter>
        </Sidebar>

        <SidebarInset className='min-h-0 flex-1 overflow-hidden rounded-none border-0 bg-sidebar shadow-none ring-0 md:m-0 md:ml-0'>
          <ScrollArea className='min-h-0 flex-1'>
            <div className='mx-auto flex w-full flex-col gap-6 px-4 py-6 md:px-6'>
              {children}
            </div>
          </ScrollArea>
        </SidebarInset>
      </div>

      <ConnectionDialog />
    </SidebarProvider>
  )
}