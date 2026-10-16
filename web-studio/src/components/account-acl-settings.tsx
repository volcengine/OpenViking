import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Settings2Icon } from 'lucide-react'
import { useAclManagement } from '#/hooks/use-acl-management'
import { Button } from '#/components/ui/button'
import { Alert, AlertTitle, AlertDescription } from '#/components/ui/alert'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from '#/components/ui/alert-dialog'
import { getErrorMessage } from '#/routes/users/-lib/error'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '#/components/ui/popover'

export function AccountAclSettings({
  inPopover = false,
}: {
  inPopover?: boolean
}) {
  const state = useAclManagement()
  if (!state.allowed) return null
  return (
    <AccountAclControl
      key={JSON.stringify(state.settingsKey)}
      state={state}
      inPopover={inPopover}
    />
  )
}
function AccountAclControl({
  state,
  inPopover,
}: {
  state: ReturnType<typeof useAclManagement>
  inPopover: boolean
}) {
  const { t } = useTranslation('settings')
  const client = useQueryClient()
  const [next, setNext] = useState<boolean | null>(null)
  const update = useMutation({
    mutationFn: (enabled: boolean) => state.api.setEnabled(enabled),
    onSuccess: async (enabled) => {
      client.setQueryData(state.settingsKey, enabled)
      setNext(null)
      toast.success(t('acl.saved'))
      await client.invalidateQueries()
    },
    onError: (error) =>
      toast.error(t('acl.failed'), { description: getErrorMessage(error) }),
  })
  const controls = (
    <>
      <p>{t('acl.accountHint')}</p>
      {state.settings.isError ? (
        <>
          <p role="alert">
            {t('acl.failed')}: {getErrorMessage(state.settings.error)}
          </p>
          <Button
            variant="outline"
            onClick={() => void state.settings.refetch()}
          >
            {t('actions.refresh')}
          </Button>
        </>
      ) : (
        <Button
          variant={state.settings.data ? 'outline' : 'default'}
          size="sm"
          disabled={
            !state.settings.isSuccess ||
            state.settings.isFetching ||
            update.isPending
          }
          onClick={() => setNext(!state.settings.data)}
        >
          {state.settings.isPending
            ? t('loading')
            : t(state.settings.data ? 'acl.disableAction' : 'acl.enableAction')}
        </Button>
      )}
    </>
  )
  return (
    <>
      {inPopover ? (
        <Popover>
          <PopoverTrigger
            openOnHover
            delay={150}
            closeDelay={150}
            render={<Button variant="ghost" size="sm" />}
          >
            <Settings2Icon data-icon="inline-start" />
            {t('acl.page.advanced')}
          </PopoverTrigger>
          <PopoverContent
            align="end"
            sideOffset={8}
            className="w-[min(22rem,calc(100vw-2rem))] gap-3"
          >
            <div className="font-medium">
              {t('acl.accountTitle', { account: state.connection.accountId })}
            </div>
            <div className="flex flex-col items-start gap-3 text-xs text-muted-foreground">
              {controls}
            </div>
          </PopoverContent>
        </Popover>
      ) : (
        <Alert className="bg-transparent">
          <AlertTitle>
            {t('acl.accountTitle', { account: state.connection.accountId })}
          </AlertTitle>
          <AlertDescription className="flex flex-col items-start gap-3">
            {controls}
          </AlertDescription>
        </Alert>
      )}
      <AlertDialog
        open={next !== null}
        onOpenChange={(open) => {
          if (!open && !update.isPending) setNext(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t(next ? 'acl.enableTitle' : 'acl.disableTitle')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t(next ? 'acl.enableWarning' : 'acl.disableWarning', {
                account: state.connection.accountId,
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={update.isPending}>
              {t('actions.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={update.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (next !== null) update.mutate(next)
              }}
            >
              {update.isPending ? t('loading') : t('acl.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
