import { useTranslation } from 'react-i18next'

import { CrossDeviceVerifyForm } from '#/features/oauth/components/cross-device-verify-form'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/shared/ui/card'

export function VerifyPage() {
  const { t } = useTranslation('oauth')

  return (
    <div className="flex min-h-[60vh] w-full items-center justify-center px-4 py-8">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>{t('verify.title')}</CardTitle>
          <CardDescription>{t('verify.description')}</CardDescription>
        </CardHeader>
        <CardContent>
          <CrossDeviceVerifyForm />
        </CardContent>
      </Card>
    </div>
  )
}
