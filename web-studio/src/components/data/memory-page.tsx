import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Brain } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '#/components/ui/alert'
import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '#/components/ui/card'
import { Textarea } from '#/components/ui/textarea'
import { LegacyPageShell } from '#/components/legacy/shared/page-shell'
import { type LatestResult, isRecord, formatResult, getErrorMessage } from '#/lib/legacy/data-utils'
import {
  getOvResult,
  postSessionIdCommit,
  postSessionIdMessages,
  postSessions,
  type AddMessageRequest,
} from '#/lib/ov-client'
import { applyLegacyConnectionSettings, loadLegacyConnectionSettings } from '#/lib/legacy/connection'

export function MemoryPage() {
  const [memoryInput, setMemoryInput] = useState('')
  const [latestResult, setLatestResult] = useState<LatestResult | null>(null)

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

  useEffect(() => {
    applyLegacyConnectionSettings(loadLegacyConnectionSettings())
  }, [])

  const activeError = addMemoryMutation.error

  return (
    <LegacyPageShell title="Add Memory" description="创建 session、写入消息、提交 commit，将内容存入 memory。">
      {activeError ? (
        <Alert variant="destructive">
          <Brain className="size-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{getErrorMessage(activeError)}</AlertDescription>
        </Alert>
      ) : null}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(340px,0.7fr)]">
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
        <Card>
          <CardHeader>
            <CardTitle>Latest Result</CardTitle>
            <CardDescription>最近一次请求的原始结果。</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="mb-3 flex flex-wrap gap-2">
              <Badge variant="secondary">{latestResult?.title || 'Idle'}</Badge>
              {addMemoryMutation.isPending ? <Badge variant="outline">memory</Badge> : null}
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
