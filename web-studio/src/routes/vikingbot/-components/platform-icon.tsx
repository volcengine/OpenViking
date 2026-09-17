import feishu from './platform-icons/feishu.svg'
import slack from './platform-icons/slack.svg'
import dingtalk from './platform-icons/dingtalk.svg'
import discord from './platform-icons/discord.svg'
import telegram from './platform-icons/telegram.svg'

const icons: Record<string, string> = {
  feishu,
  slack,
  dingtalk,
  discord,
  telegram,
}

export function PlatformIcon({ platform }: { platform: string }) {
  const src = icons[platform.toLowerCase()]
  if (!src) return null
  return (
    <span
      className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted/50"
      aria-hidden="true"
    >
      <img src={src} alt="" className="size-5 object-contain" />
    </span>
  )
}
