import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Search } from 'lucide-react'

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
import { getOvResult, postSearchFind } from '#/lib/ov-client'
import {
  applyLegacyConnectionSettings,
  loadLegacyConnectionSettings,
} from '#/lib/legacy/connection'
import {
  collectFindColumns,
  formatResult,
  getErrorMessage,
  normalizeFindRows,
  type FindRow,
  type LatestResult,
} from '#/lib/legacy/data-utils'

export function FindPage() {
  const [findLimit, setFindLimit] = useState('10')
  const [findQuery, setFindQuery] = useState('')
  const [findRows, setFindRows] = useState<Array<FindRow>>([])
  const [findTargetUri, setFindTargetUri] = useState('')
  const [latestResult, setLatestResult] = useState<LatestResult | null>(null)

  useEffect(() => {
    applyLegacyConnectionSettings(loadLegacyConnectionSettings())
  }, [])

  const findMutation = useMutation({
    mutationFn: async () => {
      const parsedLimit = Number.parseInt(findLimit, 10)
      const result = await getOvResult(
        postSearchFind({
          body: {
            limit: Number.isInteger(parsedLimit) && parsedLimit > 0 ? parsedLimit : undefined,
            query: findQuery.trim(),
            target_uri: findTargetUri.trim() || undefined,
          },
        }),
      )

      return {
        rows: normalizeFindRows(result),
        result,
      }
    },
    onError: (error) => {
      setLatestResult({ title: 'Find Error', value: getErrorMessage(error) })
    },
    onSuccess: ({ rows, result }) => {
      setFindRows(rows)
      setLatestResult({ title: 'Find Result', value: result })
    },
  })

  const findColumns = collectFindColumns(findRows)
  const activeError = findMutation.error

  return (
    <LegacyPageShell
      description="语义搜索，直接调用 /api/v1/search/find 接口。"
      title="Find"
    >
      {activeError ? (
        <Alert variant="destructive">
          <Search className="size-4" />
          <AlertTitle>请求失败</AlertTitle>
          <AlertDescription>{getErrorMessage(activeError)}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(340px,0.7fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Find</CardTitle>
            <CardDescription>沿用旧版入口，但直接调用真实的 /api/v1/search/find。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_120px]">
              <div className="space-y-2">
                <Label htmlFor="find-query">Query</Label>
                <Input id="find-query" value={findQuery} onChange={(event) => setFindQuery(event.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="find-target">Target URI</Label>
                <Input id="find-target" placeholder="viking://resources/" value={findTargetUri} onChange={(event) => setFindTargetUri(event.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="find-limit">Limit</Label>
                <Input id="find-limit" value={findLimit} onChange={(event) => setFindLimit(event.target.value)} />
              </div>
            </div>

            <Button
              onClick={() => {
                if (!findQuery.trim()) {
                  setLatestResult({ title: 'Find Error', value: 'Query 不能为空。' })
                  return
                }
                findMutation.mutate()
              }}
            >
              {findMutation.isPending ? '查询中...' : '运行 Find'}
            </Button>

            <div className="rounded-2xl border border-border/70">
              <Table>
                <TableHeader>
                  <TableRow>
                    {findColumns.map((column) => (
                      <TableHead key={column}>{column}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {!findRows.length ? (
                    <TableRow>
                      <TableCell colSpan={findColumns.length}>暂无结果。</TableCell>
                    </TableRow>
                  ) : null}
                  {findRows.map((row, index) => (
                    <TableRow key={`${index}-${String(row.uri || row.id || row.value || 'row')}`}>
                      {findColumns.map((column) => (
                        <TableCell key={column} className="max-w-[260px] whitespace-normal align-top">
                          {typeof row[column] === 'string' ? String(row[column]) : formatResult(row[column] ?? '-')}
                        </TableCell>
                      ))}
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
              {findMutation.isPending ? <Badge variant="outline">find</Badge> : null}
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
