import { createFileRoute } from '@tanstack/react-router'
import { ArchiveIcon, BrainCircuitIcon, MessagesSquareIcon } from 'lucide-react'

import { PlaceholderPage } from '#/components/placeholder-page'
import { Badge } from '#/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'

export const Route = createFileRoute('/sessions')({
  component: SessionsRoute,
})

function SessionsRoute() {
  return (
    <PlaceholderPage
      kicker='会话工作区'
      title='会话、上下文与记忆沉淀'
      description='会话页不是监视大屏，而是承载消息、上下文装配、archive、记忆提取和异步任务的工作区。后续如开启 Bot，也会作为这里的可选交互子区接入。'
      highlights={[
        {
          title: 'Session 列表',
          description: '占位左侧 session 列表与切换能力，后续接 sessions create/list/get/delete。',
        },
        {
          title: '上下文装配',
          description: '预留 get_session_context 和 archive 展开区域，用于展示 assembled payload。',
        },
        {
          title: '记忆区',
          description: '首版把 extraction stats、commit 结果与 memory 入口收纳到会话页内。',
        },
      ]}
      aside={
        <div className='grid gap-4'>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <MessagesSquareIcon className='size-4' />
                主区块
              </CardTitle>
              <CardDescription>
                左中右三栏布局会在后续迭代中逐步落地。
              </CardDescription>
            </CardHeader>
            <CardContent className='flex flex-wrap gap-2'>
              <Badge variant='outline'>消息与操作</Badge>
              <Badge variant='outline'>上下文侧栏</Badge>
              <Badge variant='outline'>Archive 历史</Badge>
              <Badge variant='outline'>Task 状态</Badge>
            </CardContent>
          </Card>

          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <ArchiveIcon className='size-4' />
                记忆沉淀
              </CardTitle>
            </CardHeader>
            <CardContent className='flex flex-wrap gap-2'>
              <Badge variant='outline'>Commit</Badge>
              <Badge variant='outline'>Extract</Badge>
              <Badge variant='outline'>Session Stats</Badge>
              <Badge variant='outline'>Aggregate Memory Stats</Badge>
              <Badge variant='secondary'>记忆先不单列一级入口</Badge>
            </CardContent>
          </Card>

          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <BrainCircuitIcon className='size-4' />
                Bot 集成
              </CardTitle>
              <CardDescription>
                仅在服务端启用时作为可选交互区接入，不影响会话页本体成立。
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      }
    />
  )
}