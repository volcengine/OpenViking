import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from '#/shared/ui/sidebar'
import { Link } from '@tanstack/react-router'
import {
  FileSearchIcon,
  BotIcon,
  BracesIcon,
  FolderLockIcon,
  KeyRoundIcon,
  LayoutDashboardIcon,
  ListChecksIcon,
  ScrollTextIcon,
  ServerCogIcon,
  WaypointsIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

export function StudioManagementNavigation({
  pathname,
  isRoot,
  showRootConfiguration = isRoot,
  showGateway = false,
  gatewayOnly = false,
  developmentMode = false,
}: {
  pathname: string
  isRoot: boolean
  showRootConfiguration?: boolean
  showGateway?: boolean
  gatewayOnly?: boolean
  developmentMode?: boolean
}) {
  const { t } = useTranslation('appShell')
  const groups = [
    ...(!gatewayOnly && !developmentMode
      ? ([
          {
            label: 'adminNavigation.overview',
            items: [
              {
                to: '/admin/dashboard',
                title: 'adminNavigation.dashboard',
                icon: LayoutDashboardIcon,
              },
            ],
          },
          {
            label: 'adminNavigation.members',
            items: [
              {
                to: '/admin/users',
                title: 'adminNavigation.users',
                icon: KeyRoundIcon,
              },
            ],
          },
          {
            label: 'adminNavigation.spaceConfiguration',
            items: [
              {
                to: '/admin/users/permissions',
                title: 'adminNavigation.permissions',
                icon: FolderLockIcon,
              },
              {
                to: '/admin/users/memory-templates',
                title: 'adminNavigation.extraction',
                icon: FileSearchIcon,
              },
            ],
          },
        ] as const)
      : []),
    {
      label: 'adminNavigation.services',
      items: [
        ...(isRoot && !gatewayOnly
          ? ([
              {
                to: '/admin/vikingbot',
                title: 'modes.botManagement',
                icon: BotIcon,
              },
            ] as const)
          : []),
      ],
    },
    ...(!gatewayOnly && !developmentMode
      ? ([
          {
            label: 'adminNavigation.operations',
            items: [
              {
                to: '/admin/tasks',
                title: 'adminNavigation.tasks',
                icon: ListChecksIcon,
              },
              {
                to: '/admin/request-logs',
                title: 'navigation.requestLogs.title',
                icon: ScrollTextIcon,
              },
            ],
          },
        ] as const)
      : []),
    {
      label: 'adminNavigation.root',
      items: [
        ...(showRootConfiguration && !gatewayOnly && !developmentMode
          ? ([
              {
                to: '/admin/configuration',
                title: 'modes.configuration',
                icon: BracesIcon,
              },
            ] as const)
          : []),
        ...(isRoot && !gatewayOnly && showRootConfiguration
          ? ([
              {
                to: '/admin/configuration/server',
                title: 'modes.serverConfiguration',
                icon: ServerCogIcon,
              },
            ] as const)
          : []),
        ...(showGateway && (showRootConfiguration || gatewayOnly)
          ? ([
              {
                to: '/admin/gateway',
                title: 'footer.gateway',
                icon: WaypointsIcon,
              },
            ] as const)
          : []),
      ],
    },
  ]
  const current = pathname.replace(/\/$/, '')
  return groups
    .filter(({ items }) => items.length > 0)
    .map(({ label, items }) => (
      <SidebarGroup key={label} className="px-2.5 pb-1">
        <SidebarGroupLabel className="h-7 px-2 text-xs font-normal text-muted-foreground">
          {t(label)}
        </SidebarGroupLabel>
        <SidebarGroupContent>
          <SidebarMenu>
            {items.map(({ to, title, icon: Icon }) => (
              <SidebarMenuItem key={to}>
                <SidebarMenuButton
                  render={<Link to={to} />}
                  isActive={
                    current === to ||
                    (to === '/admin/users' &&
                      current === '/admin/users/groups') ||
                    (to === '/admin/gateway' && current.startsWith(`${to}/`))
                  }
                  tooltip={t(title)}
                  className="min-h-9 h-auto rounded-lg px-2.5 data-[active=true]:font-semibold [&>svg:first-child]:size-4! [&>svg:first-child]:rounded-none! [&>svg:first-child]:bg-transparent! [&>svg:first-child]:p-0! [&>svg:first-child]:shadow-none! [&>span:last-child]:whitespace-normal group-data-[collapsible=icon]:[&>span:last-child]:hidden"
                >
                  <Icon />
                  <span>{t(title)}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
    ))
}
