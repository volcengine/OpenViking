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
import {
  MemoryDirectoryPreview,
  MemoryPolicyPicker,
  MemoryPolicySummary,
} from './memory-policy-picker'
import { buildMemoryPolicy } from '#/lib/user-memory-policy'
import type { UserMemoryPolicy } from '#/lib/user-memory-policy'
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
  const [preset, setPreset] = React.useState<UserMemoryPolicy>(() =>
    buildMemoryPolicy('general'),
  )
  const [choosing, setChoosing] = React.useState(false)
  const [draft, setDraft] = React.useState<CreateUserInput>({
    accountId,
    role: 'user',
    userId: '',
  })

  React.useEffect(() => {
    if (open) {
      setPreset(buildMemoryPolicy('general'))
      setChoosing(false)
      setDraft({ accountId, role: 'user', userId: '' })
    }
  }, [accountId, open])

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!isPending) onOpenChange(next)
      }}
    >
      <DialogContent
        className="gap-0 p-0 sm:max-w-5xl"
        showCloseButton={!isPending}
      >
        {choosing ? (
          <div className="p-6">
            <DialogHeader className="mb-4">
              <DialogTitle>{t('memoryPolicy.choose')}</DialogTitle>
            </DialogHeader>
            <MemoryPolicyPicker
              value={preset}
              onBack={() => setChoosing(false)}
              onSelect={(value) => {
                setPreset(value)
                setChoosing(false)
              }}
            />
          </div>
        ) : (
          <div className="grid lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
            <form
              className="flex flex-col p-6 lg:p-8"
              onSubmit={(event) => {
                event.preventDefault()
                if (!isPending)
                  onCreate({
                    ...draft,
                    userId: draft.userId.trim(),
                    memoryPolicy: preset,
                  })
              }}
            >
              <DialogHeader>
                <DialogTitle>{t('dialogs.addUser.title')}</DialogTitle>
                <DialogDescription>
                  {t('dialogs.addUser.currentAccountDescription', {
                    accountId,
                  })}
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
              <MemoryPolicySummary
                value={preset}
                onChange={() => setChoosing(true)}
                disabled={isPending}
              />
              <DialogFooter className="mt-8">
                <Button
                  type="button"
                  disabled={isPending}
                  variant="outline"
                  onClick={() => onOpenChange(false)}
                >
                  {t('actions.cancel')}
                </Button>
                <Button
                  type="submit"
                  disabled={isPending || !draft.userId.trim()}
                >
                  {isPending ? (
                    <LoaderCircleIcon className="animate-spin" />
                  ) : (
                    <PlusIcon />
                  )}
                  {t('actions.addUser')}
                </Button>
              </DialogFooter>
            </form>
            <MemoryDirectoryPreview value={preset} userId={draft.userId} />
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
