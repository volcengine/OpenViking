import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Database, FileText, FolderTree, Search, Upload } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '#/components/ui/alert'
import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '#/components/ui/card'
import { Checkbox } from '#/components/ui/checkbox'
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
import { Textarea } from '#/components/ui/textarea'
import { LegacyPageShell } from '#/components/legacy/shared/page-shell'
import {
  OvClientError,
  getContentRead,
  getFsLs,
  getFsStat,
  getOvResult,
  postResources,
  postResourcesTempUpload,
  postSearchFind,
  postSessionIdCommit,
  postSessionIdMessages,
  postSessions,
  type AddMessageRequest,
  type AddResourceRequest,
} from '#/lib/ov-client'
import {
  applyLegacyConnectionSettings,
  loadLegacyConnectionSettings,
} from '#/lib/legacy/connection'

type FsEntry = {
  abstract: string
  isDir: boolean
  modTime: string
  size: string
  uri: string
}

type FindRow = Record<string, unknown>

type LatestResult = {
  title: string
  value: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function pickFirstNonEmpty(values: Array<unknown>): unknown {
  for (const value of values) {
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      return value
    }
  }
  return ''
}

function normalizeDirUri(uri: string): string {
  const value = uri.trim()
  if (!value) {
    return 'viking://'
  }
  if (value === 'viking://') {
    return value
  }
  return value.endsWith('/') ? value : `${value}/`
}

function parentUri(uri: string): string {
  const normalized = normalizeDirUri(uri)
  if (normalized === 'viking://') {
    return normalized
  }

  const body = normalized.slice('viking://'.length, -1)
  if (!body.includes('/')) {
    return 'viking://'
  }

  return `viking://${body.slice(0, body.lastIndexOf('/') + 1)}`
}

function joinUri(baseUri: string, child: string): string {
  const raw = child.trim()
  if (!raw) {
    return normalizeDirUri(baseUri)
  }
  if (raw.startsWith('viking://')) {
    return raw
  }
  return `${normalizeDirUri(baseUri)}${raw.replace(/^\//, '')}`
}

function normalizeFsEntries(result: unknown, currentUri: string): Array<FsEntry> {
  const toEntry = (item: unknown): FsEntry => {
    if (typeof item === 'string') {
      const isDir = item.endsWith('/')
      const uri = joinUri(currentUri, item)
      return {
        abstract: '',
        isDir,
        modTime: '',
        size: '',
        uri: isDir ? normalizeDirUri(uri) : uri,
      }
    }

    if (isRecord(item)) {
      const label = String(
        pickFirstNonEmpty([item.name, item.path, item.relative_path, item.uri, item.id, 'unknown']),
      )
      const isDir =
        Boolean(item.is_dir) ||
        Boolean(item.isDir) ||
        item.type === 'dir' ||
        item.type === 'directory' ||
        label.endsWith('/')

      const uri = String(pickFirstNonEmpty([item.uri, item.path, item.relative_path, label]))
      return {
        abstract: String(pickFirstNonEmpty([item.abstract, item.summary, item.description])),
        isDir,
        modTime: String(
          pickFirstNonEmpty([
            item.modTime,
            item.mod_time,
            item.modified_at,
            item.modifiedAt,
            item.updated_at,
            item.updatedAt,
          ]),
        ),
        size: String(
          pickFirstNonEmpty([item.size, item.size_bytes, item.content_length, item.contentLength]),
        ),
        uri: isDir ? normalizeDirUri(joinUri(currentUri, uri)) : joinUri(currentUri, uri),
      }
    }

    return {
      abstract: '',
      isDir: false,
      modTime: '',
      size: '',
      uri: String(item),
    }
  }

  if (Array.isArray(result)) {
    return result.map(toEntry)
  }

  if (isRecord(result)) {
    const buckets = [result.entries, result.items, result.children, result.results]
    for (const bucket of buckets) {
      if (Array.isArray(bucket)) {
        return bucket.map(toEntry)
      }
    }
  }

  return []
}

function normalizeReadContent(result: unknown): string {
  if (typeof result === 'string') {
    return result
  }
  if (Array.isArray(result)) {
    return result.map((item) => String(item)).join('\n')
  }
  if (isRecord(result)) {
    const content = pickFirstNonEmpty([result.content, result.text, result.body, result.value, result.data])
    if (typeof content === 'string') {
      return content
    }
  }
  return JSON.stringify(result, null, 2)
}

function extractDeepestObjectArray(value: unknown): Array<Record<string, unknown>> | null {
  let bestDepth = -1
  let best: Array<Record<string, unknown>> | null = null

  const visit = (candidate: unknown, depth: number) => {
    if (Array.isArray(candidate)) {
      if (candidate.length > 0 && candidate.every((item) => isRecord(item)) && depth > bestDepth) {
        bestDepth = depth
        best = candidate as Array<Record<string, unknown>>
      }

      for (const item of candidate) {
        visit(item, depth + 1)
      }
      return
    }

    if (!isRecord(candidate)) {
      return
    }

    for (const nested of Object.values(candidate)) {
      visit(nested, depth + 1)
    }
  }

  visit(value, 0)
  return best
}

function normalizeFindRows(result: unknown): Array<FindRow> {
  if (Array.isArray(result)) {
    return result.map((item) => (isRecord(item) ? item : { value: item }))
  }

  if (isRecord(result)) {
    const topLevelArrays = [result.results, result.items, result.matches, result.hits, result.rows, result.entries]
    for (const value of topLevelArrays) {
      if (Array.isArray(value)) {
        return value.map((item) => (isRecord(item) ? item : { value: item }))
      }
    }

    const deepRows = extractDeepestObjectArray(result)
    if (deepRows) {
      return deepRows
    }

    return [result]
  }

  if (result === null || result === undefined) {
    return []
  }

  return [{ value: result }]
}

function collectFindColumns(rows: Array<FindRow>): Array<string> {
  const columns: Array<string> = []
  const seen = new Set<string>()

  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key)
        columns.push(key)
      }
    }
  }

  return columns.length ? columns : ['value']
}

function formatResult(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  return JSON.stringify(value, null, 2)
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

export function DataLegacyPage() {
  const [draftUri, setDraftUri] = useState('viking://')
  const [currentUri, setCurrentUri] = useState('viking://')
  const [findLimit, setFindLimit] = useState('10')
  const [findQuery, setFindQuery] = useState('')
  const [findRows, setFindRows] = useState<Array<FindRow>>([])
  const [findTargetUri, setFindTargetUri] = useState('')
  const [latestResult, setLatestResult] = useState<LatestResult | null>(null)
  const [memoryInput, setMemoryInput] = useState('')
  const [resourceExclude, setResourceExclude] = useState('')
  const [resourceFile, setResourceFile] = useState<File | null>(null)
  const [resourceIgnoreDirs, setResourceIgnoreDirs] = useState('')
  const [resourceInclude, setResourceInclude] = useState('')
  const [resourceInstruction, setResourceInstruction] = useState('')
  const [resourceMode, setResourceMode] = useState<'path' | 'upload'>('path')
  const [resourcePath, setResourcePath] = useState('')
  const [resourceReason, setResourceReason] = useState('')
  const [resourceStrict, setResourceStrict] = useState(true)
  const [resourceTargetUri, setResourceTargetUri] = useState('')
  const [resourceTimeout, setResourceTimeout] = useState('')
  const [resourceUploadMedia, setResourceUploadMedia] = useState(true)
  const [resourceWait, setResourceWait] = useState(false)

  useEffect(() => {
    applyLegacyConnectionSettings(loadLegacyConnectionSettings())
  }, [])

  const filesystemQuery = useQuery({
    queryKey: ['legacy-data-filesystem', currentUri],
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

  const addResourceMutation = useMutation({
    mutationFn: async () => {
      const request: AddResourceRequest = {
        directly_upload_media: resourceUploadMedia,
        exclude: resourceExclude.trim() || undefined,
        ignore_dirs: resourceIgnoreDirs.trim() || undefined,
        include: resourceInclude.trim() || undefined,
        instruction: resourceInstruction.trim() || undefined,
        reason: resourceReason.trim() || undefined,
        strict: resourceStrict,
        timeout:
          resourceTimeout.trim() && Number.isFinite(Number(resourceTimeout))
            ? Number(resourceTimeout)
            : undefined,
        to: resourceTargetUri.trim() || undefined,
        wait: resourceWait,
      }

      if (resourceMode === 'path') {
        if (!resourcePath.trim()) {
          throw new Error('请输入 OpenViking 可访问的资源路径。')
        }

        request.path = resourcePath.trim()
        return getOvResult(postResources({ body: request }))
      }

      if (!resourceFile) {
        throw new Error('请先选择需要上传的文件。')
      }

      const uploadResult = await getOvResult(
        postResourcesTempUpload({
          body: {
            file: resourceFile,
            telemetry: true,
          },
        }),
      )

      const tempFileId = isRecord(uploadResult) ? uploadResult.temp_file_id : undefined
      if (typeof tempFileId !== 'string' || !tempFileId.trim()) {
        throw new Error('临时上传成功，但未返回 temp_file_id。')
      }

      request.temp_file_id = tempFileId

      return {
        addResource: await getOvResult(postResources({ body: request })),
        upload: uploadResult,
      }
    },
    onError: (error) => {
      setLatestResult({ title: 'Add Resource Error', value: getErrorMessage(error) })
    },
    onSuccess: (result) => {
      setLatestResult({ title: 'Add Resource Result', value: result })
    },
  })

  const addMemoryMutation = useMutation({
    mutationFn: async () => {
      const text = memoryInput.trim()
      if (!text) {
        throw new Error('请输入要提交的 memory 内容。')
      }

      let messages: Array<AddMessageRequest>
      try {
        const parsed = JSON.parse(text) as unknown
        messages = Array.isArray(parsed)
          ? (parsed as Array<AddMessageRequest>)
          : [{ content: text, role: 'user' }]
      } catch {
        messages = [{ content: text, role: 'user' }]
      }

      const session = await getOvResult(postSessions({ body: {} }))
      const sessionId = isRecord(session) ? session.session_id : undefined
      if (typeof sessionId !== 'string' || !sessionId.trim()) {
        throw new Error('创建 session 失败，未返回 session_id。')
      }

      for (const message of messages) {
        await getOvResult(
          postSessionIdMessages({
            body: message,
            path: { session_id: sessionId },
          }),
        )
      }

      const commit = await getOvResult(
        postSessionIdCommit({
          path: { session_id: sessionId },
        }),
      )

      return {
        commit,
        session,
      }
    },
    onError: (error) => {
      setLatestResult({ title: 'Add Memory Error', value: getErrorMessage(error) })
    },
    onSuccess: (result) => {
      setLatestResult({ title: 'Add Memory Result', value: result })
    },
  })

  const findColumns = collectFindColumns(findRows)
  const activeError =
    filesystemQuery.error ||
    findMutation.error ||
    addResourceMutation.error ||
    addMemoryMutation.error ||
    readMutation.error ||
    statMutation.error

  return (
    <LegacyPageShell
      description="复刻旧版数据操作入口，但请求直接走真实后端接口，加载和提交状态统一由 TanStack Query 管理。"
      section="data"
      title="旧控制台数据面板"
    >
        {activeError ? (
          <Alert variant="destructive">
            <Database className="size-4" />
            <AlertTitle>请求失败</AlertTitle>
            <AlertDescription>{getErrorMessage(activeError)}</AlertDescription>
          </Alert>
        ) : null}

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(340px,0.7fr)]">
          <div className="grid gap-6">
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
                <CardTitle>Find</CardTitle>
                <CardDescription>沿用旧版入口，但直接调用真实的 /api/v1/search/find。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-4 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_120px]">
                  <div className="space-y-2">
                    <Label htmlFor="legacy-find-query">Query</Label>
                    <Input id="legacy-find-query" value={findQuery} onChange={(event) => setFindQuery(event.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="legacy-find-target">Target URI</Label>
                    <Input id="legacy-find-target" placeholder="viking://resources/" value={findTargetUri} onChange={(event) => setFindTargetUri(event.target.value)} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="legacy-find-limit">Limit</Label>
                    <Input id="legacy-find-limit" value={findLimit} onChange={(event) => setFindLimit(event.target.value)} />
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

            <div className="grid gap-6 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Add Resource</CardTitle>
                  <CardDescription>保留旧版 path/upload 两种入口，但参数映射到真实后端。</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex flex-wrap gap-2">
                    <Button variant={resourceMode === 'path' ? 'default' : 'outline'} onClick={() => setResourceMode('path')}>
                      Path
                    </Button>
                    <Button variant={resourceMode === 'upload' ? 'default' : 'outline'} onClick={() => setResourceMode('upload')}>
                      Upload
                    </Button>
                  </div>

                  {resourceMode === 'path' ? (
                    <div className="space-y-2">
                      <Label htmlFor="legacy-resource-path">Source Path</Label>
                      <Input id="legacy-resource-path" placeholder="/abs/path/on/server/or/repo" value={resourcePath} onChange={(event) => setResourcePath(event.target.value)} />
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <Label htmlFor="legacy-resource-file">Upload File</Label>
                      <Input id="legacy-resource-file" type="file" onChange={(event) => setResourceFile(event.target.files?.[0] || null)} />
                    </div>
                  )}

                  <div className="space-y-2">
                    <Label htmlFor="legacy-resource-target">Target URI</Label>
                    <Input id="legacy-resource-target" placeholder="viking://resources/my-resource" value={resourceTargetUri} onChange={(event) => setResourceTargetUri(event.target.value)} />
                  </div>

                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="legacy-resource-timeout">Timeout</Label>
                      <Input id="legacy-resource-timeout" placeholder="30" value={resourceTimeout} onChange={(event) => setResourceTimeout(event.target.value)} />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="legacy-resource-ignore">Ignore Dirs</Label>
                      <Input id="legacy-resource-ignore" placeholder=".git,node_modules" value={resourceIgnoreDirs} onChange={(event) => setResourceIgnoreDirs(event.target.value)} />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="legacy-resource-include">Include</Label>
                      <Input id="legacy-resource-include" placeholder="*.md,*.txt" value={resourceInclude} onChange={(event) => setResourceInclude(event.target.value)} />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="legacy-resource-exclude">Exclude</Label>
                      <Input id="legacy-resource-exclude" placeholder="*.log,*.tmp" value={resourceExclude} onChange={(event) => setResourceExclude(event.target.value)} />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="legacy-resource-reason">Reason</Label>
                    <Textarea id="legacy-resource-reason" rows={2} value={resourceReason} onChange={(event) => setResourceReason(event.target.value)} />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="legacy-resource-instruction">Instruction</Label>
                    <Textarea id="legacy-resource-instruction" rows={3} value={resourceInstruction} onChange={(event) => setResourceInstruction(event.target.value)} />
                  </div>

                  <div className="grid gap-3 sm:grid-cols-3">
                    <Label htmlFor="legacy-resource-wait" className="rounded-2xl border border-border/70 bg-muted/20 p-3">
                      <Checkbox checked={resourceWait} onCheckedChange={(checked) => setResourceWait(Boolean(checked))} id="legacy-resource-wait" />
                      wait
                    </Label>
                    <Label htmlFor="legacy-resource-strict" className="rounded-2xl border border-border/70 bg-muted/20 p-3">
                      <Checkbox checked={resourceStrict} onCheckedChange={(checked) => setResourceStrict(Boolean(checked))} id="legacy-resource-strict" />
                      strict
                    </Label>
                    <Label htmlFor="legacy-resource-upload-media" className="rounded-2xl border border-border/70 bg-muted/20 p-3">
                      <Checkbox checked={resourceUploadMedia} onCheckedChange={(checked) => setResourceUploadMedia(Boolean(checked))} id="legacy-resource-upload-media" />
                      directly_upload_media
                    </Label>
                  </div>

                  <Button onClick={() => addResourceMutation.mutate()}>
                    {addResourceMutation.isPending ? '提交中...' : 'Add Resource'}
                  </Button>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Add Memory</CardTitle>
                  <CardDescription>按照旧版流程创建 session、写入消息、再提交 commit。</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <Textarea
                    rows={12}
                    placeholder='纯文本会按单条 user message 处理；也可以输入 JSON 数组 [{"role":"user","content":"..."}]'
                    value={memoryInput}
                    onChange={(event) => setMemoryInput(event.target.value)}
                  />
                  <Button onClick={() => addMemoryMutation.mutate()}>
                    {addMemoryMutation.isPending ? '提交中...' : 'Add Memory'}
                  </Button>
                </CardContent>
              </Card>
            </div>
          </div>

          <div className="grid gap-6">
            <Card>
              <CardHeader>
                <CardTitle>Latest Result</CardTitle>
                <CardDescription>代替旧控制台右侧输出区，展示最近一次请求结果。</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="mb-3 flex flex-wrap gap-2">
                  <Badge variant="secondary">{latestResult?.title || 'Idle'}</Badge>
                  {filesystemQuery.isFetching ? <Badge variant="outline">filesystem loading</Badge> : null}
                  {readMutation.isPending ? <Badge variant="outline">reading</Badge> : null}
                  {statMutation.isPending ? <Badge variant="outline">stat</Badge> : null}
                  {findMutation.isPending ? <Badge variant="outline">find</Badge> : null}
                  {addResourceMutation.isPending ? <Badge variant="outline">resource</Badge> : null}
                  {addMemoryMutation.isPending ? <Badge variant="outline">memory</Badge> : null}
                </div>
                <pre className="max-h-[70vh] overflow-auto rounded-2xl border border-border/70 bg-muted/20 p-4 text-xs leading-6 whitespace-pre-wrap break-words">
                  {latestResult ? formatResult(latestResult.value) : '尚未执行请求。'}
                </pre>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Data Scope</CardTitle>
                <CardDescription>对应旧控制台 Data 分组中的核心能力。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm text-muted-foreground">
                <div className="flex items-start gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <FolderTree className="mt-0.5 size-4 text-foreground" />
                  <p>FileSystem 仅保留基础目录浏览和内容读取，不复刻旧版树形状态与结果面板联动。</p>
                </div>
                <div className="flex items-start gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <Search className="mt-0.5 size-4 text-foreground" />
                  <p>Find 结果会按动态列展示，避免绑定旧 BFF 的特定结构。</p>
                </div>
                <div className="flex items-start gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <Upload className="mt-0.5 size-4 text-foreground" />
                  <p>Add Resource 和 Add Memory 都直接走真实后端路径与真实参数模型。</p>
                </div>
                <div className="flex items-start gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <FileText className="mt-0.5 size-4 text-foreground" />
                  <p>所有加载与提交状态均由 TanStack Query 暴露，页面只负责渲染。</p>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
    </LegacyPageShell>
  )
}