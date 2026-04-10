import { createFileRoute } from '@tanstack/react-router'
import { KeyRoundIcon, ShieldCheckIcon, UsersIcon } from 'lucide-react'

import { PlaceholderPage } from '#/components/placeholder-page'
import { Badge } from '#/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'

export const Route = createFileRoute('/admin')({
  component: AdminRoute,
})

function AdminRoute() {
  return (
    <PlaceholderPage
      kicker='管理面'
      title='账号、用户与密钥管理'
      description='管理入口用于承接多租户账号、用户、角色与密钥操作。开发模式下这些接口并不成立，因此导航会自动隐藏。'
      highlights={[
        {
          title: '账号管理',
          description: '后续接入 account create/list/delete。',
        },
        {
          title: '用户管理',
          description: '后续接入 user register/list/delete 和 role 调整。',
        },
        {
          title: '密钥轮换',
          description: '后续接入 regenerate key，并对 root/admin 权限做前端提示。',
        },
      ]}
      aside={
        <div className='grid gap-4'>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <UsersIcon className='size-4' />
                预期对象
              </CardTitle>
            </CardHeader>
            <CardContent className='flex flex-wrap gap-2'>
              <Badge variant='outline'>Accounts</Badge>
              <Badge variant='outline'>Users</Badge>
              <Badge variant='outline'>Roles</Badge>
            </CardContent>
          </Card>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <KeyRoundIcon className='size-4' />
                权限前提
              </CardTitle>
              <CardDescription>
                当前只做骨架，具体权限判断和空态提示在后续接入接口时完善。
              </CardDescription>
            </CardHeader>
          </Card>
          <Card size='sm' className='bg-background/80'>
            <CardHeader>
              <CardTitle className='flex items-center gap-2 text-sm'>
                <ShieldCheckIcon className='size-4' />
                开发模式兼容
              </CardTitle>
              <CardDescription>
                检测到开发模式时会隐藏该导航项，避免进入不可用骨架。
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      }
    />
  )
}