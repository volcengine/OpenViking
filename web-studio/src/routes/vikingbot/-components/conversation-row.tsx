import type { ReactNode } from 'react'

export function ConversationRow({
  title,
  subtitle,
  time,
  icon,
  selected,
  onSelect,
  action,
}: {
  title: string
  subtitle: string
  time?: string
  icon: ReactNode
  selected: boolean
  onSelect: () => void
  action?: ReactNode
}) {
  const date =
    time && !Number.isNaN(Date.parse(time)) ? new Date(time) : undefined
  return (
    <div
      className={`group/conversation relative mt-1 rounded-lg hover:bg-muted ${selected ? 'bg-muted' : ''}`}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className="block w-full min-w-0 p-3 text-left text-sm"
      >
        <span className="flex h-5 items-center gap-2 pr-9">
          <span className="shrink-0 text-muted-foreground">{icon}</span>
          <span className="min-w-0 flex-1 truncate font-medium">{title}</span>
        </span>
        <span className="mt-1.5 flex h-4 items-center justify-between gap-2 text-xs text-muted-foreground">
          <span className="truncate rounded bg-muted px-1.5 py-0.5 text-[11px]">
            {subtitle}
          </span>
          {date && (
            <time
              className="shrink-0"
              dateTime={time}
              title={date.toLocaleString()}
            >
              {date.toLocaleString(undefined, {
                month: 'numeric',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
              })}
            </time>
          )}
        </span>
      </button>
      {action && <div className="absolute right-1 top-1">{action}</div>}
    </div>
  )
}
