import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LoaderCircleIcon, SettingsIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from '#/components/ui/popover'
import { Switch } from '#/components/ui/switch'
import { useAppConnection } from '#/hooks/use-app-connection'
import { isOvClientError } from '#/lib/ov-client'

import {
  fetchAgentEvolutionStatus,
  setAgentEvolutionEnabled,
} from '../-lib/api'

/**
 * Admin/root-only settings popover that toggles the deployment Agent
 * Evolution switch (`GET/PUT /api/v1/admin/agent-evolution`).
 *
 * When disabled, new session commits stop extracting experiences and
 * trajectories, which is the most common reason the impact panel stays empty.
 */
export function EvolutionSettingsPopover() {
  const { t } = useTranslation('agentExperiencePage')
  const { connectionRole, identityScopeKey, isConnectionRoleLoading } =
    useAppConnection()
  const queryClient = useQueryClient()

  const canManage =
    !isConnectionRoleLoading &&
    (connectionRole === 'admin' || connectionRole === 'root')

  const statusQuery = useQuery({
    enabled: canManage,
    queryFn: ({ signal }) => fetchAgentEvolutionStatus(signal),
    queryKey: ['agent-evolution-status', identityScopeKey],
    staleTime: 30_000,
  })

  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => setAgentEvolutionEnabled(enabled),
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
    <Popover>
      <PopoverTrigger
        className="inline-flex h-8 items-center gap-1.5 rounded-[min(var(--radius-md),10px)] border border-border bg-background px-2.5 text-sm font-medium shadow-xs transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        aria-label={t('settings.title')}
      >
        <SettingsIcon className="size-3.5" />
        {t('settings.title')}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <PopoverHeader>
          <PopoverTitle>{t('settings.title')}</PopoverTitle>
          <PopoverDescription>{t('settings.description')}</PopoverDescription>
        </PopoverHeader>
        <div className="grid gap-3 px-1 pb-1">
          {statusQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircleIcon className="size-3.5 animate-spin" />
              {t('settings.loading')}
            </div>
          ) : statusQuery.isError ? (
            <div className="grid gap-2 text-sm">
              <p className="text-muted-foreground">
                {t('settings.loadFailed')}
              </p>
              <Button
                type="button"
                size="xs"
                variant="outline"
                className="w-fit"
                onClick={() => void statusQuery.refetch()}
              >
                {t('refresh')}
              </Button>
            </div>
          ) : statusQuery.data ? (
            <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5">
              <div className="grid gap-0.5">
                <span className="text-sm font-medium">
                  {statusQuery.data.enabled
                    ? t('settings.statusEnabled')
                    : t('settings.statusDisabled')}
                </span>
                <span className="text-xs text-muted-foreground">
                  {statusQuery.data.enabled
                    ? t('settings.statusEnabledHint')
                    : t('settings.statusDisabledHint')}
                </span>
              </div>
              <Switch
                aria-label={t('settings.title')}
                checked={statusQuery.data.enabled}
                disabled={toggleMutation.isPending}
                onCheckedChange={(checked) => toggleMutation.mutate(checked)}
              />
            </div>
          ) : null}
          {toggleMutation.isPending ? (
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <LoaderCircleIcon className="size-3 animate-spin" />
              {t('settings.pending')}
            </p>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  )
}
