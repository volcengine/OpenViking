import { createFileRoute } from '@tanstack/react-router'
import { SearchIcon, UploadIcon } from 'lucide-react'
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
    descriptionKey: 'highlights.tree.description',
    id: 'tree',
    titleKey: 'highlights.tree.title',
  },
  {
    descriptionKey: 'highlights.preview.description',
    id: 'preview',
    titleKey: 'highlights.preview.title',
  },
  {
    descriptionKey: 'highlights.searchModal.description',
    id: 'searchModal',
    titleKey: 'highlights.searchModal.title',
  },
] as const

export const Route = createFileRoute('/resources')({
  component: ResourcesRoute,
})

function ResourcesRoute() {
  const { t } = useTranslation('resources')

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
        <Card size='sm' className='bg-background/80'>
          <CardHeader>
            <CardTitle className='flex items-center gap-2 text-sm'>
              <SearchIcon className='size-4' />
              {t('aside.plan.title')}
            </CardTitle>
            <CardDescription>
              {t('aside.plan.description')}
            </CardDescription>
          </CardHeader>
          <CardContent className='flex flex-wrap gap-2'>
            <Badge variant='outline'>{t('aside.tags.tree')}</Badge>
            <Badge variant='outline'>{t('aside.tags.preview')}</Badge>
            <Badge variant='outline'>{t('aside.tags.relations')}</Badge>
            <Badge variant='outline'>{t('aside.tags.searchModal')}</Badge>
            <Badge variant='outline'>{t('aside.tags.importExport')}</Badge>
            <Badge variant='outline'>{t('aside.tags.reindex')}</Badge>
            <Badge variant='secondary' className='mt-2'>
              <UploadIcon className='size-3' />
              {t('aside.tags.uploadFlow')}
            </Badge>
          </CardContent>
        </Card>
      }
    />
  )
}