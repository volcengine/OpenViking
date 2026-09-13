import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LoaderCircleIcon, PencilIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '#/components/ui/dialog'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '#/components/ui/tooltip'
import { fetchUserMemorySettings, updateUserMemorySettings } from '#/lib/admin'
import type { AdminConnection, AdminUser } from '#/lib/admin'
import { identifyMemoryPreset } from '#/lib/user-memory-policy'
import type { UserMemoryPolicy } from '#/lib/user-memory-policy'
import { MemoryPolicyPicker } from './memory-policy-picker'
import { getErrorMessage } from '../-lib/error'

export function UserMemoryPolicyCell({
  connection,
  user,
}: {
  connection: AdminConnection
  user: AdminUser
}) {
  const { t } = useTranslation('settings')
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()
  const queryKey = [
    'user-memory-settings',
    connection.baseUrl,
    connection.apiKey,
    user.accountId,
    user.userId,
  ]
  const query = useQuery({
    queryKey,
    queryFn: () =>
      fetchUserMemorySettings(connection, user.accountId, user.userId),
    retry: false,
  })
  const update = useMutation({
    mutationFn: (preset: UserMemoryPolicy) =>
      updateUserMemorySettings(connection, user.accountId, user.userId, preset),
    onSuccess: (result) => {
      queryClient.setQueryData(queryKey, result)
      setOpen(false)
      toast.success(t('memoryPolicy.saved'))
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })
  if (query.isPending)
    return (
      <LoaderCircleIcon
        aria-label={t('loading')}
        className="size-4 animate-spin"
      />
    )
  if (query.isError)
    return (
      <Button
        variant="ghost"
        size="sm"
        title={getErrorMessage(query.error)}
        onClick={() => void query.refetch()}
      >
        {t('memoryPolicy.retry')}
      </Button>
    )
  const preset = identifyMemoryPreset(query.data.memory_policy)
  return (
    <>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setOpen(true)}
              aria-label={t('memoryPolicy.editUser', { user: user.userId })}
            />
          }
        >
          <span className="decoration-dotted underline-offset-4 underline">
            {t(`memoryPolicy.${preset}.name`)}
          </span>
          <PencilIcon className="size-3.5 text-primary" />
        </TooltipTrigger>
        <TooltipContent>{t('memoryPolicy.edit')}</TooltipContent>
      </Tooltip>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!update.isPending) setOpen(next)
        }}
      >
        <DialogContent
          className="sm:max-w-4xl"
          showCloseButton={!update.isPending}
        >
          <DialogHeader>
            <DialogTitle>{t('memoryPolicy.edit')}</DialogTitle>
            <DialogDescription>
              {user.userId} · {t('memoryPolicy.saveHint')}
            </DialogDescription>
          </DialogHeader>
          <MemoryPolicyPicker
            editing
            value={query.data.memory_policy}
            disabled={update.isPending}
            onBack={() => setOpen(false)}
            onSelect={(value) => update.mutate(value)}
          />
          {update.isPending && (
            <LoaderCircleIcon
              className="size-4 animate-spin"
              aria-label={t('loading')}
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
