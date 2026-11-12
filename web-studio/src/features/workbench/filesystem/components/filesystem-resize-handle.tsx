import type { PointerEvent as ReactPointerEvent } from 'react'

export function FilesystemResizeHandle({
  active,
  label,
  onPointerDown,
}: {
  active: boolean
  label: string
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void
}) {
  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation="vertical"
      data-active={active}
      className="group relative z-10 hidden -mx-1.5 w-1.5 shrink-0 cursor-col-resize touch-none items-center justify-center border-x border-transparent transition-colors hover:bg-primary/10 active:bg-primary/15 data-[active=true]:bg-primary/15 lg:flex"
      onPointerDown={onPointerDown}
    >
      <span className="h-full w-px bg-transparent transition-colors group-hover:bg-primary/60 group-data-[active=true]:bg-primary" />
    </div>
  )
}
