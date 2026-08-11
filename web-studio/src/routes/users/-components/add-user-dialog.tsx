import * as React from 'react'
import { LoaderCircleIcon, PlusIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { Input } from '#/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import type { CreateUserInput } from '#/lib/admin'
import { PLAIN_INPUT_PROPS } from '#/lib/form-input'

export function AddUserDialog({
  accountId,
  isPending,
  onCreate,
  onOpenChange,
  open,
}: {
  accountId: string
  isPending: boolean
  onCreate: (draft: CreateUserInput) => void
  onOpenChange: (open: boolean) => void
  open: boolean
}) {
  const { t } = useTranslation('settings')
  const [draft, setDraft] = React.useState<CreateUserInput>({
    accountId,
    role: 'user',
    userId: '',
  })

  React.useEffect(() => {
    if (open) {
      setDraft({ accountId, role: 'user', userId: '' })
    }
  }, [accountId, open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            onCreate(draft)
          }}
        >
          <DialogHeader>
            <DialogTitle>{t('dialogs.addUser.title')}</DialogTitle>
            <DialogDescription>
              {t('dialogs.addUser.currentAccountDescription', { accountId })}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-5">
            <label className="grid gap-2 text-sm font-medium">
              {t('fields.user')}
              <Input
                required
                value={draft.userId}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    userId: event.target.value,
                  }))
                }
                placeholder={t('placeholders.user')}
                {...PLAIN_INPUT_PROPS}
              />
            </label>
            <label className="grid gap-2 text-sm font-medium">
              {t('fields.role')}
              <Select
                value={draft.role}
                onValueChange={(role) =>
                  setDraft((current) => ({
                    ...current,
                    role: role || 'user',
                  }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="user">{t('roles.user')}</SelectItem>
                  <SelectItem value="admin">{t('roles.admin')}</SelectItem>
                </SelectContent>
              </Select>
            </label>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t('actions.cancel')}
            </Button>
            <Button type="submit" disabled={isPending}>
              {isPending ? (
                <LoaderCircleIcon className="animate-spin" />
              ) : (
                <PlusIcon />
              )}
              {t('actions.addUser')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
