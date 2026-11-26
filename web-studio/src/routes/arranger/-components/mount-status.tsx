import { CircleXIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { cn } from '#/shared/lib/utils'
import { Badge } from '#/shared/ui/badge'
import { getLiveMountStatus, mountStatusStyles } from '../-lib/mount-status'
import type { LiveContextMount } from '../-lib/model'

export function DirectoryMountWarning() {
  const { t } = useTranslation('arranger')
  const title = t('live.directoryWarning')
  return (
    <span role="img" aria-label={title} title={title} className="shrink-0">
      <CircleXIcon className="size-3.5 text-destructive" />
    </span>
  )
}

export function LiveMountStatusBadge({
  mount,
}: {
  mount: LiveContextMount
}) {
  const { t } = useTranslation('arranger')
  const status = getLiveMountStatus(mount)
  const label = t(`live.statusLabels.${status}`)
  const description = t(`live.statusDescriptions.${status}`)
  const title = [label, description, status !== 'VALID' && mount.reason]
    .filter(Boolean)
    .join('\n')
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
