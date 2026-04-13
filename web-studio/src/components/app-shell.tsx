import * as React from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import {
  ActivityIcon,
  BlocksIcon,
  ChevronRightIcon,
  FolderTreeIcon,
  HardDriveIcon,
  HomeIcon,
  LanguagesIcon,
  MoonIcon,
  PlugZapIcon,
  SunIcon,
  UploadIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from 'next-themes'

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '#/components/ui/collapsible'
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
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
  SidebarTrigger,
} from '#/components/ui/sidebar'
import { AppConnectionProvider, useAppConnection } from '#/hooks/use-app-connection'
import { ResourceUploadProvider } from '#/hooks/use-resource-upload'
import { describeServerMode } from '#/hooks/use-server-mode'

type NavItem = {
  icon: React.ComponentType
  id: string
  titleKey: string
  to: string
  children?: readonly NavSubItem[]
}

type NavSubItem = {
  icon: React.ComponentType
  id: string
  titleKey: string
  to: string
}

type NavGroupItemProps = {
  item: NavItem & { children: readonly NavSubItem[] }
  pathname: string
  title: string
  t: ReturnType<typeof useTranslation>['t']
}

const NAV_ITEMS: readonly NavItem[] = [
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
    children: [
      {
        icon: HardDriveIcon,
        id: 'fileSystem',
        titleKey: 'navigation.fileSystem.title',
        to: '/resources',
      },
      {
        icon: UploadIcon,
        id: 'addResource',
        titleKey: 'navigation.addResource.title',
        to: '/resources/add-resource',
      },
    ],
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

function NavGroupItem({ item, pathname, title, t }: NavGroupItemProps) {
  const Icon = item.icon
  const isActive = pathname === item.to || pathname.startsWith(`${item.to}/`)
  const [open, setOpen] = React.useState(isActive)

  React.useEffect(() => {
    if (isActive) {
      setOpen(true)
    }
  }, [isActive])

  return (
    <Collapsible open={open} onOpenChange={setOpen} className='group/collapsible'>
      <SidebarMenuItem>
        <CollapsibleTrigger
          render={
            <SidebarMenuButton tooltip={title}>
              <Icon />
              <span>{title}</span>
              <ChevronRightIcon className='ml-auto transition-transform duration-200 group-data-[open]/collapsible:rotate-90' />
            </SidebarMenuButton>
          }
        />
        <CollapsibleContent>
          <SidebarMenuSub>
            {item.children.map((child) => {
              const ChildIcon = child.icon
              const childActive = pathname === child.to || (child.to !== item.to && pathname.startsWith(`${child.to}/`))
              const childTitle = t(child.titleKey, { ns: 'appShell' })

              return (
                <SidebarMenuSubItem key={child.id}>
                  <SidebarMenuSubButton
                    render={<Link to={child.to} />}
                    isActive={childActive}
                  >
                    <ChildIcon />
                    <span>{childTitle}</span>
                  </SidebarMenuSubButton>
                </SidebarMenuSubItem>
              )
            })}
          </SidebarMenuSub>
        </CollapsibleContent>
      </SidebarMenuItem>
    </Collapsible>
  )
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AppConnectionProvider>
      <ResourceUploadProvider>
        <AppShellInner>{children}</AppShellInner>
      </ResourceUploadProvider>
    </AppConnectionProvider>
  )
}

function AppShellInner({ children }: { children: React.ReactNode }) {
  const { i18n, t } = useTranslation(['appShell', 'common'])
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const { openConnectionDialog, serverMode } = useAppConnection()
  const { setTheme, resolvedTheme } = useTheme()
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

        <div className='flex items-center gap-1'>
          <Badge variant={serverModeBadge.variant} className='mr-1'>
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
              <span className='hidden sm:inline'>{currentLanguageOption.shortLabel}</span>
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
                    const isActive = pathname === item.to || pathname.startsWith(`${item.to}/`)
                    const title = t(item.titleKey, { ns: 'appShell' })

                    if (item.children) {
                      return (
                        <NavGroupItem key={item.id} item={item} pathname={pathname} title={title} t={t} />
                      )
                    }

                    const Icon = item.icon

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
            <div className='flex w-full flex-col gap-6 px-4 py-6 md:px-6'>
              {children}
            </div>
          </ScrollArea>
        </SidebarInset>
      </div>

      <ConnectionDialog />
    </SidebarProvider>
  )
}
