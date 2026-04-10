import { createFileRoute } from '@tanstack/react-router'
import { BugIcon, GaugeIcon, ServerCogIcon } from 'lucide-react'
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
    descriptionKey: 'highlights.systemStatus.description',
    id: 'systemStatus',
    titleKey: 'highlights.systemStatus.title',
  },
  {
    descriptionKey: 'highlights.tasks.description',
    id: 'tasks',
    titleKey: 'highlights.tasks.title',
  },
  {
    descriptionKey: 'highlights.debugMetrics.description',
    id: 'debugMetrics',
    titleKey: 'highlights.debugMetrics.title',
  },
] as const

export const Route = createFileRoute('/operations')({
  component: OperationsRoute,
})

function OperationsRoute() {
  const { t } = useTranslation('operations')

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
                <ServerCogIcon className='size-4' />
                {t('aside.sources.title')}
              </CardTitle>
            </CardHeader>
            <CardContent className='flex flex-wrap gap-2'>
              <Badge variant='outline'>{t('aside.sources.tags.health')}</Badge>
              <Badge variant='outline'>{t('aside.sources.tags.ready')}</Badge>
              <Badge variant='outline'>{t('aside.sources.tags.observer')}</Badge>
              <Badge variant='outline'>{t('aside.sources.tags.tasks')}</Badge>
            </CardContent>
          </Card>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <GaugeIcon className='size-4' />
                {t('aside.quality.title')}
              </CardTitle>
              <CardDescription>
                {t('aside.quality.description')}
              </CardDescription>
            </CardHeader>
          </Card>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <BugIcon className='size-4' />
                {t('aside.debug.title')}
              </CardTitle>
              <CardDescription>
                {t('aside.debug.description')}
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      }
    />
  )
}