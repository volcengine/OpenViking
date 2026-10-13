import { useQuery } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { useEffect } from 'react'

import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { getOvResult, ovClient } from '#/lib/ov-client'

export type DingTalkResourceOptionsValue = {
  identity: string
  maxBytesMiB: string
  maxDepth: string
  maxNodes: string
}

type DingTalkIdentity = {
  label: string
  name: string
}

const DINGTALK_LIMITS = [
  { field: 'maxNodes', label: 'dingtalk.maxNodes', minimum: 1 },
  { field: 'maxDepth', label: 'dingtalk.maxDepth', minimum: 0 },
  { field: 'maxBytesMiB', label: 'dingtalk.maxBytesMiB', minimum: 1 },
] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function normalizeDingTalkIdentities(
  value: unknown,
): DingTalkIdentity[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!isRecord(item)) return []
    const name = typeof item.name === 'string' ? item.name.trim() : ''
    const label = typeof item.label === 'string' ? item.label.trim() : ''
    return name ? [{ name, label: label || name }] : []
  })
}

export async function fetchDingTalkIdentities(): Promise<DingTalkIdentity[]> {
  const result = await getOvResult<unknown>(
    ovClient.client.get({ url: '/api/v1/resources/dingtalk/identities' }),
  )
  return normalizeDingTalkIdentities(result)
}

function isInteger(value: string, minimum: number): boolean {
  if (!value.trim()) return false
  const number = Number(value)
  return Number.isSafeInteger(number) && number >= minimum
}

export function isValidDingTalkOptions(
  value: DingTalkResourceOptionsValue,
): boolean {
  return (
    Boolean(value.identity) &&
    isInteger(value.maxNodes, 1) &&
    isInteger(value.maxDepth, 0) &&
    isInteger(value.maxBytesMiB, 1) &&
    Number(value.maxBytesMiB) <= Number.MAX_SAFE_INTEGER / 1024 / 1024
  )
}

export function DingTalkResourceOptions({
  disabled,
  onIdentityValidityChange,
  onChange,
  t,
  value,
}: {
  disabled: boolean
  onIdentityValidityChange: (valid: boolean) => void
  onChange: (value: DingTalkResourceOptionsValue) => void
  t: TFunction<'addResource'>
  value: DingTalkResourceOptionsValue
}) {
  const identities = useQuery({
    queryFn: fetchDingTalkIdentities,
    queryKey: ['dingtalk-identities'],
    staleTime: 60_000,
  })
  const options = identities.data ?? []
  const identityIsValid =
    identities.isSuccess &&
    options.some((identity) => identity.name === value.identity)

  useEffect(() => {
    onIdentityValidityChange(identityIsValid)
  }, [identityIsValid, onIdentityValidityChange])

  return (
    <div className="space-y-4 rounded-lg border border-border/60 bg-muted/10 p-4">
      <div className="space-y-1">
        <p className="text-sm font-medium">{t('dingtalk.title')}</p>
        <p className="text-xs leading-5 text-muted-foreground">
          {t('dingtalk.hint')}
        </p>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="add-resource-dingtalk-identity">
          {t('dingtalk.identity')}
        </Label>
        {identities.isLoading ? (
          <p className="text-sm text-muted-foreground" role="status">
            {t('dingtalk.identityLoading')}
          </p>
        ) : identities.isError ? (
          <div className="flex items-center gap-3" role="alert">
            <p className="text-sm text-destructive">
              {t('dingtalk.identityError')}
            </p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => void identities.refetch()}
            >
              {t('dingtalk.retry')}
            </Button>
          </div>
        ) : options.length === 0 ? (
          <p className="text-sm text-destructive" role="alert">
            {t('dingtalk.identityEmpty')}
          </p>
        ) : (
          <select
            id="add-resource-dingtalk-identity"
            aria-label={t('dingtalk.identity')}
            className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs disabled:cursor-not-allowed disabled:opacity-50"
            value={value.identity}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...value, identity: event.target.value })
            }
          >
            <option value="">{t('dingtalk.identityPlaceholder')}</option>
            {options.map((identity) => (
              <option key={identity.name} value={identity.name}>
                {identity.label}
              </option>
            ))}
          </select>
        )}
        <p className="text-xs text-muted-foreground">
          {t('dingtalk.identityHint')}
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        {DINGTALK_LIMITS.map(({ field, label, minimum }) => (
          <div className="grid gap-2" key={field}>
            <Label htmlFor={`add-resource-dingtalk-${field}`}>{t(label)}</Label>
            <Input
              id={`add-resource-dingtalk-${field}`}
              type="number"
              min={minimum}
              step="1"
              value={value[field]}
              disabled={disabled}
              aria-invalid={!isInteger(value[field], minimum)}
              onChange={(event) =>
                onChange({ ...value, [field]: event.target.value })
              }
            />
          </div>
        ))}
      </div>
      {!isValidDingTalkOptions(value) && value.identity ? (
        <p className="text-xs text-destructive" role="alert">
          {t('dingtalk.limitError')}
        </p>
      ) : null}
      <p className="text-xs leading-5 text-muted-foreground">
        {t('dingtalk.limitations')}
      </p>
    </div>
  )
}
