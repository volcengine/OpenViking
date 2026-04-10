import { createFileRoute } from '@tanstack/react-router'
import { SearchIcon, UploadIcon } from 'lucide-react'

import { PlaceholderPage } from '#/components/placeholder-page'
import { Badge } from '#/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'

export const Route = createFileRoute('/resources')({
  component: ResourcesRoute,
})

function ResourcesRoute() {
  return (
    <PlaceholderPage
      kicker='资源工作区'
      title='资源浏览与检索'
      description='这里会承载资源树、内容预览、关系查看、导入导出和检索 modal。当前版本先把整体骨架搭起来，方便后续逐块接功能。'
      highlights={[
        {
          title: '树状浏览',
          description: '预留左侧资源树和目录导航，后续接 fs.ls、fs.tree、content.read。',
        },
        {
          title: '内容预览',
          description: '预留 abstract、overview、download 和 reindex 的交互位。',
        },
        {
          title: '检索 modal',
          description: '检索不会独立成一级页面，而会从当前资源视图中弹出。',
        },
      ]}
      aside={
        <Card size='sm' className='bg-background/80'>
          <CardHeader>
            <CardTitle className='flex items-center gap-2 text-sm'>
              <SearchIcon className='size-4' />
              本页规划
            </CardTitle>
            <CardDescription>
              将浏览与检索收敛为一条操作流。
            </CardDescription>
          </CardHeader>
          <CardContent className='flex flex-wrap gap-2'>
            <Badge variant='outline'>树状浏览</Badge>
            <Badge variant='outline'>内容预览</Badge>
            <Badge variant='outline'>关系查看</Badge>
            <Badge variant='outline'>检索 modal</Badge>
            <Badge variant='outline'>导入导出</Badge>
            <Badge variant='outline'>重建索引</Badge>
            <Badge variant='secondary' className='mt-2'>
              <UploadIcon className='size-3' />
              后续接入资源上传与 pack 流程
            </Badge>
          </CardContent>
        </Card>
      }
    />
  )
}