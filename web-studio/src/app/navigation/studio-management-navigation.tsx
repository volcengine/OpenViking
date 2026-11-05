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
  BookOpenTextIcon,
  BotIcon,
  BracesIcon,
  ServerCogIcon,
  ShieldCheckIcon,
  UserRoundIcon,
  UsersRoundIcon,
  WaypointsIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

const managementItems = [
  {
    to: '/admin/users',
    title: 'settings:groups.usersTab',
    icon: UserRoundIcon,
  },
  {
    to: '/admin/users/groups',
    title: 'settings:groups.title',
    icon: UsersRoundIcon,
  },
  {
    to: '/admin/users/permissions',
    title: 'settings:acl.page.title',
    icon: ShieldCheckIcon,
  },
  {
    to: '/admin/users/memory-templates',
    title: 'settings:memoryTemplates.tab',
    icon: BookOpenTextIcon,
  },
  {
    to: '/admin/configuration',
    title: 'appShell:modes.configuration',
    icon: BracesIcon,
  },
] as const

export function StudioManagementNavigation({
  pathname,
  isRoot,
  showGateway = false,
  gatewayOnly = false,
  developmentMode = false,
}: {
  pathname: string
  isRoot: boolean
  showGateway?: boolean
  gatewayOnly?: boolean
  developmentMode?: boolean
}) {
  const { t } = useTranslation(['appShell', 'settings'])
  const serviceItems = [
    ...(isRoot && !gatewayOnly
      ? ([
          {
            to: '/admin/configuration/server',
            title: 'appShell:modes.serverConfiguration',
            icon: ServerCogIcon,
          },
        ] as const)
      : []),
    ...(isRoot
      ? ([
          {
            to: '/admin/vikingbot',
            title: 'appShell:modes.botManagement',
            icon: BotIcon,
          },
        ] as const)
      : []),
    ...(showGateway
      ? ([
          {
            to: '/admin/gateway',
            title: 'appShell:footer.gateway',
            icon: WaypointsIcon,
          },
        ] as const)
      : []),
  ]
  const groups = [
    ...(!gatewayOnly && !developmentMode
      ? [{ label: 'appShell:modes.management', items: managementItems }]
      : []),
    ...(serviceItems.length
      ? [{ label: 'appShell:sidebar.groups.services', items: serviceItems }]
      : []),
  ]
  return (
    <>
      {groups.map(({ label, items }) => (
        <SidebarGroup key={label} className="pb-1">
          <SidebarGroupLabel className="h-7 px-2 pt-2 text-[11px] font-semibold text-sidebar-foreground/45">
            {t(label)}
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {items.map(({ to, title, icon: Icon }) => (
                <SidebarMenuItem key={to}>
                  <SidebarMenuButton
                    render={<Link to={to} />}
                    isActive={
                      pathname.replace(/\/$/, '') === to ||
                      (to === '/admin/gateway' && pathname.startsWith(`${to}/`))
                    }
                    tooltip={t(title)}
                    className="h-9"
                  >
                    <Icon />
                    <span>{t(title)}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      ))}
    </>
  )
}
