import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '#/shared/ui/collapsible'
import {
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
} from '#/shared/ui/sidebar'
import { Link } from '@tanstack/react-router'
import {
  ActivityIcon,
  BotIcon,
  BrainIcon,
  ChevronRightIcon,
  HistoryIcon,
  HomeIcon,
  ListChecksIcon,
  MessagesSquareIcon,
  PanelsTopLeftIcon,
  ScrollTextIcon,
  SearchIcon,
  SparklesIcon,
  WorkflowIcon,
} from 'lucide-react'
import * as React from 'react'
import type { useTranslation } from 'react-i18next'

export type NavItem = {
  icon: React.ComponentType
  id: string
  section: 'workspace' | 'operations'
  titleKey: string
  to: string
  children?: readonly NavSubItem[]
}

export type NavGroupItemProps = {
  item: NavItem & { children: readonly NavSubItem[] }
  pathname: string
  title: string
  t: ReturnType<typeof useTranslation>['t']
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    icon: HomeIcon,
    id: 'home',
    section: 'workspace',
    titleKey: 'navigation.home.title',
    to: '/home',
  },
  {
    icon: PanelsTopLeftIcon,
    id: 'filesystem',
    section: 'workspace',
    titleKey: 'navigation.filesystem.title',
    to: '/filesystem',
  },
  {
    icon: BotIcon,
    id: 'vikingbot',
    section: 'workspace',
    titleKey: 'vikingbot:title',
    to: '/vikingbot',
  },
  {
    icon: SearchIcon,
    id: 'retrieval',
    section: 'workspace',
    titleKey: 'navigation.retrieval.title',
    to: '/retrieval',
  },
  {
    icon: SparklesIcon,
    id: 'skills',
    section: 'workspace',
    titleKey: 'navigation.skills.title',
    to: '/skills',
  },

  {
    icon: WorkflowIcon,
    id: 'compile',
    section: 'workspace',
    titleKey: 'navigation.compile.title',
    to: '/compile',
  },
  {
    icon: MessagesSquareIcon,
    id: 'sessions',
    section: 'operations',
    titleKey: 'navigation.sessions.title',
    to: '/sessions',
  },
  {
    icon: BrainIcon,
    id: 'agentExperience',
    section: 'operations',
    titleKey: 'navigation.agentExperience.title',
    to: '/agent-experience',
  },
  {
    icon: ScrollTextIcon,
    id: 'requestLogs',
    section: 'operations',
    titleKey: 'navigation.requestLogs.title',
    to: '/request-logs',
  },
  {
    icon: ListChecksIcon,
    id: 'tasks',
    section: 'operations',
    titleKey: 'navigation.tasks.title',
    to: '/tasks',
  },
  {
    icon: HistoryIcon,
    id: 'watches',
    section: 'operations',
    titleKey: 'navigation.watches.title',
    to: '/watches',
  },
  {
    icon: ActivityIcon,
    id: 'monitoring',
    section: 'operations',
    titleKey: 'navigation.monitoring.title',
    to: '/monitoring',
  },
] as const

export const NAV_SECTIONS = [
  {
    id: 'workspace',
    titleKey: 'sidebar.groups.workspace',
  },
  {
    id: 'operations',
    titleKey: 'sidebar.groups.operations',
  },
] as const

export function NavGroupItem({ item, pathname, title, t }: NavGroupItemProps) {
  const Icon = item.icon
  const isActive = pathname === item.to || pathname.startsWith(`${item.to}/`)
  const [open, setOpen] = React.useState(isActive)

  React.useEffect(() => {
    if (isActive) {
      setOpen(true)
    }
  }, [isActive])

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="group/collapsible"
    >
      <SidebarMenuItem>
        <CollapsibleTrigger
          render={
            <SidebarMenuButton tooltip={title}>
              <Icon />
              <span>{title}</span>
              <ChevronRightIcon className="ml-auto transition-transform duration-200 group-data-[open]/collapsible:rotate-90" />
            </SidebarMenuButton>
          }
        />
        <CollapsibleContent>
          <SidebarMenuSub>
            {item.children.map((child) => {
              const ChildIcon = child.icon
              const childActive =
                pathname === child.to ||
                (child.to !== item.to && pathname.startsWith(`${child.to}/`))
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

export type NavSubItem = {
  icon: React.ComponentType
  id: string
  titleKey: string
  to: string
}
