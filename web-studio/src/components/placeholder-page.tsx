import type { ReactNode } from 'react'
import { ArrowRightIcon } from 'lucide-react'

import { Badge } from '#/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'

type PlaceholderPageProps = {
  description: string
  highlights: Array<{ title: string; description: string }>
  kicker: string
  title: string
  aside?: ReactNode
}

export function PlaceholderPage({ aside, description, highlights, kicker, title }: PlaceholderPageProps) {
  return (
    <div className='grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]'>
      <div className='grid gap-6'>
        <Card className='overflow-hidden bg-[linear-gradient(135deg,rgba(71,126,255,0.08),rgba(255,255,255,0.95)_55%)]'>
          <CardHeader className='gap-3'>
            <Badge variant='outline'>{kicker}</Badge>
            <div className='grid gap-2'>
              <CardTitle className='text-2xl'>{title}</CardTitle>
              <CardDescription className='max-w-3xl text-sm leading-6'>
                {description}
              </CardDescription>
            </div>
          </CardHeader>
        </Card>

        <div className='grid gap-4 md:grid-cols-2 xl:grid-cols-3'>
          {highlights.map((item) => (
            <Card key={item.title} size='sm' className='bg-background/80'>
              <CardHeader className='gap-2'>
                <CardTitle className='text-sm'>{item.title}</CardTitle>
                <CardDescription className='leading-6'>{item.description}</CardDescription>
              </CardHeader>
              <CardContent className='flex items-center gap-2 text-xs text-muted-foreground'>
                <ArrowRightIcon className='size-3.5' />
                这一版先提供布局占位，后续接入具体功能。
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      <div className='grid gap-4'>
        {aside ?? (
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='text-sm'>当前状态</CardTitle>
              <CardDescription>
                页面骨架已经落位，功能区在后续迭代中逐步填充。
              </CardDescription>
            </CardHeader>
          </Card>
        )}
      </div>
    </div>
  )
}