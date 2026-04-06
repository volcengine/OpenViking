import { Link, useMatchRoute } from '@tanstack/react-router'
import { Brain, FolderTree, Search, Settings, Shield } from 'lucide-react'
import type { ComponentType } from 'react'

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
    label: 'Data',
    items: [
      { icon: FolderTree as ComponentType<{ className?: string }>, label: 'FileSystem', to: '/data/filesystem' as const },
      { icon: Search as ComponentType<{ className?: string }>, label: 'Find', to: '/data/find' as const },
      { icon: Brain as ComponentType<{ className?: string }>, label: 'Add Memory', to: '/data/memory' as const },
    ],
  },
  {
    label: 'Ops',
    items: [
      { icon: Settings as ComponentType<{ className?: string }>, label: 'Ops', to: '/legacy/ops' as const },
    ],
  },
  {
    label: 'Access',
    items: [
      { icon: Shield as ComponentType<{ className?: string }>, label: 'Settings', to: '/access/settings' as const },
    ],
  },
]

export function AppSidebar() {
  const matchRoute = useMatchRoute()

  return (
    <Sidebar>
      <SidebarContent>
        {sidebarNav.map((group) => (
          <SidebarGroup key={group.label}>
            <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => {
                  const isActive = Boolean(matchRoute({ to: item.to }))

                  return (
                    <SidebarMenuItem key={item.to}>
                      <SidebarMenuButton
                        isActive={isActive}
                        tooltip={item.label}
                        render={<Link to={item.to} />}
                      >
                        <item.icon className="size-4" />
                        <span>{item.label}</span>
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
