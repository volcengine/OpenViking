import * as React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { KeyRoundIcon, LoaderCircleIcon, RefreshCwIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import { useAppConnection } from '#/hooks/use-app-connection'
import {
  fetchAccountModelConfiguration,
  patchAccountModelConfiguration,
} from '#/lib/admin'
import type { AdminConnection } from '#/lib/admin'
import type {
  AccountModelSection,
  AccountModelSectionConfig,
} from '#/lib/account-model-config'
import { resolveStudioManagementCapabilities } from '#/lib/studio-permissions'
import { getErrorMessage } from '#/routes/users/-lib/error'
import { AccountModelSectionCard } from './-components/account-model-section-card'

export const Route = createFileRoute('/models')({
  component: AccountModelsPage,
})

const MODEL_SECTIONS: readonly AccountModelSection[] = ['vlm', 'query_planner']

function AccountModelsPage() {
  const { t } = useTranslation('settings')
  const queryClient = useQueryClient()
  const {
    connection,
    connectionRole,
    identityScopeKey,
    isConnectionRoleLoading,
    serverMode,
  } = useAppConnection()
  const { canManageAccounts } = resolveStudioManagementCapabilities({
    hasControlCredential: Boolean(connection.adminApiKey.trim()),
    isRoleLoading: isConnectionRoleLoading,
    role: connectionRole,
    serverMode,
  })
  const adminConnection = React.useMemo<AdminConnection>(
    () => ({
      accountId: connection.accountId,
      apiKey: connection.adminApiKey,
      baseUrl: connection.baseUrl,
      userId: connection.userId,
    }),
    [
      connection.accountId,
      connection.adminApiKey,
      connection.baseUrl,
      connection.userId,
    ],
  )
  const queryKey = [
    'account-model-configuration',
    identityScopeKey,
    connection.accountId,
  ]
  const configuration = useQuery({
    enabled: canManageAccounts && Boolean(connection.accountId),
    queryFn: () =>
      fetchAccountModelConfiguration(adminConnection, connection.accountId),
    queryKey,
    retry: false,
  })

  async function updateSection(
    section: AccountModelSection,
    value: AccountModelSectionConfig | null,
  ): Promise<void> {
    const result = await patchAccountModelConfiguration(
      adminConnection,
      connection.accountId,
      { [section]: value },
    )
    queryClient.setQueryData(queryKey, result)
    toast.success(
      t(value ? 'models.toast.saved' : 'models.toast.inheritanceRestored'),
    )
  }

  if (isConnectionRoleLoading) {
    return (
      <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground">
        <LoaderCircleIcon className="size-4 animate-spin" />
        {t('loading')}
      </div>
    )
  }

  if (!canManageAccounts) {
    return (
      <Card className="mx-auto mt-10 w-full max-w-xl">
        <CardHeader className="items-center text-center">
          <div className="mb-2 flex size-12 items-center justify-center rounded-xl border bg-muted/40 text-muted-foreground">
            <KeyRoundIcon className="size-5" />
          </div>
          <CardTitle>{t('models.accessDenied.title')}</CardTitle>
          <CardDescription>
            {t('models.accessDenied.description')}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex justify-center">
          <Button nativeButton={false} render={<Link to="/settings" />}>
            <KeyRoundIcon />
            {t('models.accessDenied.action')}
          </Button>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex min-w-0 flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('models.title')}
          </h1>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            {t('models.description', { account: connection.accountId })}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          disabled={configuration.isFetching}
          onClick={() => void configuration.refetch()}
        >
          <RefreshCwIcon
            className={configuration.isFetching ? 'animate-spin' : undefined}
          />
          {t('actions.refresh')}
        </Button>
      </header>

      {configuration.isPending ? (
        <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground">
          <LoaderCircleIcon className="size-4 animate-spin" />
          {t('models.loading')}
        </div>
      ) : configuration.isError ? (
        <Card>
          <CardHeader>
            <CardTitle>{t('models.errors.loadFailed')}</CardTitle>
            <CardDescription>
              {getErrorMessage(configuration.error)}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              type="button"
              variant="outline"
              onClick={() => void configuration.refetch()}
            >
              <RefreshCwIcon />
              {t('actions.retry')}
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-5 xl:grid-cols-2">
          {MODEL_SECTIONS.map((section) => (
            <AccountModelSectionCard
              key={section}
              section={section}
              config={configuration.data.settings[section]}
              onSave={(value) => updateSection(section, value)}
              onReset={() => updateSection(section, null)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
