import {
  MemoryPolicyDetails,
  MemoryPolicyPicker,
} from '#/features/admin/users/components/memory-policy-picker'
import type { AdminConnection, AdminUser } from '#/shared/api/admin'
import {
  fetchUserMemorySettings,
  updateUserMemorySettings,
} from '#/shared/api/admin'
import { getErrorMessage } from '#/shared/lib/error'
import type { UserMemoryPolicy } from '#/shared/lib/user-memory-policy'
import { identifyMemoryPreset } from '#/shared/lib/user-memory-policy'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import { Tooltip, TooltipContent, TooltipTrigger } from '#/shared/ui/tooltip'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LoaderCircleIcon, PencilIcon } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

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
    onError: (error) =>
      toast.error(t('memoryPolicy.saveFailed'), {
        description: getErrorMessage(error),
      }),
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
              disabled={query.isFetching}
              onClick={async () => {
                const result = await query.refetch()
                if (result.isSuccess) setOpen(true)
              }}
              aria-label={t('memoryPolicy.editUser', { user: user.userId })}
            />
          }
        >
          <span className="decoration-dotted underline-offset-4 underline">
            {t(`memoryPolicy.${preset}.name`)}
          </span>
          <PencilIcon className="size-3.5 text-primary" />
        </TooltipTrigger>
        <TooltipContent className="block w-96 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border bg-popover p-0 text-sm text-popover-foreground shadow-lg [&>[aria-hidden=true]]:bg-popover">
          <p className="border-b px-4 py-3 font-semibold">
            {t(`memoryPolicy.${preset}.name`)}
          </p>
          <MemoryPolicyDetails value={query.data.memory_policy} />
        </TooltipContent>
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
