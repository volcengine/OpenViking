import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from '#/shared/ui/sidebar'
import styles from './studio-management-navigation.module.css'
import { Link } from '@tanstack/react-router'
import {
  BotIcon,
  FolderLockIcon,
  KeyRoundIcon,
  ChartPieIcon,
  BoxIcon,
  LightbulbIcon,
  HexagonIcon,
  CpuIcon,
  ListChecksIcon,
  ScrollTextIcon,
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
                icon: ChartPieIcon,
              },
            ],
          },
          {
            label: 'adminNavigation.spaces',
            items: [
              ...(isRoot
                ? ([
                    {
                      to: '/admin/spaces',
                      title: 'adminNavigation.spaces',
                      icon: BoxIcon,
                    },
                  ] as const)
                : []),
              {
                to: '/admin/users',
                title: 'adminNavigation.users',
                icon: KeyRoundIcon,
              },
              {
                to: '/admin/users/memory-templates',
                title: 'adminNavigation.extraction',
                icon: LightbulbIcon,
              },
              {
                to: '/admin/users/permissions',
                title: 'adminNavigation.permissions',
                icon: FolderLockIcon,
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
        ...(showRootConfiguration && !gatewayOnly && !developmentMode
          ? ([
              {
                to: '/admin/configuration',
                title: 'adminNavigation.generalConfiguration',
                icon: HexagonIcon,
              },
              {
                to: '/admin/configuration/server',
                title: 'adminNavigation.modelConfiguration',
                icon: CpuIcon,
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
                to: '/admin/monitoring',
                title: 'adminNavigation.monitoring',
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
      label: 'adminNavigation.extensions',
      items: [
        ...(showGateway && (showRootConfiguration || gatewayOnly)
          ? ([
              {
                to: '/admin/gateway',
                title: 'adminNavigation.gateway',
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
      <SidebarGroup key={label} className="px-3 pt-0 pb-4">
        <SidebarGroupLabel className="h-[22px] px-2 text-[13px] font-normal text-muted-foreground">
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
                  className={`${styles.item} min-h-8 h-auto rounded-lg px-2.5 text-[13px] leading-[22px] data-[active=true]:font-semibold [&>svg:first-child]:size-4! [&>svg:first-child]:rounded-none! [&>svg:first-child]:bg-transparent! [&>svg:first-child]:p-0! [&>svg:first-child]:shadow-none! [&>span:last-child]:whitespace-normal group-data-[collapsible=icon]:[&>span:last-child]:hidden`}
                >
                  <Icon />
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span>{t(title)}</span>
                    {to === '/admin/gateway' && (
                      <span className="shrink-0 rounded border border-border px-1 text-[10px] leading-4 font-normal text-muted-foreground">
                        {t('footer.beta')}
                      </span>
                    )}
                  </span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
    ))
}
