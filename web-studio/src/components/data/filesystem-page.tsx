import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Database } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '#/components/ui/alert'
import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '#/components/ui/card'
import { Input } from '#/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/components/ui/table'
import { LegacyPageShell } from '#/components/legacy/shared/page-shell'
import { getContentRead, getFsLs, getFsStat, getOvResult } from '#/lib/ov-client'
import {
  type LatestResult,
  formatResult,
  getErrorMessage,
  normalizeDirUri,
  normalizeFsEntries,
  normalizeReadContent,
  parentUri,
} from '#/lib/legacy/data-utils'
import {
  applyLegacyConnectionSettings,
  loadLegacyConnectionSettings,
} from '#/lib/legacy/connection'

export function FileSystemPage() {
  const [draftUri, setDraftUri] = useState('viking://')
  const [currentUri, setCurrentUri] = useState('viking://')
  const [latestResult, setLatestResult] = useState<LatestResult | null>(null)

  useEffect(() => {
    applyLegacyConnectionSettings(loadLegacyConnectionSettings())
  }, [])

  const filesystemQuery = useQuery({
    queryKey: ['data-filesystem', currentUri],
    queryFn: async () => {
      const result = await getOvResult(
        getFsLs({
          query: {
            show_all_hidden: true,
            uri: normalizeDirUri(currentUri),
          },
        }),
      )

      return normalizeFsEntries(result, normalizeDirUri(currentUri))
    },
  })

  useEffect(() => {
    setDraftUri(currentUri)
  }, [currentUri])

  const statMutation = useMutation({
    mutationFn: async (uri: string) =>
      getOvResult(
        getFsStat({
          query: { uri },
        }),
      ),
    onError: (error) => {
      setLatestResult({ title: 'Stat Error', value: getErrorMessage(error) })
    },
    onSuccess: (result, uri) => {
      setLatestResult({ title: `Stat: ${uri}`, value: result })
    },
  })

  const readMutation = useMutation({
    mutationFn: async (uri: string) => {
      const result = await getOvResult(
        getContentRead({
          query: {
            limit: -1,
            offset: 0,
            uri,
          },
        }),
      )

      return normalizeReadContent(result)
    },
    onError: (error) => {
      setLatestResult({ title: 'Read Error', value: getErrorMessage(error) })
    },
    onSuccess: (result, uri) => {
      setLatestResult({ title: `Read: ${uri}`, value: result || '(empty file)' })
    },
  })

  const activeError = filesystemQuery.error || readMutation.error || statMutation.error

  return (
    <LegacyPageShell
      description="浏览 viking:// 文件系统，支持目录导航和文件读取。"
      title="FileSystem"
    >
      {activeError ? (
        <Alert variant="destructive">
          <Database className="size-4" />
          <AlertTitle>请求失败</AlertTitle>
          <AlertDescription>{getErrorMessage(activeError)}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(340px,0.7fr)]">
        <Card>
          <CardHeader>
            <CardTitle>FileSystem</CardTitle>
            <CardDescription>基础列表浏览，支持进入目录、读取文件和查看 stat。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col gap-3 lg:flex-row">
              <Input value={draftUri} onChange={(event) => setDraftUri(event.target.value)} />
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => setCurrentUri(normalizeDirUri(draftUri))}>进入</Button>
                <Button variant="outline" onClick={() => setCurrentUri(parentUri(currentUri))}>上一级</Button>
                <Button variant="outline" onClick={() => filesystemQuery.refetch()}>刷新</Button>
              </div>
            </div>

            <div className="rounded-2xl border border-border/70">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>uri</TableHead>
                    <TableHead>size</TableHead>
                    <TableHead>isDir</TableHead>
                    <TableHead>modTime</TableHead>
                    <TableHead>abstract</TableHead>
                    <TableHead className="text-right">actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filesystemQuery.isLoading ? (
                    <TableRow>
                      <TableCell colSpan={6}>正在加载目录...</TableCell>
                    </TableRow>
                  ) : null}
                  {!filesystemQuery.isLoading && !filesystemQuery.data?.length ? (
                    <TableRow>
                      <TableCell colSpan={6}>当前目录没有内容。</TableCell>
                    </TableRow>
                  ) : null}
                  {filesystemQuery.data?.map((entry) => (
                    <TableRow key={entry.uri}>
                      <TableCell>
                        <button
                          className="text-left font-medium text-foreground hover:text-primary"
                          type="button"
                          onClick={() => {
                            if (entry.isDir) {
                              setCurrentUri(normalizeDirUri(entry.uri))
                              return
                            }
                            readMutation.mutate(entry.uri)
                          }}
                        >
                          {entry.uri}
                        </button>
                      </TableCell>
                      <TableCell>{entry.size || '-'}</TableCell>
                      <TableCell>{entry.isDir ? 'true' : 'false'}</TableCell>
                      <TableCell>{entry.modTime || '-'}</TableCell>
                      <TableCell className="max-w-[240px] whitespace-normal text-muted-foreground">{entry.abstract || '-'}</TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-2">
                          {!entry.isDir ? (
                            <Button size="sm" variant="outline" onClick={() => readMutation.mutate(entry.uri)}>
                              读取
                            </Button>
                          ) : null}
                          <Button size="sm" variant="outline" onClick={() => statMutation.mutate(entry.uri)}>
                            Stat
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Latest Result</CardTitle>
            <CardDescription>最近一次请求的原始结果。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="mb-3 flex flex-wrap gap-2">
              <Badge variant="secondary">{latestResult?.title || 'Idle'}</Badge>
              {filesystemQuery.isFetching ? <Badge variant="outline">filesystem loading</Badge> : null}
              {readMutation.isPending ? <Badge variant="outline">reading</Badge> : null}
              {statMutation.isPending ? <Badge variant="outline">stat</Badge> : null}
            </div>
            <pre className="max-h-[70vh] overflow-auto rounded-2xl border border-border/70 bg-muted/20 p-4 text-xs leading-6 whitespace-pre-wrap break-words">
              {latestResult ? formatResult(latestResult.value) : '尚未执行请求。'}
            </pre>
          </CardContent>
        </Card>
      </div>
    </LegacyPageShell>
  )
}
