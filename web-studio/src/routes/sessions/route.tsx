import { createFileRoute } from '@tanstack/react-router'
import { ArchiveIcon, BrainCircuitIcon, MessagesSquareIcon } from 'lucide-react'
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
    descriptionKey: 'highlights.sessionList.description',
    id: 'sessionList',
    titleKey: 'highlights.sessionList.title',
  },
  {
    descriptionKey: 'highlights.context.description',
    id: 'context',
    titleKey: 'highlights.context.title',
  },
  {
    descriptionKey: 'highlights.memory.description',
    id: 'memory',
    titleKey: 'highlights.memory.title',
  },
] as const

export const Route = createFileRoute('/sessions')({
  component: SessionsRoute,
})

function SessionsRoute() {
  const { t } = useTranslation('sessions')

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
                <MessagesSquareIcon className='size-4' />
                {t('aside.layout.title')}
              </CardTitle>
              <CardDescription>
                {t('aside.layout.description')}
              </CardDescription>
            </CardHeader>
            <CardContent className='flex flex-wrap gap-2'>
              <Badge variant='outline'>{t('aside.layout.tags.messages')}</Badge>
              <Badge variant='outline'>{t('aside.layout.tags.contextSidebar')}</Badge>
              <Badge variant='outline'>{t('aside.layout.tags.archive')}</Badge>
              <Badge variant='outline'>{t('aside.layout.tags.taskStatus')}</Badge>
            </CardContent>
          </Card>

          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <ArchiveIcon className='size-4' />
                {t('aside.memory.title')}
              </CardTitle>
            </CardHeader>
            <CardContent className='flex flex-wrap gap-2'>
              <Badge variant='outline'>{t('aside.memory.tags.commit')}</Badge>
              <Badge variant='outline'>{t('aside.memory.tags.extract')}</Badge>
              <Badge variant='outline'>{t('aside.memory.tags.sessionStats')}</Badge>
              <Badge variant='outline'>{t('aside.memory.tags.aggregateStats')}</Badge>
              <Badge variant='secondary'>{t('aside.memory.tags.standalone')}</Badge>
            </CardContent>
          </Card>

          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <BrainCircuitIcon className='size-4' />
                {t('aside.bot.title')}
              </CardTitle>
              <CardDescription>
                {t('aside.bot.description')}
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      }
    />
  )
}