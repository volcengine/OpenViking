import { ChevronDown, ExternalLink, Brain, FileText, Wrench } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '#/components/ui/badge'
import { cn } from '#/lib/utils'

import type { FindContextType, FindResultItem, GroupedFindResult } from '../-types/viking-fm'

interface FindResultsProps {
  data: GroupedFindResult
  onNavigate: (uri: string) => void
}

const GROUP_CONFIG: Array<{
  key: keyof Pick<GroupedFindResult, 'memories' | 'resources' | 'skills'>
  label: string
  icon: typeof Brain
  type: FindContextType
}> = [
  { key: 'resources', label: 'Resources', icon: FileText, type: 'resource' },
  { key: 'memories', label: 'Memories', icon: Brain, type: 'memory' },
  { key: 'skills', label: 'Skills', icon: Wrench, type: 'skill' },
]

export function FindResults({ data, onNavigate }: FindResultsProps) {
  if (data.total === 0) {
    return (
      <div className="px-4 py-8 text-center text-sm text-muted-foreground">
        未找到相关结果
      </div>
    )
  }

  return (
    <div className="divide-y">
      {GROUP_CONFIG.map((group) => {
        const items = data[group.key]
        if (items.length === 0) return null
        return (
          <FindResultGroup
            key={group.key}
            label={group.label}
            icon={group.icon}
            items={items}
            onNavigate={onNavigate}
          />
        )
      })}
    </div>
  )
}

function FindResultGroup({
  label,
  icon: Icon,
  items,
  onNavigate,
}: {
  label: string
  icon: typeof Brain
  items: FindResultItem[]
  onNavigate: (uri: string) => void
}) {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div>
      <button
        type="button"
        className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm font-medium hover:bg-muted/50"
        onClick={() => setCollapsed(!collapsed)}
      >
        <ChevronDown className={cn('size-4 transition-transform', collapsed && '-rotate-90')} />
        <Icon className="size-4 text-muted-foreground" />
        <span>{label}</span>
        <Badge variant="secondary" className="ml-1 text-xs">{items.length}</Badge>
      </button>

      {!collapsed && (
        <div>
          {items.map((item, i) => (
            <FindResultRow
              key={`${item.uri}-${i}`}
              item={item}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function FindResultRow({
  item,
  onNavigate,
}: {
  item: FindResultItem
  onNavigate: (uri: string) => void
}) {
  const scorePercent = Math.round(item.score * 100)

  return (
    <div className="border-t px-4 py-2.5 text-sm">
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="min-w-0 truncate text-left text-primary hover:underline"
          onClick={() => onNavigate(item.uri)}
          title={item.uri}
        >
          {item.uri}
        </button>
        <ExternalLink className="size-3 shrink-0 text-muted-foreground" />
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">
          {scorePercent}%
        </span>
      </div>

      {item.abstract && (
        <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
          {item.abstract}
        </p>
      )}
    </div>
  )
}
