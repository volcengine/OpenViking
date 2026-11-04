import { Button } from '#/shared/ui/button'
import { CopyIcon, EyeIcon, EyeOffIcon } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { AdminUser } from '#/shared/api/admin'

export function UserApiKeyCell({
  user,
  onCopy,
}: {
  user: AdminUser
  onCopy: (value: string) => void
}) {
  const { t } = useTranslation('appShell')
  const [visible, setVisible] = useState(false)
  const label = user.apiKey
    ? visible
      ? user.apiKey
      : `${user.apiKey.slice(0, 3)}***********`
    : user.keyPrefix
      ? `${user.keyPrefix}********`
      : '—'
  return (
    <div className="users-api-key">
      <span className="truncate" title={visible ? undefined : user.keyPrefix}>
        {label}
      </span>
      {user.apiKey ? (
        <>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={t(visible ? 'apiKey.hide' : 'apiKey.show')}
            onClick={() => setVisible(!visible)}
          >
            {visible ? <EyeOffIcon /> : <EyeIcon />}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={t('apiKey.copy')}
            onClick={() => onCopy(user.apiKey!)}
          >
            <CopyIcon />
          </Button>
        </>
      ) : null}
    </div>
  )
}
