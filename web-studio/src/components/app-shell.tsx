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
  PlugZapIcon,
  UploadIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

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
  SidebarRail,
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

const ALL_NAV_ITEMS: readonly (NavItem | NavSubItem)[] = NAV_ITEMS.flatMap((item) =>
  item.children ? [...item.children, item] : [item],
)

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
  const currentItem = ALL_NAV_ITEMS.find((item) => pathname === item.to || pathname.startsWith(`${item.to}/`))
  const serverModeBadge = describeServerMode(serverMode)
  const currentLanguage = resolveLanguage(i18n.resolvedLanguage ?? i18n.language)
  const currentLanguageOption = LANGUAGE_OPTIONS.find((item) => item.value === currentLanguage) ?? LANGUAGE_OPTIONS[0]

  return (
    <SidebarProvider
      defaultOpen
      className='flex h-svh flex-col overflow-hidden bg-[radial-gradient(circle_at_top_left,rgba(71,126,255,0.12),transparent_32%),linear-gradient(180deg,rgba(245,248,255,0.9)_0%,rgba(255,255,255,1)_28%)]'
      style={{ '--header-height': '3rem' } as React.CSSProperties}
    >
      <header className='flex h-12 shrink-0 items-center justify-between border-b border-border/70 bg-background/85 pl-2 pr-4 backdrop-blur-md md:pr-6'>
        <div className='flex min-w-0 items-center gap-4'>
          <SidebarTrigger className='shrink-0' />
          <div className='min-w-0'>
            <div className='truncate text-sm font-medium'>
              {currentItem ? t(currentItem.titleKey, { ns: 'appShell' }) : t('header.defaultTitle', { ns: 'appShell' })}
            </div>
          </div>
        </div>

        <div className='flex items-center gap-2'>
          <Badge variant={serverModeBadge.variant}>
            {t(serverModeBadge.labelKey, { ns: 'common' })}
          </Badge>

          <DropdownMenu>
            <DropdownMenuTrigger
              aria-label={t('language.label', { ns: 'common' })}
              className={buttonVariants({ size: 'sm', variant: 'outline' })}
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
        <Sidebar variant='sidebar' collapsible='icon'>
          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel>{t('sidebar.workspaceGroupLabel', { ns: 'appShell' })}</SidebarGroupLabel>
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
                        >
                          <Icon />
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
                <SidebarMenuButton onClick={openConnectionDialog} tooltip={t('footer.connection', { ns: 'appShell' })}>
                  <PlugZapIcon />
                  <span>{t('footer.connection', { ns: 'appShell' })}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarFooter>
          <SidebarRail />
        </Sidebar>

        <SidebarInset className='min-h-0 flex-1 overflow-hidden rounded-none shadow-none md:m-0 md:ml-0'>
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
