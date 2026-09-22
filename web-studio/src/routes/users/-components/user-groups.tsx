import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Button } from '#/components/ui/button'
import { Badge } from '#/components/ui/badge'
import { Input } from '#/components/ui/input'
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from '#/components/ui/field'
import { Alert, AlertDescription, AlertTitle } from '#/components/ui/alert'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '#/components/ui/dialog'
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
import {
  Table,
  TableHeader,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
} from '#/components/ui/table'
import {
  createAdminGroup,
  deleteAdminGroup,
  fetchAdminGroups,
  fetchAdminGroupMembers,
  fetchAdminUsersPage,
  updateAdminGroupMember,
} from '#/lib/admin'
import type { AdminConnection } from '#/lib/admin'
import { getErrorMessage } from '../-lib/error'

function QueryError({ error, retry }: { error: unknown; retry: () => void }) {
  const { t } = useTranslation('settings')
  return (
    <Alert variant="destructive">
      <AlertTitle>{t('groups.loadFailed')}</AlertTitle>
      <AlertDescription>
        <p>{getErrorMessage(error)}</p>
        <Button variant="outline" onClick={retry}>
          {t('actions.refresh')}
        </Button>
      </AlertDescription>
    </Alert>
  )
}

export function UserGroups({ connection }: { connection: AdminConnection }) {
  const { t } = useTranslation('settings')
  const client = useQueryClient()
  const scope = [connection.baseUrl, connection.accountId, connection.apiKey]
  const queryKey = ['managed-groups', ...scope]
  const groups = useQuery({
    queryKey,
    queryFn: () => fetchAdminGroups(connection),
    retry: false,
  })
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)
  const [groupId, setGroupId] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const onError = (error: unknown) =>
    toast.error(t('groups.failed'), { description: getErrorMessage(error) })
  const create = useMutation({
    mutationFn: (id: string) => createAdminGroup(connection, id),
    onError,
    onSuccess: async () => {
      setCreating(false)
      setGroupId('')
      toast.success(t('groups.created'))
      await client.invalidateQueries({ queryKey })
    },
  })
  const remove = useMutation({
    mutationFn: (id: string) => deleteAdminGroup(connection, id),
    onError: async (error) => {
      onError(error)
      await client.invalidateQueries({ queryKey })
    },
    onSuccess: async () => {
      setDeleting(null)
      toast.success(t('groups.deleted'))
      await client.invalidateQueries({ queryKey })
    },
  })
  const filtered =
    groups.data?.filter((group) =>
      group.group_id.toLowerCase().includes(search.trim().toLowerCase()),
    ) ?? []
  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold">
            {t('groups.title')}{' '}
            <Badge variant="secondary">{connection.accountId}</Badge>
          </h1>
          <p className="text-sm text-muted-foreground">
            {t('groups.description', { account: connection.accountId })}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={groups.isFetching}
            onClick={() => void groups.refetch()}
          >
            {t('actions.refresh')}
          </Button>
          <Button onClick={() => setCreating(true)}>
            {t('groups.create')}
          </Button>
        </div>
      </header>
      <Input
        className="max-w-sm"
        aria-label={t('groups.search')}
        placeholder={t('groups.search')}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      {groups.isPending ? (
        <p role="status">{t('loading')}</p>
      ) : groups.isError ? (
        <QueryError error={groups.error} retry={() => void groups.refetch()} />
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('groups.id')}</TableHead>
                <TableHead>{t('groups.count')}</TableHead>
                <TableHead className="text-right">
                  {t('groups.actions')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={3}
                    className="py-10 text-center text-muted-foreground"
                  >
                    {t('groups.empty')}
                  </TableCell>
                </TableRow>
              ) : (
                filtered.map((group) => (
                  <TableRow key={group.group_id}>
                    <TableCell className="font-mono">
                      {group.group_id}
                    </TableCell>
                    <TableCell>{group.member_count}</TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setSelected(group.group_id)}
                        >
                          {t('groups.manage')}
                        </Button>
                        <span
                          title={
                            group.member_count
                              ? t('groups.deleteHint')
                              : undefined
                          }
                        >
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={
                              group.member_count > 0 || remove.isPending
                            }
                            onClick={() => setDeleting(group.group_id)}
                          >
                            {t('groups.delete')}
                          </Button>
                        </span>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      )}
      <Dialog
        open={creating}
        onOpenChange={(open) => {
          if (!create.isPending) setCreating(open)
        }}
      >
        <DialogContent showCloseButton={!create.isPending}>
          <form
            className="flex flex-col gap-5"
            onSubmit={(event) => {
              event.preventDefault()
              if (groupId.trim() && !create.isPending)
                create.mutate(groupId.trim())
            }}
          >
            <DialogHeader>
              <DialogTitle>{t('groups.create')}</DialogTitle>
              <DialogDescription>
                {t('groups.description', { account: connection.accountId })}
              </DialogDescription>
            </DialogHeader>
            <FieldGroup>
              <Field>
                <FieldLabel htmlFor="new-group-id">{t('groups.id')}</FieldLabel>
                <Input
                  id="new-group-id"
                  required
                  value={groupId}
                  disabled={create.isPending}
                  onChange={(event) => setGroupId(event.target.value)}
                />
                <FieldDescription>{t('groups.idHint')}</FieldDescription>
              </Field>
            </FieldGroup>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={create.isPending}
                onClick={() => setCreating(false)}
              >
                {t('actions.cancel')}
              </Button>
              <Button
                type="submit"
                disabled={!groupId.trim() || create.isPending}
              >
                {create.isPending ? t('loading') : t('groups.create')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <AlertDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setDeleting(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('groups.delete')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('groups.deleteDescription', { group: deleting })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>
              {t('actions.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={remove.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (deleting) remove.mutate(deleting)
              }}
            >
              {remove.isPending ? t('loading') : t('groups.delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {selected !== null && (
        <GroupMembers
          key={selected}
          connection={connection}
          groupId={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}

function GroupMembers({
  connection,
  groupId,
  onClose,
}: {
  connection: AdminConnection
  groupId: string
  onClose: () => void
}) {
  const { t } = useTranslation('settings')
  const client = useQueryClient()
  const scope = [connection.baseUrl, connection.accountId, connection.apiKey]
  const membersKey = ['managed-group-members', ...scope, groupId]
  const members = useQuery({
    queryKey: membersKey,
    queryFn: () => fetchAdminGroupMembers(connection, groupId),
    retry: false,
  })
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search)
      setPage(1)
    }, 250)
    return () => clearTimeout(timer)
  }, [search])
  const users = useQuery({
    queryKey: ['group-user-candidates', ...scope, query, page],
    queryFn: () =>
      fetchAdminUsersPage(connection, connection.accountId, {
        page,
        pageSize: 20,
        search: query,
      }),
    retry: false,
  })
  const change = useMutation({
    mutationFn: ({ userId, add }: { userId: string; add: boolean }) =>
      updateAdminGroupMember(connection, groupId, userId, add),
    onError: (error) =>
      toast.error(t('groups.failed'), { description: getErrorMessage(error) }),
    onSuccess: () => {
      toast.success(t('groups.updated'))
    },
    onSettled: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: membersKey }),
        client.invalidateQueries({ queryKey: ['managed-groups', ...scope] }),
      ])
    },
  })
  const pages = Math.max(1, Math.ceil((users.data?.total ?? 0) / 20))
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !change.isPending) onClose()
      }}
    >
      <DialogContent
        className="gap-5 sm:max-w-3xl"
        showCloseButton={!change.isPending}
      >
        <DialogHeader className="pr-8">
          <DialogTitle className="break-all text-lg">
            {t('groups.members', { group: groupId })}
          </DialogTitle>
          <DialogDescription>{t('groups.memberHint')}</DialogDescription>
        </DialogHeader>
        <div className="grid min-w-0 gap-5 sm:grid-cols-2">
          <section className="flex min-w-0 flex-col gap-3">
            <h2 className="flex items-center gap-2 font-medium">
              {t('groups.currentMembers')}
              {members.isSuccess && (
                <Badge variant="secondary">{members.data.members.length}</Badge>
              )}
            </h2>
            {members.isPending ? (
              <p role="status">{t('loading')}</p>
            ) : members.isError ? (
              <QueryError
                error={members.error}
                retry={() => void members.refetch()}
              />
            ) : (
              <ul className="max-h-80 overflow-y-auto overscroll-contain rounded-lg border divide-y">
                {members.data.members.length === 0 && (
                  <li className="px-3 py-6 text-center text-sm text-muted-foreground">
                    {t('groups.noMembers')}
                  </li>
                )}
                {members.data.members.map((user) => (
                  <li
                    key={user}
                    className="flex items-center justify-between gap-3 px-3 py-2"
                  >
                    <span className="truncate font-mono text-sm" title={user}>
                      {user}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={change.isPending || members.isFetching}
                      onClick={() =>
                        change.mutate({ userId: user, add: false })
                      }
                    >
                      {t('groups.remove')}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="flex min-w-0 flex-col gap-3">
            <h2 className="font-medium">{t('groups.candidates')}</h2>
            <Input
              aria-label={t('groups.searchUsers')}
              placeholder={t('groups.searchUsers')}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            {users.isPending ? (
              <p role="status">{t('loading')}</p>
            ) : users.isError ? (
              <QueryError
                error={users.error}
                retry={() => void users.refetch()}
              />
            ) : (
              <>
                <ul className="max-h-80 overflow-y-auto overscroll-contain rounded-lg border divide-y">
                  {users.data.users.length === 0 && (
                    <li className="px-3 py-6 text-center text-sm text-muted-foreground">
                      {t('groups.noUsers')}
                    </li>
                  )}
                  {users.data.users.map((user) => (
                    <li
                      key={user.userId}
                      className="flex items-center justify-between gap-3 px-3 py-2"
                    >
                      <span
                        className="truncate font-mono text-sm"
                        title={user.userId}
                      >
                        {user.userId}
                      </span>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={
                          !members.isSuccess ||
                          members.isFetching ||
                          change.isPending ||
                          search !== query ||
                          members.data.members.includes(user.userId)
                        }
                        onClick={() =>
                          change.mutate({ userId: user.userId, add: true })
                        }
                      >
                        {t('groups.add')}
                      </Button>
                    </li>
                  ))}
                </ul>
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page <= 1 || users.isFetching}
                    onClick={() => setPage(page - 1)}
                  >
                    {t('groups.previous')}
                  </Button>
                  <span>{t('groups.page', { page, pages })}</span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page >= pages || users.isFetching}
                    onClick={() => setPage(page + 1)}
                  >
                    {t('groups.next')}
                  </Button>
                </div>
              </>
            )}
          </section>
        </div>
      </DialogContent>
    </Dialog>
  )
}
