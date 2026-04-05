import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Activity, KeyRound, ShieldCheck, Users } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '#/components/ui/alert'
import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '#/components/ui/card'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { LegacyPageShell } from '#/components/legacy/shared/page-shell'
import {
  OvClientError,
  deleteAdminAccountByAccountId,
  deleteAdminAccountIdUserByUserId,
  getAdminAccountIdUsers,
  getAdminAccounts,
  getObserverSystem,
  getOvResult,
  getSystemStatus,
  postAdminAccountIdUserIdKey,
  postAdminAccountIdUsers,
  postAdminAccounts,
  putAdminAccountIdUserIdRole,
} from '#/lib/ov-client'
import {
  applyLegacyConnectionSettings,
  loadLegacyConnectionSettings,
} from '#/lib/legacy/connection'

type TenantAccount = {
  accountId: string
  userCount: string
}

type TenantUser = {
  role: string
  userId: string
}

type LatestResult = {
  title: string
  value: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function pickFirstNonEmpty(values: Array<unknown>): string {
  for (const value of values) {
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      return String(value)
    }
  }
  return ''
}

function normalizeArrayResult(result: unknown, candidateKeys: Array<string>): Array<unknown> {
  if (Array.isArray(result)) {
    return result
  }
  if (isRecord(result)) {
    for (const key of candidateKeys) {
      const value = result[key]
      if (Array.isArray(value)) {
        return value
      }
    }
  }
  return []
}

function normalizeTenantAccounts(result: unknown): Array<TenantAccount> {
  return normalizeArrayResult(result, ['accounts', 'items', 'results'])
    .map((item) => {
      if (typeof item === 'string') {
        return { accountId: item.trim(), userCount: '' }
      }
      if (!isRecord(item)) {
        return null
      }
      return {
        accountId: pickFirstNonEmpty([item.account_id, item.accountId, item.id, item.name]),
        userCount: pickFirstNonEmpty([item.user_count, item.userCount, item.users, item.member_count]),
      }
    })
    .filter((item): item is TenantAccount => Boolean(item?.accountId))
}

function normalizeTenantUsers(result: unknown): Array<TenantUser> {
  return normalizeArrayResult(result, ['users', 'items', 'results'])
    .map((item) => {
      if (typeof item === 'string') {
        return { role: '', userId: item.trim() }
      }
      if (!isRecord(item)) {
        return null
      }
      return {
        role: pickFirstNonEmpty([item.role, item.user_role, item.userRole, item.permission]),
        userId: pickFirstNonEmpty([item.user_id, item.userId, item.id, item.name]),
      }
    })
    .filter((item): item is TenantUser => Boolean(item?.userId))
}

function formatResult(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function getErrorMessage(error: unknown): string {
  if (error instanceof OvClientError) {
    return `${error.code}: ${error.message}`
  }
  if (error instanceof Error) {
    return error.message
  }
  return String(error)
}

export function OpsLegacyPage() {
  const queryClient = useQueryClient()
  const [accountSearch, setAccountSearch] = useState('')
  const [createAccountId, setCreateAccountId] = useState('')
  const [createAdminUserId, setCreateAdminUserId] = useState('admin')
  const [newUserId, setNewUserId] = useState('')
  const [newUserRole, setNewUserRole] = useState('user')
  const [latestResult, setLatestResult] = useState<LatestResult | null>(null)
  const [selectedAccountId, setSelectedAccountId] = useState('')

  useEffect(() => {
    applyLegacyConnectionSettings(loadLegacyConnectionSettings())
  }, [])

  const accountsQuery = useQuery({
    queryKey: ['legacy-ops-accounts'],
    queryFn: async () => normalizeTenantAccounts(await getOvResult(getAdminAccounts())),
  })

  useEffect(() => {
    const accounts = accountsQuery.data || []
    if (!accounts.length) {
      setSelectedAccountId('')
      return
    }

    const hasSelected = accounts.some((account) => account.accountId === selectedAccountId)
    if (!hasSelected) {
      setSelectedAccountId(accounts[0]?.accountId || '')
    }
  }, [accountsQuery.data, selectedAccountId])

  const usersQuery = useQuery({
    enabled: Boolean(selectedAccountId),
    queryKey: ['legacy-ops-users', selectedAccountId],
    queryFn: async () =>
      normalizeTenantUsers(
        await getOvResult(
          getAdminAccountIdUsers({
            path: { account_id: selectedAccountId },
          }),
        ),
      ),
  })

  const refreshQueries = async (accountId?: string) => {
    await queryClient.invalidateQueries({ queryKey: ['legacy-ops-accounts'] })
    if (accountId) {
      await queryClient.invalidateQueries({ queryKey: ['legacy-ops-users', accountId] })
    }
  }

  const createAccountMutation = useMutation({
    mutationFn: async () =>
      getOvResult(
        postAdminAccounts({
          body: {
            account_id: createAccountId.trim(),
            admin_user_id: createAdminUserId.trim(),
          },
        }),
      ),
    onError: (error) => {
      setLatestResult({ title: 'Create Account Error', value: getErrorMessage(error) })
    },
    onSuccess: async (result) => {
      setLatestResult({ title: 'Create Account Result', value: result })
      setCreateAccountId('')
      await refreshQueries(selectedAccountId)
    },
  })

  const addUserMutation = useMutation({
    mutationFn: async () =>
      getOvResult(
        postAdminAccountIdUsers({
          body: {
            role: newUserRole,
            user_id: newUserId.trim(),
          },
          path: { account_id: selectedAccountId },
        }),
      ),
    onError: (error) => {
      setLatestResult({ title: 'Add User Error', value: getErrorMessage(error) })
    },
    onSuccess: async (result) => {
      setLatestResult({ title: 'Add User Result', value: result })
      setNewUserId('')
      await refreshQueries(selectedAccountId)
    },
  })

  const roleMutation = useMutation({
    mutationFn: async ({ role, userId }: { role: string; userId: string }) =>
      getOvResult(
        putAdminAccountIdUserIdRole({
          body: { role },
          path: {
            account_id: selectedAccountId,
            user_id: userId,
          },
        }),
      ),
    onError: (error) => {
      setLatestResult({ title: 'Update Role Error', value: getErrorMessage(error) })
    },
    onSuccess: async (result) => {
      setLatestResult({ title: 'Update Role Result', value: result })
      await refreshQueries(selectedAccountId)
    },
  })

  const resetKeyMutation = useMutation({
    mutationFn: async (userId: string) =>
      getOvResult(
        postAdminAccountIdUserIdKey({
          path: {
            account_id: selectedAccountId,
            user_id: userId,
          },
        }),
      ),
    onError: (error) => {
      setLatestResult({ title: 'Reset Key Error', value: getErrorMessage(error) })
    },
    onSuccess: (result) => {
      setLatestResult({ title: 'Reset Key Result', value: result })
    },
  })

  const removeUserMutation = useMutation({
    mutationFn: async (userId: string) =>
      getOvResult(
        deleteAdminAccountIdUserByUserId({
          path: {
            account_id: selectedAccountId,
            user_id: userId,
          },
        }),
      ),
    onError: (error) => {
      setLatestResult({ title: 'Remove User Error', value: getErrorMessage(error) })
    },
    onSuccess: async (result) => {
      setLatestResult({ title: 'Remove User Result', value: result })
      await refreshQueries(selectedAccountId)
    },
  })

  const deleteAccountMutation = useMutation({
    mutationFn: async (accountId: string) =>
      getOvResult(
        deleteAdminAccountByAccountId({
          path: { account_id: accountId },
        }),
      ),
    onError: (error) => {
      setLatestResult({ title: 'Delete Account Error', value: getErrorMessage(error) })
    },
    onSuccess: async (result) => {
      setLatestResult({ title: 'Delete Account Result', value: result })
      await refreshQueries(selectedAccountId)
    },
  })

  const systemMutation = useMutation({
    mutationFn: async () => getOvResult(getSystemStatus()),
    onError: (error) => {
      setLatestResult({ title: 'System Status Error', value: getErrorMessage(error) })
    },
    onSuccess: (result) => {
      setLatestResult({ title: 'System Status', value: result })
    },
  })

  const observerMutation = useMutation({
    mutationFn: async () => getOvResult(getObserverSystem()),
    onError: (error) => {
      setLatestResult({ title: 'Observer Error', value: getErrorMessage(error) })
    },
    onSuccess: (result) => {
      setLatestResult({ title: 'Observer System', value: result })
    },
  })

  const activeError =
    accountsQuery.error ||
    usersQuery.error ||
    createAccountMutation.error ||
    addUserMutation.error ||
    roleMutation.error ||
    resetKeyMutation.error ||
    removeUserMutation.error ||
    deleteAccountMutation.error ||
    systemMutation.error ||
    observerMutation.error

  const filteredAccounts = (accountsQuery.data || []).filter((account) =>
    account.accountId.toLowerCase().includes(accountSearch.trim().toLowerCase()),
  )

  const observerSummary = isRecord(observerMutation.data) && isRecord(observerMutation.data.components)
    ? Object.entries(observerMutation.data.components).map(([key, value]) => `${key}: ${isRecord(value) ? pickFirstNonEmpty([value.status, JSON.stringify(value)]) : String(value)}`)
    : []

  const systemSummary = isRecord(systemMutation.data)
    ? Object.entries(systemMutation.data).map(([key, value]) => `${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`)
    : []

  return (
    <LegacyPageShell
      description="对应旧版的 Tenants 与 Monitor 两块功能，但不复刻 capability 预判和确认弹窗细节。"
      section="ops"
      title="旧控制台运维面板"
    >
        {activeError ? (
          <Alert variant="destructive">
            <ShieldCheck className="size-4" />
            <AlertTitle>请求失败</AlertTitle>
            <AlertDescription>{getErrorMessage(activeError)}</AlertDescription>
          </Alert>
        ) : null}

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(340px,0.7fr)]">
          <div className="grid gap-6">
            <Card>
              <CardHeader>
                <CardTitle>Tenants</CardTitle>
                <CardDescription>账号和用户管理直接对接真实管理接口。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                  <div className="space-y-2">
                    <Label htmlFor="legacy-account-id">New Account ID</Label>
                    <Input id="legacy-account-id" value={createAccountId} onChange={(event) => setCreateAccountId(event.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="legacy-admin-user-id">Initial Admin User</Label>
                    <Input id="legacy-admin-user-id" value={createAdminUserId} onChange={(event) => setCreateAdminUserId(event.target.value)} />
                  </div>
                  <div className="flex items-end">
                    <Button
                      onClick={() => {
                        if (!createAccountId.trim() || !createAdminUserId.trim()) {
                          setLatestResult({ title: 'Create Account Error', value: '请填写 account_id 和 admin_user_id。' })
                          return
                        }
                        createAccountMutation.mutate()
                      }}
                    >
                      {createAccountMutation.isPending ? '创建中...' : 'Create'}
                    </Button>
                  </div>
                </div>

                <div className="grid gap-6 lg:grid-cols-2">
                  <div className="space-y-4">
                    <div className="flex items-center gap-3">
                      <Input placeholder="Filter account_id" value={accountSearch} onChange={(event) => setAccountSearch(event.target.value)} />
                      <Button variant="outline" onClick={() => accountsQuery.refetch()}>
                        刷新
                      </Button>
                    </div>

                    <div className="rounded-2xl border border-border/70">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>account_id</TableHead>
                            <TableHead>user_count</TableHead>
                            <TableHead className="text-right">actions</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {accountsQuery.isLoading ? (
                            <TableRow>
                              <TableCell colSpan={3}>正在加载 accounts...</TableCell>
                            </TableRow>
                          ) : null}
                          {!accountsQuery.isLoading && !filteredAccounts.length ? (
                            <TableRow>
                              <TableCell colSpan={3}>暂无 accounts。</TableCell>
                            </TableRow>
                          ) : null}
                          {filteredAccounts.map((account) => (
                            <TableRow key={account.accountId} data-state={selectedAccountId === account.accountId ? 'selected' : undefined}>
                              <TableCell>
                                <button className="text-left font-medium text-foreground hover:text-primary" type="button" onClick={() => setSelectedAccountId(account.accountId)}>
                                  {account.accountId}
                                </button>
                              </TableCell>
                              <TableCell>{account.userCount || '-'}</TableCell>
                              <TableCell>
                                <div className="flex justify-end gap-2">
                                  <Button
                                    size="sm"
                                    variant="destructive"
                                    onClick={() => {
                                      if (!window.confirm(`删除 account ${account.accountId} ?`)) {
                                        return
                                      }
                                      deleteAccountMutation.mutate(account.accountId)
                                    }}
                                  >
                                    删除
                                  </Button>
                                </div>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  </div>

                  <div className="space-y-4">
                    <div className="rounded-2xl border border-border/70 bg-muted/20 p-4">
                      <div className="mb-3 flex items-center justify-between gap-3">
                        <div>
                          <div className="text-sm font-medium text-foreground">Selected Account</div>
                          <div className="text-sm text-muted-foreground">{selectedAccountId || '尚未选择'}</div>
                        </div>
                        <Badge variant="secondary">{usersQuery.data?.length || 0} users</Badge>
                      </div>
                      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_120px_auto]">
                        <Input placeholder="new user_id" value={newUserId} onChange={(event) => setNewUserId(event.target.value)} />
                        <Input placeholder="role" value={newUserRole} onChange={(event) => setNewUserRole(event.target.value)} />
                        <Button
                          onClick={() => {
                            if (!selectedAccountId) {
                              setLatestResult({ title: 'Add User Error', value: '请先选择 account。' })
                              return
                            }
                            if (!newUserId.trim()) {
                              setLatestResult({ title: 'Add User Error', value: '请填写 user_id。' })
                              return
                            }
                            addUserMutation.mutate()
                          }}
                        >
                          {addUserMutation.isPending ? '添加中...' : 'Add User'}
                        </Button>
                      </div>
                    </div>

                    <div className="rounded-2xl border border-border/70">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>user_id</TableHead>
                            <TableHead>role</TableHead>
                            <TableHead className="text-right">actions</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {usersQuery.isLoading ? (
                            <TableRow>
                              <TableCell colSpan={3}>正在加载 users...</TableCell>
                            </TableRow>
                          ) : null}
                          {!selectedAccountId && !usersQuery.isLoading ? (
                            <TableRow>
                              <TableCell colSpan={3}>请先选择 account。</TableCell>
                            </TableRow>
                          ) : null}
                          {selectedAccountId && !usersQuery.isLoading && !usersQuery.data?.length ? (
                            <TableRow>
                              <TableCell colSpan={3}>当前 account 没有 user。</TableCell>
                            </TableRow>
                          ) : null}
                          {usersQuery.data?.map((user) => (
                            <TableRow key={user.userId}>
                              <TableCell>{user.userId}</TableCell>
                              <TableCell>{user.role || '-'}</TableCell>
                              <TableCell>
                                <div className="flex justify-end gap-2">
                                  <Button size="sm" variant="outline" onClick={() => roleMutation.mutate({ role: user.role === 'admin' ? 'user' : 'admin', userId: user.userId })}>
                                    切换角色
                                  </Button>
                                  <Button size="sm" variant="outline" onClick={() => resetKeyMutation.mutate(user.userId)}>
                                    重置 Key
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="destructive"
                                    onClick={() => {
                                      if (!window.confirm(`从 ${selectedAccountId} 删除用户 ${user.userId} ?`)) {
                                        return
                                      }
                                      removeUserMutation.mutate(user.userId)
                                    }}
                                  >
                                    删除
                                  </Button>
                                </div>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Monitor</CardTitle>
                <CardDescription>保留旧版两个入口按钮，分别对应系统状态和 observer/system。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex flex-wrap gap-3">
                  <Button onClick={() => systemMutation.mutate()}>
                    {systemMutation.isPending ? '加载中...' : 'System Status'}
                  </Button>
                  <Button variant="outline" onClick={() => observerMutation.mutate()}>
                    {observerMutation.isPending ? '加载中...' : 'Observer System'}
                  </Button>
                </div>

                <div className="grid gap-4 lg:grid-cols-2">
                  <div className="rounded-2xl border border-border/70 bg-muted/20 p-4">
                    <div className="mb-3 flex items-center gap-2 font-medium text-foreground">
                      <Activity className="size-4" />
                      System Snapshot
                    </div>
                    <ul className="space-y-2 text-sm text-muted-foreground">
                      {systemSummary.length ? systemSummary.map((item) => <li key={item}>{item}</li>) : <li>尚未加载。</li>}
                    </ul>
                  </div>
                  <div className="rounded-2xl border border-border/70 bg-muted/20 p-4">
                    <div className="mb-3 flex items-center gap-2 font-medium text-foreground">
                      <ShieldCheck className="size-4" />
                      Observer Snapshot
                    </div>
                    <ul className="space-y-2 text-sm text-muted-foreground">
                      {observerSummary.length ? observerSummary.map((item) => <li key={item}>{item}</li>) : <li>尚未加载。</li>}
                    </ul>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-6">
            <Card>
              <CardHeader>
                <CardTitle>Latest Result</CardTitle>
                <CardDescription>最近一次 tenant 或 monitor 请求的原始结果。</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="mb-3 flex flex-wrap gap-2">
                  <Badge variant="secondary">{latestResult?.title || 'Idle'}</Badge>
                  {accountsQuery.isFetching ? <Badge variant="outline">accounts</Badge> : null}
                  {usersQuery.isFetching ? <Badge variant="outline">users</Badge> : null}
                  {createAccountMutation.isPending ? <Badge variant="outline">create account</Badge> : null}
                  {addUserMutation.isPending ? <Badge variant="outline">add user</Badge> : null}
                  {roleMutation.isPending ? <Badge variant="outline">role</Badge> : null}
                  {resetKeyMutation.isPending ? <Badge variant="outline">reset key</Badge> : null}
                  {removeUserMutation.isPending ? <Badge variant="outline">remove user</Badge> : null}
                  {deleteAccountMutation.isPending ? <Badge variant="outline">delete account</Badge> : null}
                </div>
                <pre className="max-h-[70vh] overflow-auto rounded-2xl border border-border/70 bg-muted/20 p-4 text-xs leading-6 whitespace-pre-wrap break-words">
                  {latestResult ? formatResult(latestResult.value) : '尚未执行请求。'}
                </pre>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Ops Scope</CardTitle>
                <CardDescription>对应旧控制台 Ops 分组的两个能力区。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm text-muted-foreground">
                <div className="flex items-start gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <Users className="mt-0.5 size-4 text-foreground" />
                  <p>Tenant 管理不再依赖 capability 开关，实际可写性由服务端直接返回。</p>
                </div>
                <div className="flex items-start gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <KeyRound className="mt-0.5 size-4 text-foreground" />
                  <p>重置 API Key、角色切换、删除等操作统一使用 mutation，页面自动刷新相关 query。</p>
                </div>
                <div className="flex items-start gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <Activity className="mt-0.5 size-4 text-foreground" />
                  <p>Monitor 只保留基本状态查看，不复刻旧版列表格式和结果抽屉细节。</p>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
    </LegacyPageShell>
  )
}