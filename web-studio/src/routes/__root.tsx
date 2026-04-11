import { RotateCcwIcon, TriangleAlertIcon } from 'lucide-react'
import { Outlet, createRootRoute } from '@tanstack/react-router'
import type { ErrorComponentProps } from '@tanstack/react-router'
import { TanStackRouterDevtoolsPanel } from '@tanstack/react-router-devtools'
import { TanStackDevtools } from '@tanstack/react-devtools'
import { useTranslation } from 'react-i18next'

import { AppShell } from '#/components/app-shell'
import { Button } from '#/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '#/components/ui/card'
import '../styles.css'

export const Route = createRootRoute({
  component: RootComponent,
  errorComponent: RootErrorComponent,
})

function RootComponent() {
  return (
    <>
      <AppShell>
        <Outlet />
      </AppShell>
      <TanStackDevtools
        config={{
          position: 'bottom-right',
        }}
        plugins={[
          {
            name: 'TanStack Router',
            render: <TanStackRouterDevtoolsPanel />,
          },
        ]}
      />
    </>
  )
}

function RootErrorComponent({ error, reset }: ErrorComponentProps) {
  const { t } = useTranslation('common')
  const message = error instanceof Error ? error.message : 'Unknown error'

  return (
    <>
      <div className='flex min-h-svh items-center justify-center bg-[radial-gradient(circle_at_top_left,rgba(242,105,38,0.12),transparent_28%),linear-gradient(180deg,rgba(255,248,245,0.92)_0%,rgba(255,255,255,1)_32%)] px-4 py-10'>
        <Card className='w-full max-w-2xl border-destructive/20 bg-background/95 shadow-lg'>
          <CardHeader className='gap-3'>
            <div className='flex items-center gap-3 text-destructive'>
              <TriangleAlertIcon className='size-5' />
              <CardTitle>{t('errorBoundary.title')}</CardTitle>
            </div>
            <CardDescription>
              {t('errorBoundary.description')}
            </CardDescription>
          </CardHeader>
          <CardContent className='grid gap-4'>
            <div className='rounded-lg border border-destructive/15 bg-destructive/5 px-4 py-3 font-mono text-sm text-foreground'>
              {message}
            </div>
          </CardContent>
          <CardFooter className='justify-end gap-2'>
            <Button variant='outline' onClick={() => window.location.reload()}>
              {t('errorBoundary.reload')}
            </Button>
            <Button onClick={() => reset()}>
              <RotateCcwIcon />
              {t('errorBoundary.retry')}
            </Button>
          </CardFooter>
        </Card>
      </div>
      <TanStackDevtools
        config={{
          position: 'bottom-right',
        }}
        plugins={[
          {
            name: 'TanStack Router',
            render: <TanStackRouterDevtoolsPanel />,
          },
        ]}
      />
    </>
  )
}
