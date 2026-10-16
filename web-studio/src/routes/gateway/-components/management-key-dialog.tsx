import * as React from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '#/components/ui/dialog'
import { getAccess, toGatewayError } from '../-lib/api'
import {
  MANAGEMENT_UNLOCK_EVENT,
  setManagementKey,
} from '../-lib/management-key'
import { useGateway } from '../-lib/use-gateway'
import { gatewayErrorMessage } from '../-lib/localize'
import { SettingField } from './setting-field'

const MANAGEMENT_CONFIGS = [
  'ov.conf: gateway.management_key',
  'gateway.conf: management_key',
]

export function ManagementKeyDialog() {
  const { t } = useTranslation('gateway')
  const { connection } = useGateway()
  const [open, setOpen] = React.useState(false)
  const [key, setKey] = React.useState('')
  const [error, setError] = React.useState('')
  React.useEffect(() => {
    const show = () => {
      setKey('')
      setError('')
      setOpen(true)
    }
    window.addEventListener(MANAGEMENT_UNLOCK_EVENT, show)
    return () => window.removeEventListener(MANAGEMENT_UNLOCK_EVENT, show)
  }, [])
  const unlock = useMutation({
    mutationFn: (candidate: typeof connection) => getAccess(candidate),
    // Storing the key changes the query scope, so gateway data reloads with it.
    onSuccess: (access, candidate) => {
      if (!access.can_write_objects) {
        setError(t('management.invalid'))
        return
      }
      setManagementKey(candidate.baseUrl, candidate.managementKey ?? '')
      setOpen(false)
      setKey('')
      toast.success(t('management.unlocked'))
    },
    onError: (failure) =>
      setError(
        toGatewayError(failure).reason === 'management_key_invalid'
          ? t('management.invalid')
          : gatewayErrorMessage(t, failure),
      ),
  })
  const wrongKey = key.trim().startsWith('ovgw_')
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('management.title')}</DialogTitle>
          <DialogDescription>{t('management.description')}</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (key.trim() && !wrongKey)
              unlock.mutate({ ...connection, managementKey: key.trim() })
          }}
        >
          <details className="text-sm">
            <summary className="cursor-pointer">
              {t('management.where')}
            </summary>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
              {MANAGEMENT_CONFIGS.map((config) => (
                <li key={config}>
                  <code>{config}</code>
                </li>
              ))}
              <li>
                {t('management.environment')}{' '}
                <code>OPENVIKING_GATEWAY_MANAGEMENT_KEY</code>
              </li>
              <li>{t('management.prefix')}</li>
            </ul>
          </details>
          <SettingField
            label={t('management.label')}
            htmlFor="gateway-management-key"
            error={wrongKey ? t('management.wrongKey') : error}
            description={t('management.session')}
          >
            <Input
              id="gateway-management-key"
              autoFocus
              type="password"
              autoComplete="off"
              value={key}
              onChange={(event) => {
                setKey(event.target.value)
                setError('')
              }}
            />
          </SettingField>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
            >
              {t('actions.cancel')}
            </Button>
            <Button
              type="submit"
              disabled={!key.trim() || wrongKey || unlock.isPending}
            >
              {t('management.unlock')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
