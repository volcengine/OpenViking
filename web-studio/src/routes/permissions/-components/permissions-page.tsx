import { useState } from 'react'
import {
  ArrowLeftIcon,
  ChevronRightIcon,
  FolderIcon,
  RotateCwIcon,
} from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AccountAclSettings } from '#/components/account-acl-settings'
import { ResourcePermissionsPanel } from '#/components/resource-permissions'
import { ResourceAclIdentityRecovery } from '#/components/resource-acl-identity-recovery'
import { Button } from '#/components/ui/button'
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
      <div className="rounded-lg border">
        <Table className="table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="pl-4">{t('acl.page.nameColumn')}</TableHead>
              <TableHead className="w-44">{t('acl.page.ruleColumn')}</TableHead>
              <TableHead className="w-28 pr-4 text-right">
                {t('acl.page.actionsColumn')}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {directories.isPending ? (
              <TableRow>
                <TableCell
                  colSpan={3}
                  className="py-10 text-center text-muted-foreground"
                >
                  {t('loading')}
                </TableCell>
              </TableRow>
            ) : directories.isError ? (
              <TableRow>
                <TableCell colSpan={3} className="py-8 text-center">
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
                  colSpan={3}
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
        <SheetContent className="gap-0 data-[side=right]:sm:max-w-3xl">
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
  onOpen,
  onEdit,
}: {
  entry: VikingFsEntry
  accountEnabled: boolean
  onOpen: () => void
  onEdit: () => void
}) {
  const state = useAclManagement(false)
  const { t } = useTranslation('settings')
  const uri = entry.uri
  const report = useQuery({
    queryKey: ['resource-acl', state.aclIdentityScopeKey, uri],
    queryFn: () => state.api.get(uri),
    enabled: state.allowed,
    retry: false,
  })
  const name = entry.name
  return (
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
      <TableCell>
        <span className="text-muted-foreground">
          {report.isPending
            ? t('loading')
            : report.isError
              ? t('acl.page.unreadable')
              : t(`acl.modes.${report.data.acl_mode}`)}
        </span>
      </TableCell>
      <TableCell className="pr-4 text-right">
        <Button
          variant="ghost"
          size="sm"
          disabled={!accountEnabled}
          onClick={onEdit}
        >
          {t('acl.page.manageAction')}
        </Button>
      </TableCell>
    </TableRow>
  )
}
