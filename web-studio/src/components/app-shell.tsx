import * as React from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import {
  ActivityIcon,
  BlocksIcon,
  FolderTreeIcon,
  PlugZapIcon,
  ShieldIcon,
} from 'lucide-react'

import { ConnectionDialog } from '#/components/connection-dialog'
import { Badge } from '#/components/ui/badge'
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
  SidebarRail,
  SidebarTrigger,
} from '#/components/ui/sidebar'
import { AppConnectionProvider, useAppConnection } from '#/hooks/use-app-connection'
import { describeServerMode } from '#/hooks/use-server-mode'

const NAV_ITEMS = [
  {
    description: '浏览资源树、预览内容，并通过检索 modal 快速定位上下文。',
    icon: FolderTreeIcon,
    id: 'resources',
    title: '资源',
    to: '/resources',
  },
  {
    description: '围绕 session 组织消息、上下文、archive、记忆和异步任务。',
    icon: BlocksIcon,
    id: 'sessions',
    title: '会话',
    to: '/sessions',
  },
  {
    description: '查看服务状态、后台任务、调试信息与运行时指标。',
    icon: ActivityIcon,
    id: 'operations',
    title: '运维',
    to: '/operations',
  },
  {
    description: '账号、用户与密钥管理，仅在具备管理能力时使用。',
    icon: ShieldIcon,
    id: 'admin',
    title: '管理',
    to: '/admin',
  },
] as const

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AppConnectionProvider>
      <AppShellInner>{children}</AppShellInner>
    </AppConnectionProvider>
  )
}

function AppShellInner({ children }: { children: React.ReactNode }) {
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const { openConnectionDialog, serverMode } = useAppConnection()
  const currentItem = NAV_ITEMS.find((item) => pathname === item.to || pathname.startsWith(`${item.to}/`))
  const visibleItems = NAV_ITEMS.filter((item) => !(item.id === 'admin' && serverMode === 'dev-implicit'))

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
            <div className='truncate text-sm font-medium'>{currentItem?.title || 'OpenViking Studio'}</div>
          </div>
        </div>

        <div className='flex items-center'>
          <Badge variant={describeServerMode(serverMode).variant}>
            {describeServerMode(serverMode).label}
          </Badge>
        </div>
      </header>

      <div className='flex min-h-0 flex-1 overflow-hidden'>
        <Sidebar variant='sidebar' collapsible='icon'>
          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel>工作区</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {visibleItems.map((item) => {
                    const Icon = item.icon
                    const isActive = pathname === item.to || pathname.startsWith(`${item.to}/`)

                    return (
                      <SidebarMenuItem key={item.id}>
                        <SidebarMenuButton
                          render={<Link to={item.to} />}
                          isActive={isActive}
                          tooltip={item.title}
                        >
                          <Icon />
                          <span>{item.title}</span>
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
                <SidebarMenuButton onClick={openConnectionDialog} tooltip='连接与身份'>
                  <PlugZapIcon />
                  <span>连接与身份</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarFooter>
          <SidebarRail />
        </Sidebar>

        <SidebarInset className='min-h-0 flex-1 overflow-hidden rounded-none shadow-none md:m-0 md:ml-0'>
          <ScrollArea className='min-h-0 flex-1'>
            <div className='mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 md:px-6'>
              {children}
            </div>
          </ScrollArea>
        </SidebarInset>
      </div>

      <ConnectionDialog />
    </SidebarProvider>
  )
}