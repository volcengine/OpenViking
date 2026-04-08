import { Link, useMatchRoute } from '@tanstack/react-router'
import { Brain, FolderTree, Search, Settings, Shield } from 'lucide-react'
import type { ComponentType } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from '#/components/ui/sidebar'

const sidebarNav = [
  {
    labelKey: 'sidebar.group.data',
    items: [
      { icon: FolderTree as ComponentType<{ className?: string }>, labelKey: 'sidebar.filesystem', to: '/data/filesystem' as const },
      { icon: Search as ComponentType<{ className?: string }>, labelKey: 'sidebar.find', to: '/data/find' as const },
      { icon: Brain as ComponentType<{ className?: string }>, labelKey: 'sidebar.memory', to: '/data/memory' as const },
    ],
  },
  {
    labelKey: 'sidebar.group.ops',
    items: [
      { icon: Settings as ComponentType<{ className?: string }>, labelKey: 'sidebar.ops', to: '/legacy/ops' as const },
    ],
  },
  {
    labelKey: 'sidebar.group.access',
    items: [
      { icon: Shield as ComponentType<{ className?: string }>, labelKey: 'sidebar.settings', to: '/access/settings' as const },
    ],
  },
]

export function AppSidebar() {
  const matchRoute = useMatchRoute()
  const { t } = useTranslation()

  return (
    <Sidebar>
      <SidebarContent>
        {sidebarNav.map((group) => (
          <SidebarGroup key={group.labelKey}>
            <SidebarGroupLabel>{t(group.labelKey)}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => {
                  const isActive = Boolean(matchRoute({ to: item.to }))
                  const label = t(item.labelKey)

                  return (
                    <SidebarMenuItem key={item.to}>
                      <SidebarMenuButton
                        isActive={isActive}
                        tooltip={label}
                        render={<Link to={item.to} />}
                      >
                        <item.icon className="size-4" />
                        <span>{label}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  )
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  )
}
