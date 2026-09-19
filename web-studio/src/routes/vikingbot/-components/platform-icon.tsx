import {
  FeatherIcon,
  Gamepad2Icon,
  HashIcon,
  SendIcon,
  ZapIcon,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

const icons: Record<string, LucideIcon> = {
  feishu: FeatherIcon,
  slack: HashIcon,
  dingtalk: ZapIcon,
  discord: Gamepad2Icon,
  telegram: SendIcon,
}

export function PlatformIcon({ platform }: { platform: string }) {
  const Icon = icons[platform.toLowerCase()]
  if (!Icon) return null
  return (
    <span
      className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted/50 text-muted-foreground"
      aria-hidden="true"
    >
      <Icon className="size-5" />
    </span>
  )
}
