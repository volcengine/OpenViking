import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '#/components/ui/dialog'
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
  const [open, setOpen] = useState(false)
  const mutation = useMutation({
    mutationFn: () =>
      updateConnectionSettings(connection, {
        thread_require_mention: required,
      }),
    onSuccess: () => {
      setOpen(false)
      onSaved()
    },
  })
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (mutation.isPending) return
        setRequired(saved)
        mutation.reset()
        setOpen(value)
      }}
    >
      <DialogTrigger render={<Button variant="ghost" />}>
        {t('replyMode')}
      </DialogTrigger>
      <DialogContent showCloseButton={!mutation.isPending}>
        <DialogHeader>
          <DialogTitle>{t('replyMode')}</DialogTitle>
          <DialogDescription>{connection.bot_name}</DialogDescription>
        </DialogHeader>
        <ReplyMode
          value={required}
          onChange={setRequired}
          disabled={mutation.isPending}
        />
        {mutation.error && (
          <p role="alert" className="text-sm text-destructive">
            {t('operationFailed')} {mutation.error.message}
          </p>
        )}
        <DialogFooter>
          <Button
            variant="outline"
            disabled={mutation.isPending}
            onClick={() => setOpen(false)}
          >
            {t('cancelEdit')}
          </Button>
          <Button
            disabled={required === saved || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {t(mutation.isPending ? 'savingReplyMode' : 'saveReplyMode')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
