import { createFileRoute } from '@tanstack/react-router'
import { BugIcon, GaugeIcon, ServerCogIcon } from 'lucide-react'

import { PlaceholderPage } from '#/components/placeholder-page'
import { Badge } from '#/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'

export const Route = createFileRoute('/operations')({
  component: OperationsRoute,
})

function OperationsRoute() {
  return (
    <PlaceholderPage
      kicker='运维面板'
      title='服务状态与后台任务'
      description='这里用于承载 system、observer、tasks、metrics 与 debug 等运行时信息，和会话工作区里的业务操作面保持分离。'
      highlights={[
        {
          title: '系统状态',
          description: '聚合 health、ready、observer.system 和 system.status。',
        },
        {
          title: '后台任务',
          description: '提供 session commit、资源 reindex 等后台任务的轮询与追踪。',
        },
        {
          title: '调试指标',
          description: '承接 metrics、vector debug 与其他运行时调试入口。',
        },
      ]}
      aside={
        <div className='grid gap-4'>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <ServerCogIcon className='size-4' />
                数据源规划
              </CardTitle>
            </CardHeader>
            <CardContent className='flex flex-wrap gap-2'>
              <Badge variant='outline'>/health</Badge>
              <Badge variant='outline'>/ready</Badge>
              <Badge variant='outline'>observer.*</Badge>
              <Badge variant='outline'>tasks</Badge>
            </CardContent>
          </Card>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <GaugeIcon className='size-4' />
                指标与质量
              </CardTitle>
              <CardDescription>
                Prometheus、retrieval 和健康指标会在后续接入。
              </CardDescription>
            </CardHeader>
          </Card>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <BugIcon className='size-4' />
                Debug
              </CardTitle>
              <CardDescription>
                这里只放系统级调试，不与资源或会话页面混用。
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      }
    />
  )
}