import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '#/components/ui/dialog'
import { rotateCredentials } from '../../-api'
import type { Connection } from '../../-api'

export function ConnectionCredentials({
  connection,
  onSaved,
}: {
  connection: Connection
  onSaved: () => void
}) {
  const { t } = useTranslation('vikingbot')
  const [secret, setSecret] = useState('')
  const [open, setOpen] = useState(false)
  const mutation = useMutation({
    mutationFn: () =>
      rotateCredentials(
        connection,
        { app_secret: secret },
        connection.identity_user,
      ),
    onSuccess: () => {
      setSecret('')
      setOpen(false)
      onSaved()
    },
  })
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (mutation.isPending) return
        setSecret('')
        mutation.reset()
        setOpen(value)
      }}
    >
      <DialogTrigger render={<Button variant="ghost" />}>
        {t('rotate')}
      </DialogTrigger>
      <DialogContent showCloseButton={!mutation.isPending}>
        <DialogHeader>
          <DialogTitle>{t('rotate')}</DialogTitle>
          <DialogDescription>{t('rotateHint')}</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (!mutation.isPending) mutation.mutate()
          }}
        >
          <label className="block space-y-1 text-sm">
            <span>{t('appSecret')}</span>
            <Input
              autoComplete="new-password"
              type="password"
              disabled={mutation.isPending}
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
            />
          </label>
          <p className="text-sm text-muted-foreground">
            {t('runtimeUser')}: {connection.identity_user}
          </p>
          {mutation.error && (
            <p role="alert" className="text-sm text-destructive">
              {t('operationFailed')} {mutation.error.message}
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={mutation.isPending}
              onClick={() => {
                setOpen(false)
                setSecret('')
                mutation.reset()
              }}
            >
              {t('cancelEdit')}
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {t('saveCredentials')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
