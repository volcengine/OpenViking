import { ChevronDown, Brain, FileText, Wrench, FolderOpen } from 'lucide-react'
import { useState } from 'react'

import { cn } from '#/lib/utils'

import type { FindContextType, FindResultItem, GroupedFindResult } from '../-types/viking-fm'
import { fileNameFromUri, parentUri } from '../-lib/normalize'

interface FindResultsProps {
  data: GroupedFindResult
  onNavigate: (uri: string) => void
}

const GROUP_CONFIG: Array<{
  key: keyof Pick<GroupedFindResult, 'memories' | 'resources' | 'skills'>
  label: string
  icon: typeof Brain
  type: FindContextType
  accent: string
}> = [
  { key: 'resources', label: 'Resources', icon: FileText, type: 'resource', accent: 'bg-blue-500/10 text-blue-600 dark:text-blue-400' },
  { key: 'memories', label: 'Memories', icon: Brain, type: 'memory', accent: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' },
  { key: 'skills', label: 'Skills', icon: Wrench, type: 'skill', accent: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' },
]

function formatDisplayUri(uri: string): { name: string; parent: string } {
  const name = fileNameFromUri(uri)
  const dir = parentUri(uri)
  const parent = dir === 'viking://' ? 'viking://' : dir.replace(/\/$/, '').split('/').slice(-2).join('/')
  return { name, parent }
}

function ScoreIndicator({ score }: { score: number }) {
  const width = Math.max(Math.min(Math.round(score * 100), 100), 8)
  const hue = score > 0.7 ? 'bg-emerald-500/70' : score > 0.4 ? 'bg-amber-500/60' : 'bg-zinc-400/50'
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1 w-12 overflow-hidden rounded-full bg-muted">
        <div className={cn('h-full rounded-full transition-all', hue)} style={{ width: `${width}%` }} />
      </div>
    </div>
  )
}

export function FindResults({ data, onNavigate }: FindResultsProps) {
  if (data.total === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-muted-foreground">
        <FileText className="size-8 opacity-30" />
        <p className="text-sm">未找到相关结果</p>
      </div>
    )
  }

  return (
    <div className="py-1">
      {GROUP_CONFIG.map((group) => {
        const items = data[group.key]
        if (items.length === 0) return null
        return (
          <FindResultGroup
            key={group.key}
            label={group.label}
            icon={group.icon}
            accent={group.accent}
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
  accent,
  items,
  onNavigate,
}: {
  label: string
  icon: typeof Brain
  accent: string
  items: FindResultItem[]
  onNavigate: (uri: string) => void
}) {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div className="px-3 py-1">
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground transition-colors hover:text-foreground"
        onClick={() => setCollapsed(!collapsed)}
      >
        <ChevronDown className={cn('size-3 transition-transform', collapsed && '-rotate-90')} />
        <span className={cn('inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-semibold', accent)}>
          <Icon className="size-3" />
          {label}
        </span>
        <span className="text-[10px] tabular-nums text-muted-foreground/60">{items.length}</span>
      </button>

      {!collapsed && (
        <div className="mt-0.5 space-y-px">
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
  const [expanded, setExpanded] = useState(false)
  const { name, parent } = formatDisplayUri(item.uri)
  const hasDetail = item.abstract || item.overview

  return (
    <div
      className={cn(
        'group rounded-lg transition-colors',
        expanded ? 'bg-muted/50' : 'hover:bg-muted/30',
      )}
    >
      <div className="flex items-start gap-3 px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="truncate text-sm font-medium text-foreground transition-colors hover:text-primary"
              onClick={() => onNavigate(item.uri)}
              title={item.uri}
            >
              {name}
            </button>
            <ScoreIndicator score={item.score} />
          </div>
          <div className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground/70">
            <FolderOpen className="size-3 shrink-0" />
            <span className="truncate" title={item.uri}>{parent}</span>
          </div>
          {!expanded && item.abstract && (
            <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">{item.abstract}</p>
          )}
        </div>
        {hasDetail && (
          <button
            type="button"
            className="mt-0.5 shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-muted hover:text-foreground"
            onClick={() => setExpanded(!expanded)}
            title={expanded ? '收起' : '展开详情'}
          >
            <ChevronDown className={cn('size-3.5 transition-transform', expanded && 'rotate-180')} />
          </button>
        )}
      </div>

      {expanded && hasDetail && (
        <div className="px-3 pb-3">
          <div className="rounded-md bg-background/80 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
            {item.overview || item.abstract}
          </div>
        </div>
      )}
    </div>
  )
}
