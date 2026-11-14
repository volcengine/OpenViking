import {
  getFileTypeColor,
  getFileTypeIcon,
  resolveFileTypeIconKey,
} from '@openviking/react/icons'
import type { CSSProperties } from 'react'

import styles from '../workbench.module.css'

export function FileTypeIcon({ name }: { name: string }) {
  const Icon = getFileTypeIcon(name)
  const key = resolveFileTypeIconKey(name)
  return (
    <Icon
      aria-hidden="true"
      className={styles.fileTypeIcon}
      style={
        {
          '--studio-file-icon-light': getFileTypeColor(key, 'light'),
          '--studio-file-icon-dark': getFileTypeColor(key, 'dark'),
        } as CSSProperties
      }
    />
  )
}
