import * as React from 'react'
import { Loader2Icon, PlugZapIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAppConnection } from '#/hooks/use-app-connection'
import { Button } from '#/components/ui/button'
import {
  ModelTestError,
  supportsModelTest,
  testModelConnection,
} from '../-lib/model-connection-test'
import type { ModelConfig, ModelKind } from '../-lib/config-file-api'

export function ModelConnectionTest({
  kind,
  config,
  fallback,
  thinking,
  mode,
  disabled,
}: {
  kind: ModelKind
  config: ModelConfig
  fallback: ModelConfig
  thinking?: boolean
  mode?: string
  disabled: boolean
}) {
  const { t } = useTranslation('settings')
  const { connection, serverMode } = useAppConnection()
  const [result, setResult] = React.useState<{
    code: string
    ms?: number
  } | null>(null)
  const [testing, setTesting] = React.useState(false)
  const active = React.useRef<AbortController | null>(null)
  React.useEffect(() => {
    setResult(null)
    setTesting(false)
    return () => {
      active.current?.abort()
      active.current = null
    }
  }, [
    kind,
    config,
    fallback,
    thinking,
    mode,
    connection.baseUrl,
    connection.adminApiKey,
    connection.apiKey,
    connection.accountId,
    connection.userId,
    serverMode,
  ])
  const supported = supportsModelTest(kind, config.provider)
  async function test() {
    if (testing || disabled || !supported) return
    const controller = new AbortController()
    active.current = controller
    setTesting(true)
    setResult(null)
    const timer = setTimeout(() => {
      controller.abort(new DOMException('Timed out', 'TimeoutError'))
    }, 30_000)
    try {
      const ms = await testModelConnection({
        connection,
        trusted: serverMode === 'trusted',
        mode,
        kind,
        config,
        fallback,
        thinking,
        signal: controller.signal,
      })
      if (!controller.signal.aborted) setResult({ code: 'success', ms })
    } catch (error) {
      if (active.current !== controller) return
      if (
        controller.signal.aborted &&
        controller.signal.reason instanceof DOMException &&
        controller.signal.reason.name === 'TimeoutError'
      )
        setResult({ code: 'timeout' })
      else if (!controller.signal.aborted)
        setResult(
          error instanceof ModelTestError
            ? { code: error.code }
            : { code: 'failed' },
        )
    } finally {
      clearTimeout(timer)
      if (active.current === controller) setTesting(false)
    }
  }
  return (
    <div className="grid gap-2 border-t pt-4">
      <div>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || testing || !supported}
          onClick={() => void test()}
        >
          {testing ? <Loader2Icon className="animate-spin" /> : <PlugZapIcon />}
          {t(
            testing
              ? 'models.connectionTest.testing'
              : 'models.connectionTest.action',
          )}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        {t(
          supported
            ? 'models.connectionTest.hint'
            : 'models.connectionTest.unsupported',
        )}
      </p>
      {result && (
        <p
          role={result.code === 'success' ? 'status' : 'alert'}
          className={`break-all text-sm ${result.code === 'success' ? 'text-emerald-700 dark:text-emerald-400' : 'text-destructive'}`}
        >
          {t(`models.connectionTest.${result.code}`, { ms: result.ms })}
        </p>
      )}
    </div>
  )
}
