import { CircleAlertIcon, CircleHelpIcon, CircleXIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { cn } from '#/shared/lib/utils'
import { Badge } from '#/shared/ui/badge'
import { getLiveMountStatus, mountStatusStyles } from '../-lib/mount-status'
import type { LiveContextMount } from '../-lib/model'

export function LiveMountStatusBadge({
  mount,
  compact = false,
}: {
  mount: LiveContextMount
  compact?: boolean
}) {
  const { t } = useTranslation('arranger')
  const status = getLiveMountStatus(mount)
  const label = t(`live.statusLabels.${status}`)
  const description = t(`live.statusDescriptions.${status}`)
  const title = [label, description, status !== 'VALID' && mount.reason]
    .filter(Boolean)
    .join('\n')
  if (compact) {
    if (status === 'VALID') return null
    const Icon =
      status === 'REVIEW'
        ? CircleAlertIcon
        : status === 'UNKNOWN'
          ? CircleHelpIcon
          : CircleXIcon
    return (
      <span role="img" aria-label={title} title={title} className="shrink-0">
        <Icon className={cn('size-3.5', mountStatusStyles[status].icon)} />
      </span>
    )
  }
  return (
    <Badge
      variant="outline"
      className={cn('border-transparent', mountStatusStyles[status].badge)}
      title={title}
    >
      {label}
    </Badge>
  )
}
