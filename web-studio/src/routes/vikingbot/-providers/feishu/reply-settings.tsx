import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { updateConnectionSettings } from '../../-api'
import type { Connection } from '../../-api'

export function ReplyMode({
  value,
  onChange,
  disabled = false,
}: {
  value: boolean
  onChange: (value: boolean) => void
  disabled?: boolean
}) {
  const { t } = useTranslation('vikingbot')
  return (
    <div className="space-y-2">
      <label className="grid gap-2 text-sm">
        <span>{t('replyMode')}</span>
        <select
          className="h-9 rounded-md border bg-background px-3"
          value={String(value)}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value === 'true')}
        >
          <option value="true">{t('onlyMention')}</option>
          <option value="false">{t('withoutMention')}</option>
        </select>
      </label>
      <p className="text-xs leading-6 text-muted-foreground">
        {t(value ? 'mentionModeHint' : 'withoutMentionHint')}
      </p>
      {!value && (
        <p className="text-xs leading-6 text-muted-foreground">
          {t('groupMessagePermission')}
        </p>
      )}
    </div>
  )
}

export function ReplySettings({
  connection,
  onSaved,
}: {
  connection: Connection
  onSaved: () => void
}) {
  const { t } = useTranslation('vikingbot')
  const saved = connection.settings?.thread_require_mention !== false
  const [required, setRequired] = useState(saved)
  const mutation = useMutation({
    mutationFn: () =>
      updateConnectionSettings(connection, {
        thread_require_mention: required,
      }),
    onSuccess: onSaved,
  })
  return (
    <div className="space-y-3 border-t pt-4">
      <ReplyMode
        value={required}
        onChange={setRequired}
        disabled={mutation.isPending}
      />
      <Button
        variant="outline"
        size="sm"
        disabled={required === saved || mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        {t(mutation.isPending ? 'savingReplyMode' : 'saveReplyMode')}
      </Button>
      {mutation.error && (
        <p role="alert" className="text-sm text-destructive">
          {t('operationFailed')} {mutation.error.message}
        </p>
      )}
    </div>
  )
}
