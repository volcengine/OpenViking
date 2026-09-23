import { useEffect, useState } from 'react'
import {
  ArrowLeftIcon,
  ChevronRightIcon,
  FolderIcon,
  Globe2Icon,
  RotateCwIcon,
  UserRoundIcon,
  UsersRoundIcon,
} from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { AccountAclSettings } from '#/components/account-acl-settings'
import { ResourcePermissionsPanel } from '#/components/resource-permissions'
import { ResourceAclIdentityRecovery } from '#/components/resource-acl-identity-recovery'
import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Switch } from '#/components/ui/switch'
import { Alert, AlertTitle, AlertDescription } from '#/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '#/components/ui/alert-dialog'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '#/components/ui/sheet'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { useAclManagement } from '#/hooks/use-acl-management'
import { isSharedAclTarget } from '#/lib/resource-acl'
import {
  normalizeFsEntries,
  parentUri,
} from '#/routes/resources/-lib/normalize'
import { getErrorMessage } from '#/routes/users/-lib/error'
import { isOvClientError } from '#/lib/ov-client'
import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'

const root = 'viking://resources/'

export function PermissionsPage() {
  const state = useAclManagement()
  const { t } = useTranslation('settings')
  return (
    <main className="flex w-full min-w-0 flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('acl.page.title')}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t('acl.page.description')}
          </p>
        </div>
        {state.allowed && state.settings.data === true && (
          <AccountAclSettings inPopover />
        )}
      </header>
      {state.allowed && state.settings.data !== true && <AccountAclSettings />}
      <DirectoryPermissions
        key={`${state.connection.baseUrl}:${state.connection.accountId}`}
      />
    </main>
  )
}

function DirectoryPermissions() {
  const client = useQueryClient()
  const state = useAclManagement()
  const { t } = useTranslation('settings')
  const [currentUri, setCurrentUri] = useState(root)
  const [editingUri, setEditingUri] = useState('')
  const directories = useQuery({
    queryKey: ['acl-directory-list', state.aclIdentityScopeKey, currentUri],
    queryFn: async () => {
      const result = await state.api.listDirectory(currentUri)
      return normalizeFsEntries(result, currentUri).filter(
        (entry: VikingFsEntry) =>
          entry.isDir &&
          isSharedAclTarget(entry.uri) &&
          parentUri(entry.uri) === currentUri,
      )
    },
    enabled: state.allowed,
    retry: false,
  })
  const parentReport = useQuery({
    queryKey: ['resource-acl', state.aclIdentityScopeKey, currentUri],
    queryFn: () => state.api.get(currentUri),
    enabled: state.allowed && currentUri !== root,
    retry: false,
  })
  const parentControlled =
    currentUri !== root &&
    parentReport.isSuccess &&
    parentReport.data.acl_mode !== 'none'
  const parentKnown =
    currentUri === root || (parentReport.isSuccess && !parentReport.isFetching)
  const pathParts = currentUri.slice(root.length).split('/').filter(Boolean)
  const breadcrumbs = [
    { label: 'resources', uri: root },
    ...pathParts.map((part, index) => ({
      label: part,
      uri: `${root}${pathParts.slice(0, index + 1).join('/')}/`,
    })),
  ]

  if (!state.allowed) return <p>{t('acl.page.unavailable')}</p>

  return (
    <section className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {currentUri !== root && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t('acl.page.back')}
            onClick={() => setCurrentUri(parentUri(currentUri))}
          >
            <ArrowLeftIcon />
          </Button>
        )}
        <nav
          aria-label={t('acl.page.path')}
          className="flex min-w-0 flex-1 flex-wrap items-center gap-1 text-sm"
        >
          {breadcrumbs.map((crumb, index) => (
            <span key={crumb.uri} className="flex items-center gap-1">
              {index > 0 && (
                <ChevronRightIcon className="size-4 text-muted-foreground" />
              )}
              <button
                type="button"
                className={`rounded px-1.5 py-1 hover:bg-muted ${
                  index === breadcrumbs.length - 1
                    ? 'font-medium'
                    : 'text-muted-foreground'
                }`}
                aria-current={
                  index === breadcrumbs.length - 1 ? 'page' : undefined
                }
                onClick={() => setCurrentUri(crumb.uri)}
              >
                {crumb.label}
              </button>
            </span>
          ))}
        </nav>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('actions.refresh')}
          onClick={() => {
            void directories.refetch()
            void client.invalidateQueries({
              queryKey: ['resource-acl', state.aclIdentityScopeKey],
            })
            void client.invalidateQueries({ queryKey: state.settingsKey })
          }}
        >
          <RotateCwIcon />
        </Button>
      </div>
      {currentUri !== root && parentReport.isError && (
        <Alert variant="destructive">
          <AlertTitle>{t('acl.page.parentAclFailed')}</AlertTitle>
          <AlertDescription>
            <p>{getErrorMessage(parentReport.error)}</p>
            <Button
              variant="outline"
              onClick={() => void parentReport.refetch()}
            >
              {t('actions.refresh')}
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <div className="rounded-lg border">
        <Table className="min-w-[900px] table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-[24%] pl-4">
                {t('acl.page.nameColumn')}
              </TableHead>
              <TableHead>{t('acl.page.granteesColumn')}</TableHead>
              <TableHead className="w-44">{t('acl.page.ruleColumn')}</TableHead>
              <TableHead className="w-28 text-center">
                {t('acl.limitAccess')}
              </TableHead>
              <TableHead className="w-28 pr-4 text-right">
                {t('acl.page.actionsColumn')}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {directories.isPending ? (
              <TableRow>
                <TableCell
                  colSpan={5}
                  className="py-10 text-center text-muted-foreground"
                >
                  {t('loading')}
                </TableCell>
              </TableRow>
            ) : directories.isError ? (
              <TableRow>
                <TableCell colSpan={5} className="py-8 text-center">
                  {isOvClientError(directories.error) &&
                  (directories.error.statusCode === 403 ||
                    directories.error.code === 'PERMISSION_DENIED') ? (
                    <ResourceAclIdentityRecovery
                      onRetry={() => void directories.refetch()}
                      error={getErrorMessage(directories.error)}
                    />
                  ) : (
                    <>
                      <p className="text-destructive">
                        {t('acl.page.listFailed')}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {getErrorMessage(directories.error)}
                      </p>
                      <Button
                        variant="outline"
                        size="sm"
                        className="mt-3"
                        onClick={() => void directories.refetch()}
                      >
                        {t('actions.refresh')}
                      </Button>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ) : directories.data.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={5}
                  className="py-10 text-center text-muted-foreground"
                >
                  {t('acl.page.emptyDirectory')}
                </TableCell>
              </TableRow>
            ) : (
              directories.data.map((entry) => (
                <DirectoryRow
                  key={entry.uri}
                  entry={entry}
                  accountEnabled={
                    state.settings.data === true &&
                    state.settings.isSuccess &&
                    !state.settings.isFetching
                  }
                  parentControlled={parentControlled}
                  parentKnown={parentKnown}
                  onOpen={() => setCurrentUri(entry.uri)}
                  onEdit={() => setEditingUri(entry.uri)}
                />
              ))
            )}
          </TableBody>
        </Table>
      </div>
      <Sheet
        open={Boolean(editingUri)}
        onOpenChange={(open) => !open && setEditingUri('')}
      >
        <SheetContent className="gap-0 data-[side=right]:sm:max-w-2xl">
          <SheetHeader className="border-b px-6 py-5">
            <SheetTitle className="pr-10 text-lg">
              {t('acl.page.editDirectory', {
                directory: editingUri.slice(root.length).replace(/\/$/, ''),
              })}
            </SheetTitle>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            {editingUri && (
              <ResourcePermissionsPanel
                key={`${state.aclIdentityScopeKey}:${editingUri}`}
                uri={editingUri}
              />
            )}
          </div>
        </SheetContent>
      </Sheet>
    </section>
  )
}

function DirectoryRow({
  entry,
  accountEnabled,
  parentControlled,
  parentKnown,
  onOpen,
  onEdit,
}: {
  entry: VikingFsEntry
  accountEnabled: boolean
  parentControlled: boolean
  parentKnown: boolean
  onOpen: () => void
  onEdit: () => void
}) {
  const state = useAclManagement(false)
  const { t } = useTranslation('settings')
  const client = useQueryClient()
  const [next, setNext] = useState<boolean | null>(null)
  useEffect(() => {
    if (!accountEnabled) setNext(null)
  }, [accountEnabled])
  const uri = entry.uri
  const key = ['resource-acl', state.aclIdentityScopeKey, uri]
  const report = useQuery({
    queryKey: key,
    queryFn: () => state.api.get(uri),
    enabled: state.allowed,
    retry: false,
  })
  const name = entry.name
  const hasAcl = report.isSuccess && report.data.acl_mode !== 'none'
  const restricted = report.isSuccess && report.data.acl_mode === 'restricted'
  const grants =
    report.data?.direct_entries.map((grant) => {
      if (grant.principal === 'user:*') {
        return {
          principal: grant.principal,
          label: t('acl.subjects.everyoneShort'),
          level: grant.level,
          type: 'everyone',
        }
      }
      const type = grant.principal.startsWith('group:') ? 'group' : 'user'
      return {
        principal: grant.principal,
        label: grant.principal.slice(grant.principal.indexOf(':') + 1),
        level: grant.level,
        type,
      }
    }) || []
  const grantNames = grants.map(
    ({ label, level, type }) =>
      `${type === 'everyone' ? t('acl.everyone') : `${t(`acl.subjects.${type}`)} · ${label}`}: ${t(`acl.levels.${level}`)}`,
  )
  const writable =
    accountEnabled && report.isSuccess && !report.isFetching && parentKnown
  // The drawer contains identity recovery when this row's ACL report is denied.
  const canOpenEditor =
    report.isError || (writable && (hasAcl || parentControlled))
  const update = useMutation({
    mutationFn: (limit: boolean) =>
      state.api.change(
        uri,
        limit
          ? { kind: 'mode', mode: 'restricted' }
          : parentControlled
            ? { kind: 'mode', mode: 'inherit' }
            : { kind: 'reset' },
      ),
    onSuccess: async (updated) => {
      client.setQueryData(key, updated)
      setNext(null)
      toast.success(t('acl.saved'))
      await client.invalidateQueries()
    },
    onError: (error) =>
      toast.error(t('acl.failed'), { description: getErrorMessage(error) }),
  })
  return (
    <>
      <TableRow>
        <TableCell className="pl-4">
          <button
            type="button"
            className="flex max-w-full items-center gap-2 text-left font-medium hover:underline"
            onClick={onOpen}
          >
            <FolderIcon className="size-4 shrink-0 text-muted-foreground" />
            <span className="truncate" title={name}>
              {name}
            </span>
          </button>
        </TableCell>
        <TableCell
          className="min-w-0 overflow-hidden"
          title={grantNames.join(', ')}
        >
          {report.isSuccess ? (
            grants.length > 0 ? (
              <div className="flex min-w-0 items-center gap-1.5 overflow-hidden">
                {grants.slice(0, 3).map((grant) => (
                  <Badge
                    key={grant.principal}
                    variant="secondary"
                    className="max-w-44 min-w-0 gap-1 text-muted-foreground"
                    title={`${grant.label}: ${t(`acl.levels.${grant.level}`)}`}
                  >
                    {grant.type === 'group' ? (
                      <UsersRoundIcon className="shrink-0" />
                    ) : grant.type === 'everyone' ? (
                      <Globe2Icon className="shrink-0" />
                    ) : (
                      <UserRoundIcon className="shrink-0" />
                    )}
                    <span className="truncate">{grant.label}</span>
                    <span aria-hidden="true">:</span>
                    <span className="shrink-0 text-foreground">
                      {t(`acl.levels.${grant.level}`)}
                    </span>
                  </Badge>
                ))}
                {grants.length > 3 && (
                  <Badge variant="outline" className="text-muted-foreground">
                    +{grants.length - 3}
                  </Badge>
                )}
              </div>
            ) : (
              <span className="text-muted-foreground">
                {t('acl.page.noGrantees')}
              </span>
            )
          ) : (
            '—'
          )}
        </TableCell>
        <TableCell>
          <span className="text-muted-foreground">
            {report.isPending
              ? t('loading')
              : report.isError
                ? t('acl.page.unreadable')
                : t(`acl.modes.${report.data.acl_mode}`)}
          </span>
        </TableCell>
        <TableCell className="text-center">
          {report.isSuccess ? (
            <Switch
              checked={parentControlled ? restricted : hasAcl}
              disabled={!writable || update.isPending}
              aria-label={t('acl.page.limitFor', { directory: name })}
              onCheckedChange={setNext}
            />
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell className="pr-4 text-right">
          <Button
            variant="ghost"
            size="sm"
            disabled={!canOpenEditor || update.isPending}
            onClick={onEdit}
          >
            {t('acl.page.manageAction')}
          </Button>
        </TableCell>
      </TableRow>
      <AlertDialog
        open={next !== null}
        onOpenChange={(open) => {
          if (!open && !update.isPending) setNext(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t(
                next
                  ? 'acl.limitTitle'
                  : parentControlled
                    ? 'acl.page.restoreInheritanceTitle'
                    : 'acl.page.disableLimitTitle',
              )}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t(
                next
                  ? 'acl.limitWarning'
                  : parentControlled
                    ? 'acl.page.restoreInheritanceWarning'
                    : 'acl.page.disableLimitWarning',
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={update.isPending}>
              {t('actions.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={!writable || update.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (writable && next !== null) update.mutate(next)
              }}
            >
              {update.isPending ? t('loading') : t('acl.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
