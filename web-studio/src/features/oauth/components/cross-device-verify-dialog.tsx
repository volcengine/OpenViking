import { useTranslation } from 'react-i18next'

import { CrossDeviceVerifyForm } from '#/features/oauth/components/cross-device-verify-form'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'

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
        <CrossDeviceVerifyForm
          secondaryAction={
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t('action.cancel', { ns: 'common' })}
            </Button>
          }
        />
      </DialogContent>
    </Dialog>
  )
}
