import { createFileRoute } from '@tanstack/react-router'
import { KeyRoundIcon, ShieldCheckIcon, UsersIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { PlaceholderPage } from '#/components/placeholder-page'
import { Badge } from '#/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'

const HIGHLIGHTS = [
  {
    descriptionKey: 'highlights.accountManagement.description',
    id: 'accountManagement',
    titleKey: 'highlights.accountManagement.title',
  },
  {
    descriptionKey: 'highlights.userManagement.description',
    id: 'userManagement',
    titleKey: 'highlights.userManagement.title',
  },
  {
    descriptionKey: 'highlights.keyRotation.description',
    id: 'keyRotation',
    titleKey: 'highlights.keyRotation.title',
  },
] as const

export const Route = createFileRoute('/admin')({
  component: AdminRoute,
})

function AdminRoute() {
  const { t } = useTranslation('admin')

  return (
    <PlaceholderPage
      kicker={t('page.kicker')}
      title={t('page.title')}
      description={t('page.description')}
      highlights={HIGHLIGHTS.map((item) => ({
        description: t(item.descriptionKey),
        title: t(item.titleKey),
      }))}
      aside={
        <div className='grid gap-4'>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <UsersIcon className='size-4' />
                {t('aside.subjects.title')}
              </CardTitle>
            </CardHeader>
            <CardContent className='flex flex-wrap gap-2'>
              <Badge variant='outline'>{t('aside.subjects.tags.accounts')}</Badge>
              <Badge variant='outline'>{t('aside.subjects.tags.users')}</Badge>
              <Badge variant='outline'>{t('aside.subjects.tags.roles')}</Badge>
            </CardContent>
          </Card>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <KeyRoundIcon className='size-4' />
                {t('aside.permissions.title')}
              </CardTitle>
              <CardDescription>
                {t('aside.permissions.description')}
              </CardDescription>
            </CardHeader>
          </Card>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <ShieldCheckIcon className='size-4' />
                {t('aside.compatibility.title')}
              </CardTitle>
              <CardDescription>
                {t('aside.compatibility.description')}
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      }
    />
  )
}