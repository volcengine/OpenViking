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
import { CrossDeviceVerifyForm } from '#/components/cross-device-verify-form'

export function CrossDeviceVerifyDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation(['oauth', 'common'])
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('verify.title', { ns: 'oauth' })}</DialogTitle>
          <DialogDescription>
            {t('verify.description', { ns: 'oauth' })}
          </DialogDescription>
        </DialogHeader>
        <CrossDeviceVerifyForm />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('action.cancel', { ns: 'common' })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
