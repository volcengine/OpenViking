import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LoaderCircleIcon, SparklesIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { isOvClientError } from '#/shared/api/ov-client/index'
import { Button } from '#/shared/ui/button'
import { Switch } from '#/shared/ui/switch'

import {
  fetchAgentEvolutionStatus,
  setAgentEvolutionEnabled,
} from '#/features/admin/account-configuration/experience-api'

/**
 * Admin/root-only configuration row that toggles the selected account Agent
 * Evolution switch (`GET/PATCH /api/v1/admin/accounts/{account_id}/settings`).
 *
 * When disabled, new session commits stop extracting experiences and
 * trajectories, which is the most common reason the impact panel stays empty.
 */
export function EvolutionSettingRow() {
  const { t } = useTranslation('agentExperiencePage')
  const {
    connection,
    connectionRole,
    identityScopeKey,
    isConnectionRoleLoading,
    studioMode,
  } = useAppConnection()
  const queryClient = useQueryClient()

  const canManage =
    studioMode === 'management' &&
    !isConnectionRoleLoading &&
    (connectionRole === 'admin' || connectionRole === 'root')

  const statusQuery = useQuery({
    enabled: canManage && Boolean(connection.accountId),
    queryFn: ({ signal }) =>
      fetchAgentEvolutionStatus(connection.accountId, signal),
    queryKey: ['agent-evolution-status', identityScopeKey],
    staleTime: 30_000,
  })

  const targetAccountId = statusQuery.data?.accountId
  const matchesCurrentAccount =
    Boolean(targetAccountId) && targetAccountId === connection.accountId

  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => {
      if (!matchesCurrentAccount) {
        throw new Error(t('settings.scopeMismatch'))
      }
      return setAgentEvolutionEnabled(connection.accountId, enabled)
    },
    onSuccess: (status) => {
      queryClient.setQueryData(
        ['agent-evolution-status', identityScopeKey],
        status,
      )
      toast.success(
        status.enabled
          ? t('settings.enabledToast')
          : t('settings.disabledToast'),
      )
    },
    onError: (error) => {
      toast.error(
        isOvClientError(error) || error instanceof Error
          ? `${t('settings.toggleFailed')}: ${error.message}`
          : t('settings.toggleFailed'),
      )
    },
  })

  if (!canManage) return null

  return (
    <div className="border-t border-border/60 px-6 py-5">
      <div className="flex min-h-9 items-center justify-between gap-3">
        <span className="flex items-center gap-2.5 text-sm">
          <SparklesIcon className="size-4 shrink-0 text-muted-foreground" />
          {t('settings.toggleLabel')}
        </span>
        <div className="flex items-center gap-2">
          <span role="status" className="text-xs text-muted-foreground">
            {statusQuery.isPending ? (
              <LoaderCircleIcon
                className="size-3.5 animate-spin"
                aria-label={t('settings.loading')}
              />
            ) : statusQuery.isError || !matchesCurrentAccount ? null : (
              t(
                statusQuery.data.enabled
                  ? 'settings.statusEnabled'
                  : 'settings.statusDisabled',
              )
            )}
          </span>
          <Switch
            aria-label={t('settings.toggleLabel')}
            checked={statusQuery.data?.enabled ?? false}
            disabled={
              !statusQuery.isSuccess ||
              !matchesCurrentAccount ||
              toggleMutation.isPending
            }
            onCheckedChange={(checked) => toggleMutation.mutate(checked)}
          />
        </div>
      </div>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">
        {t('settings.toggleHint', { account: connection.accountId })}
      </p>
      {statusQuery.isError ? (
        <div className="mt-2 flex items-center justify-between gap-2 text-xs">
          <p role="alert" className="text-destructive">
            {t('settings.loadFailed')}
          </p>
          <Button
            type="button"
            size="xs"
            variant="outline"
            onClick={() => void statusQuery.refetch()}
          >
            {t('refresh')}
          </Button>
        </div>
      ) : statusQuery.data && !matchesCurrentAccount ? (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {t('settings.scopeMismatch')}
        </p>
      ) : null}
      {toggleMutation.isPending && (
        <p role="status" className="mt-2 text-xs text-muted-foreground">
          {t('settings.pending')}
        </p>
      )}
    </div>
  )
}
