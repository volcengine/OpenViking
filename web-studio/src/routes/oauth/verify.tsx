import { useTranslation } from 'react-i18next'
import { createFileRoute } from '@tanstack/react-router'

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import { CrossDeviceVerifyForm } from '#/components/cross-device-verify-form'

export const Route = createFileRoute('/oauth/verify')({
  component: VerifyPage,
})

function VerifyPage() {
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
