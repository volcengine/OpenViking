import { useEffect, useState } from 'react'
import { Server, Shield, UserRound, WandSparkles } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '#/components/ui/alert'
import { Button } from '#/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '#/components/ui/card'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { LegacyPageShell } from '#/components/legacy/shared/page-shell'
import {
  applyLegacyConnectionSettings,
  clearLegacyConnectionSettings,
  getDefaultBaseUrl,
  loadLegacyConnectionSettings,
  persistLegacyConnectionSettings,
  type LegacyConnectionSettings,
} from '#/lib/legacy/connection'

export function AccessLegacyPage() {
  const [message, setMessage] = useState('')
  const [settings, setSettings] = useState<LegacyConnectionSettings>(() => loadLegacyConnectionSettings())

  useEffect(() => {
    applyLegacyConnectionSettings(settings)
  }, [])

  const connectionSummary = [
    settings.apiKey ? `API Key 已载入会话存储 (${settings.apiKey.length} chars)` : '未设置 API Key',
    settings.accountId ? `account=${settings.accountId}` : '未设置 account',
    settings.userId ? `user=${settings.userId}` : '未设置 user',
    settings.agentId ? `agent=${settings.agentId}` : '未设置 agent',
  ]

  return (
    <LegacyPageShell
      description="这里只保留旧前端的连接入口，不再复刻 capability 预判。所有请求都会直接走真实 OpenViking 服务。"
      section="access"
      title="旧控制台访问配置"
    >
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
          <Card>
            <CardHeader>
              <CardTitle>Connection Settings</CardTitle>
              <CardDescription>使用旧控制台的连接字段，但直接驱动新的 ov-client 适配层。</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="legacy-base-url">Base URL</Label>
                  <Input
                    id="legacy-base-url"
                    placeholder="http://127.0.0.1:1933"
                    value={settings.baseUrl}
                    onChange={(event) => setSettings((current) => ({ ...current, baseUrl: event.target.value }))}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="legacy-api-key">X-API-Key</Label>
                  <Input
                    id="legacy-api-key"
                    placeholder="Paste session API key"
                    type="password"
                    value={settings.apiKey}
                    onChange={(event) => setSettings((current) => ({ ...current, apiKey: event.target.value }))}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="legacy-account">X-OpenViking-Account</Label>
                  <Input
                    id="legacy-account"
                    placeholder="default"
                    value={settings.accountId}
                    onChange={(event) => setSettings((current) => ({ ...current, accountId: event.target.value }))}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="legacy-user">X-OpenViking-User</Label>
                  <Input
                    id="legacy-user"
                    placeholder="admin"
                    value={settings.userId}
                    onChange={(event) => setSettings((current) => ({ ...current, userId: event.target.value }))}
                  />
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label htmlFor="legacy-agent">X-OpenViking-Agent</Label>
                  <Input
                    id="legacy-agent"
                    placeholder="console-legacy"
                    value={settings.agentId}
                    onChange={(event) => setSettings((current) => ({ ...current, agentId: event.target.value }))}
                  />
                </div>
              </div>

              <div className="flex flex-wrap gap-3">
                <Button
                  onClick={() => {
                    const nextSettings = persistLegacyConnectionSettings({
                      ...settings,
                      baseUrl: settings.baseUrl.trim() || getDefaultBaseUrl(),
                    })
                    applyLegacyConnectionSettings(nextSettings)
                    setSettings(nextSettings)
                    setMessage('连接设置已保存并同步到 ov-client。')
                  }}
                >
                  保存设置
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    const cleared = clearLegacyConnectionSettings()
                    setSettings(cleared)
                    setMessage('已清除本地 legacy 连接设置。')
                  }}
                >
                  清空设置
                </Button>
              </div>

              {message ? (
                <Alert>
                  <Shield className="size-4" />
                  <AlertTitle>状态</AlertTitle>
                  <AlertDescription>{message}</AlertDescription>
                </Alert>
              ) : null}
            </CardContent>
          </Card>

          <div className="grid gap-6">
            <Card>
              <CardHeader>
                <CardTitle>Current Runtime</CardTitle>
                <CardDescription>这些字段会被直接注入真实后端请求头。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm text-muted-foreground">
                <div className="flex items-center gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <Server className="size-4 text-foreground" />
                  <div>
                    <div className="font-medium text-foreground">Base URL</div>
                    <div className="break-all">{settings.baseUrl || '未设置'}</div>
                  </div>
                </div>
                <div className="flex items-center gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <Shield className="size-4 text-foreground" />
                  <div>
                    <div className="font-medium text-foreground">API Key</div>
                    <div>{settings.apiKey ? '已加载到 sessionStorage' : '未加载'}</div>
                  </div>
                </div>
                <div className="flex items-center gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <UserRound className="size-4 text-foreground" />
                  <div>
                    <div className="font-medium text-foreground">Identity Headers</div>
                    <div>{connectionSummary.slice(1).join(' / ')}</div>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>迁移说明</CardTitle>
                <CardDescription>这里保留旧入口，但接口行为已经切换到新前端约定。</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm text-muted-foreground">
                <div className="flex items-start gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <WandSparkles className="mt-0.5 size-4 text-foreground" />
                  <p>不再依赖旧控制台的 capability 接口，也不会在前端预判读写能力。</p>
                </div>
                <div className="flex items-start gap-3 rounded-2xl border border-border/70 bg-muted/20 p-3">
                  <Shield className="mt-0.5 size-4 text-foreground" />
                  <p>API Key 继续使用 ov_console_api_key 这个会话存储键，身份字段保存在本地浏览器设置中。</p>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
    </LegacyPageShell>
  )
}