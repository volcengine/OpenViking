import { useState } from 'react'
import { ArrowLeftIcon, CheckIcon, PlusIcon, SearchIcon } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { useAclManagement } from '#/hooks/use-acl-management'
import { ResourceAclIdentityRecovery } from './resource-acl-identity-recovery'
import { isOvClientError } from '#/lib/ov-client'
import { Button } from '#/components/ui/button'
import { Badge } from '#/components/ui/badge'
import { Input } from '#/components/ui/input'
import { Field, FieldGroup, FieldLabel } from '#/components/ui/field'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectGroup,
  SelectItem,
} from '#/components/ui/select'
import { Alert, AlertTitle, AlertDescription } from '#/components/ui/alert'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from '#/components/ui/alert-dialog'
import { isSharedAclTarget } from '#/lib/resource-acl'
import type { AclChange, AclEntry, AclLevel } from '#/lib/resource-acl'
import { fetchAdminGroups, fetchAdminUsersPage } from '#/lib/admin'
import { getErrorMessage } from '#/routes/users/-lib/error'
import { useDebouncedValue } from '#/routes/resources/-hooks/viking-fm'

const subjectTypes = ['user', 'group', 'everyone'] as const
const levels: AclLevel[] = ['read', 'write', 'manage']
type PanelChange = AclChange | { kind: 'grantMany'; entries: AclEntry[] }

export function ResourcePermissionsPanel({ uri }: { uri: string }) {
  const state = useAclManagement()
  const { t } = useTranslation('settings')
  const client = useQueryClient()
  const [confirm, setConfirm] = useState<AclChange | null>(null)
  const [grantOpen, setGrantOpen] = useState(false)
  const key = ['resource-acl', state.aclIdentityScopeKey, uri]
  const report = useQuery({
    queryKey: key,
    queryFn: () => state.api.get(uri),
    enabled: state.allowed && isSharedAclTarget(uri),
    retry: false,
  })
  const mutation = useMutation({
    mutationFn: async (change: PanelChange) => {
      if (change.kind !== 'grantMany') return state.api.change(uri, change)
      let updated
      for (const entry of change.entries) {
        updated = await state.api.change(uri, { kind: 'grant', ...entry })
      }
      return updated
    },
    onSuccess: async (updated) => {
      client.setQueryData(key, updated)
      setConfirm(null)
      setGrantOpen(false)
      toast.success(t('acl.saved'))
      // Ancestor grants affect descendant reports and every cached resource listing/search.
      await client.invalidateQueries()
    },
    onError: async (error) => {
      toast.error(t('acl.failed'), { description: getErrorMessage(error) })
      // Earlier grants in a batch may already have succeeded.
      await client.invalidateQueries()
    },
  })
  const writable =
    state.allowed &&
    state.settings.data === true &&
    state.settings.isSuccess &&
    !state.settings.isFetching &&
    report.isSuccess &&
    !report.isFetching &&
    !mutation.isPending
  const permissionDenied =
    isOvClientError(report.error) &&
    (report.error.statusCode === 403 ||
      report.error.code === 'PERMISSION_DENIED')
  const restricted = report.data?.acl_mode === 'restricted'
  const defaultShared = report.data?.acl_mode === 'none'
  const modeChange = confirm?.kind === 'mode'
  return (
    <section className="min-w-0">
      <div className="flex flex-col gap-4">
        {report.isPending ? (
          <p role="status">{t('loading')}</p>
        ) : report.isError && permissionDenied ? (
          <ResourceAclIdentityRecovery
            onRetry={() => void report.refetch()}
            error={getErrorMessage(report.error)}
          />
        ) : report.isError ? (
          <>
            <Alert variant="destructive">
              <AlertTitle>{t('acl.loadFailed')}</AlertTitle>
              <AlertDescription>
                <p>{t('acl.retryHint')}</p>
                <p>{getErrorMessage(report.error)}</p>
                <Button variant="outline" onClick={() => void report.refetch()}>
                  {t('actions.refresh')}
                </Button>
              </AlertDescription>
            </Alert>
          </>
        ) : grantOpen ? (
          <div className="space-y-5">
            <Button
              variant="ghost"
              size="sm"
              className="-ml-2"
              onClick={() => setGrantOpen(false)}
            >
              <ArrowLeftIcon data-icon="inline-start" />
              {t('acl.backToGrants')}
            </Button>
            <div className="border-b pb-3 text-sm font-medium">
              {t('acl.addGrant')}
            </div>
            <GrantForm
              state={state}
              disabled={!writable}
              onGrant={(entries) =>
                mutation.mutate({ kind: 'grantMany', entries })
              }
            />
          </div>
        ) : (
          <div className="space-y-4">
            <section className="min-w-0">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3">
                <h2 className="text-sm font-medium">
                  {t('acl.peopleWithAccess')}
                </h2>
                {!defaultShared && (
                  <Button
                    size="sm"
                    disabled={!writable}
                    onClick={() => setGrantOpen(true)}
                  >
                    <PlusIcon data-icon="inline-start" />
                    {t('acl.addGrant')}
                  </Button>
                )}
              </div>
              {defaultShared && (
                <div className="flex flex-wrap items-center justify-between gap-3 border-b py-4">
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{t('acl.everyone')}</p>
                    <p className="text-xs text-muted-foreground">
                      {t('acl.defaultRule')}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!writable}
                    onClick={() =>
                      setConfirm({ kind: 'mode', mode: 'restricted' })
                    }
                  >
                    {t('acl.limitAccess')}
                  </Button>
                </div>
              )}
              {!defaultShared &&
                report.data.direct_entries.length === 0 &&
                (restricted || report.data.inherited_entries.length === 0) && (
                  <p className="py-6 text-sm text-muted-foreground">
                    {t('acl.onlyAdmins')}
                  </p>
                )}
              {report.data.direct_entries.map((entry) => (
                <div
                  key={entry.principal}
                  className="flex flex-wrap items-center gap-3 border-b py-3 last:border-b-0"
                >
                  <div className="min-w-0 flex-1">
                    <p className="break-all text-sm font-medium">
                      {principalLabel(entry.principal, t('acl.everyone'))}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {t('acl.directSource')} ·{' '}
                      {t(`acl.subjects.${principalType(entry.principal)}`)}
                    </p>
                  </div>
                  <LevelSelect
                    value={entry.level}
                    disabled={!writable}
                    label={t('acl.levelFor', { principal: entry.principal })}
                    onChange={(level) =>
                      mutation.mutate({
                        kind: 'grant',
                        principal: entry.principal,
                        level,
                      })
                    }
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={!writable}
                    onClick={() =>
                      setConfirm({
                        kind: 'revoke',
                        principal: entry.principal,
                      })
                    }
                  >
                    {t('acl.remove')}
                  </Button>
                </div>
              ))}
              {!restricted &&
                !defaultShared &&
                report.data.inherited_entries.map((entry) => (
                  <div
                    key={`parent:${entry.principal}`}
                    className="flex items-center gap-3 border-b py-3 last:border-b-0"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="break-all text-sm font-medium">
                        {principalLabel(entry.principal, t('acl.everyone'))}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {t('acl.inheritedSource')} ·{' '}
                        {t(`acl.subjects.${principalType(entry.principal)}`)}
                      </p>
                    </div>
                    <Badge variant="outline">
                      {t(`acl.levels.${entry.level}`)}
                    </Badge>
                  </div>
                ))}
            </section>
          </div>
        )}
      </div>
      <AlertDialog
        open={confirm !== null}
        onOpenChange={(open) => {
          if (!open && !mutation.isPending) setConfirm(null)
        }}
      >
        <AlertDialogContent onKeyDown={(event) => event.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t(modeChange ? 'acl.limitTitle' : 'acl.remove')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t(modeChange ? 'acl.limitWarning' : 'acl.removeWarning', {
                principal: confirm?.kind === 'revoke' ? confirm.principal : '',
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={mutation.isPending}>
              {t('actions.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={!writable}
              onClick={(event) => {
                event.preventDefault()
                if (confirm) mutation.mutate(confirm)
              }}
            >
              {mutation.isPending ? t('loading') : t('acl.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}

function principalLabel(principal: string, everyone: string) {
  return principal === 'user:*'
    ? everyone
    : principal.replace(/^(user|group):/, '')
}
function principalType(principal: string) {
  return principal === 'user:*'
    ? 'everyone'
    : principal.startsWith('group:')
      ? 'group'
      : 'user'
}
function LevelSelect({
  value,
  disabled,
  label,
  onChange,
}: {
  value: AclLevel
  disabled: boolean
  label: string
  onChange: (level: AclLevel) => void
}) {
  const { t } = useTranslation('settings')
  return (
    <Select
      value={value}
      disabled={disabled}
      onValueChange={(level) => {
        if (levels.includes(level as AclLevel)) onChange(level as AclLevel)
      }}
    >
      <SelectTrigger aria-label={label} className="w-32">
        <SelectValue>{t(`acl.levels.${value}`)}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {levels.map((level) => (
            <SelectItem key={level} value={level}>
              {t(`acl.levels.${level}`)}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

function GrantForm({
  state,
  disabled,
  onGrant,
}: {
  state: ReturnType<typeof useAclManagement>
  disabled: boolean
  onGrant: (entries: AclEntry[]) => void
}) {
  const { t } = useTranslation('settings')
  const [kind, setKind] = useState('user')
  const [selectedPrincipals, setSelectedPrincipals] = useState<string[]>([])
  const [level, setLevel] = useState<AclLevel>('read')
  const [search, setSearch] = useState('')
  const query = useDebouncedValue(search, 250)
  const [page, setPage] = useState(1)
  const users = useQuery({
    queryKey: ['acl-users', state.identityScopeKey, query, page],
    queryFn: () =>
      fetchAdminUsersPage(state.adminConnection, state.connection.accountId, {
        search: query,
        page,
        pageSize: 20,
      }),
    enabled: !disabled && kind === 'user',
    retry: false,
  })
  const groups = useQuery({
    queryKey: [
      'managed-groups',
      state.connection.baseUrl,
      state.connection.accountId,
      state.connection.adminApiKey,
    ],
    queryFn: () => fetchAdminGroups(state.adminConnection),
    enabled: !disabled && kind === 'group',
    retry: false,
  })
  const candidates =
    kind === 'group'
      ? (groups.data
          ?.filter((group) =>
            group.group_id.toLowerCase().includes(search.trim().toLowerCase()),
          )
          .map((group) => ({
            value: `group:${group.group_id}`,
            label: group.group_id,
          })) ?? [])
      : (users.data?.users.map((user) => ({
          value: `user:${user.userId}`,
          label: user.userId,
        })) ?? [])
  const list = kind === 'group' ? groups : users
  const selected = kind === 'everyone' ? ['user:*'] : selectedPrincipals
  const valid = selected.length > 0
  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault()
        if (!disabled && valid)
          onGrant(selected.map((principal) => ({ principal, level })))
      }}
    >
      <FieldGroup className="gap-4">
        <Field>
          <FieldLabel>{t('acl.subjectType')}</FieldLabel>
          <div
            className="grid grid-cols-3 gap-1 rounded-md bg-muted/60 p-1"
            role="group"
            aria-label={t('acl.subjectType')}
          >
            {subjectTypes.map((value) => (
              <Button
                key={value}
                type="button"
                variant="ghost"
                size="sm"
                aria-pressed={kind === value}
                disabled={disabled}
                className={`min-w-0 px-1 text-xs ${
                  kind === value
                    ? 'bg-background shadow-xs hover:bg-background'
                    : 'text-muted-foreground'
                }`}
                onClick={() => {
                  setKind(value)
                  if (value !== kind) setSelectedPrincipals([])
                  setSearch('')
                  setPage(1)
                }}
              >
                {t(
                  value === 'everyone'
                    ? 'acl.subjects.everyoneShort'
                    : `acl.subjects.${value}`,
                )}
              </Button>
            ))}
          </div>
        </Field>
        {kind === 'everyone' ? (
          <Field className="min-h-[19rem]">
            <FieldLabel>{t('acl.subject')}</FieldLabel>
            <div className="h-44 rounded-md border p-1">
              <div className="flex items-center gap-2 rounded bg-muted px-3 py-2 text-sm font-medium">
                <CheckIcon className="size-4 shrink-0" />
                {t('acl.everyone')}
              </div>
            </div>
          </Field>
        ) : (
          <Field className="min-h-[19rem]">
            <FieldLabel htmlFor="acl-subject-search">
              {t('acl.search')}
            </FieldLabel>
            <div className="relative">
              <SearchIcon className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="acl-subject-search"
                className="pl-9"
                disabled={disabled}
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value)
                  setPage(1)
                }}
              />
            </div>
            {!disabled &&
              (list.isError ? (
                <Alert variant="destructive">
                  <AlertTitle>{t('acl.candidatesFailed')}</AlertTitle>
                  <AlertDescription>
                    {getErrorMessage(list.error)}
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void list.refetch()}
                    >
                      {t('actions.refresh')}
                    </Button>
                  </AlertDescription>
                </Alert>
              ) : (
                <>
                  <div
                    className="h-44 overflow-y-auto rounded-md border p-1"
                    role="group"
                    aria-label={t('acl.subject')}
                  >
                    {list.isPending || list.isFetching || query !== search ? (
                      <p
                        className="px-3 py-4 text-sm text-muted-foreground"
                        role="status"
                      >
                        {t('loading')}
                      </p>
                    ) : candidates.length === 0 ? (
                      <p className="px-3 py-4 text-sm text-muted-foreground">
                        {t('acl.noCandidates')}
                      </p>
                    ) : (
                      candidates.map((candidate) => (
                        <button
                          key={candidate.value}
                          type="button"
                          aria-pressed={selectedPrincipals.includes(
                            candidate.value,
                          )}
                          className={`flex w-full items-center gap-3 rounded px-3 py-2 text-left text-sm hover:bg-muted ${selectedPrincipals.includes(candidate.value) ? 'bg-muted font-medium' : ''}`}
                          onClick={() =>
                            setSelectedPrincipals((current) =>
                              current.includes(candidate.value)
                                ? current.filter(
                                    (value) => value !== candidate.value,
                                  )
                                : [...current, candidate.value],
                            )
                          }
                        >
                          <span className="flex size-4 shrink-0 items-center justify-center rounded border border-input">
                            {selectedPrincipals.includes(candidate.value) && (
                              <CheckIcon className="size-3" />
                            )}
                          </span>
                          <span className="truncate">{candidate.label}</span>
                        </button>
                      ))
                    )}
                  </div>
                  {kind === 'user' && (users.data?.total ?? 0) > 20 && (
                    <div className="flex items-center justify-end gap-2 text-xs text-muted-foreground">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={page <= 1 || users.isFetching}
                        onClick={() => {
                          setPage(page - 1)
                        }}
                      >
                        {t('groups.previous')}
                      </Button>
                      <span>
                        {t('groups.page', {
                          page,
                          pages: Math.max(
                            1,
                            Math.ceil((users.data?.total ?? 0) / 20),
                          ),
                        })}
                      </span>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        disabled={
                          page * 20 >= (users.data?.total ?? 0) ||
                          users.isFetching
                        }
                        onClick={() => {
                          setPage(page + 1)
                        }}
                      >
                        {t('groups.next')}
                      </Button>
                    </div>
                  )}
                </>
              ))}
          </Field>
        )}
        <Field>
          <FieldLabel>{t('acl.level')}</FieldLabel>
          <div
            className="grid grid-cols-3 gap-1 rounded-md bg-muted/60 p-1"
            role="group"
            aria-label={t('acl.level')}
          >
            {levels.map((value) => (
              <Button
                key={value}
                type="button"
                variant="ghost"
                size="sm"
                aria-pressed={level === value}
                disabled={disabled}
                className={
                  level === value
                    ? 'bg-background shadow-xs hover:bg-background'
                    : 'text-muted-foreground'
                }
                onClick={() => setLevel(value)}
              >
                {t(`acl.levels.${value}`)}
              </Button>
            ))}
          </div>
        </Field>
      </FieldGroup>
      <div className="flex justify-end">
        <Button type="submit" disabled={disabled || !valid}>
          {t(
            kind === 'everyone'
              ? 'acl.confirmEveryoneGrant'
              : 'acl.confirmGrant',
            { count: selected.length, level: t(`acl.levels.${level}`) },
          )}
        </Button>
      </div>
    </form>
  )
}
