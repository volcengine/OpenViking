import styles from './server-configuration.module.css'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import type {
  ModelConfig,
  ModelKind,
} from '#/features/admin/server-configuration/lib/config-file-api'
import {
  ModelTestError,
  supportsModelTest,
  testModelConnection,
} from '#/features/admin/server-configuration/lib/model-connection-test'
import { Button } from '#/shared/ui/button'
import { Loader2Icon, PlugZapIcon } from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

export function ModelConnectionTest({
  kind,
  config,
  fallback,
  thinking,
  mode,
  disabled,
  variant = 'panel',
  children,
}: {
  kind: ModelKind
  config: ModelConfig
  fallback: ModelConfig
  thinking?: boolean
  mode?: string
  disabled: boolean
  variant?: 'panel' | 'row' | 'compact'
  children?: React.ReactNode
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
  const supported = supportsModelTest(
    kind,
    config.provider || fallback.provider,
  )
  async function test() {
    if (testing || disabled || !supported) return
    const controller = new AbortController()
    active.current = controller
    setTesting(true)
    setResult(null)
    const timer = setTimeout(() => {
      // eslint-disable-next-line studio-i18n/no-literal-message -- TimeoutError is mapped to the translated connection-test summary below.
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
  if (variant === 'row')
    return (
      <>
        <span
          className={styles.modelTestStatus}
          data-tone={
            result
              ? result.code === 'success'
                ? 'success'
                : 'error'
              : 'neutral'
          }
          title={
            result
              ? t(`models.connectionTest.${result.code}`, { ms: result.ms })
              : undefined
          }
        >
          <span aria-hidden="true" />
          {t(
            testing
              ? 'models.rowTesting'
              : result
                ? result.code === 'success'
                  ? 'models.rowHealthy'
                  : 'models.rowFailed'
                : 'models.rowUntested',
          )}
        </span>
        <div className={styles.modelRowActions}>
          <Button
            type="button"
            variant="link"
            disabled={disabled || testing || !supported}
            title={t(
              supported
                ? 'models.connectionTest.hint'
                : 'models.connectionTest.unsupported',
            )}
            onClick={() => void test()}
          >
            {t(
              testing
                ? 'models.connectionTest.testing'
                : 'models.connectionTest.action',
            )}
          </Button>
          {children}
        </div>
      </>
    )
  return (
    <div
      className={
        variant === 'compact'
          ? styles.modelCompactTest
          : 'grid gap-2 border-t pt-4'
      }
    >
      <div>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || testing || !supported}
          onClick={() => void test()}
        >
          {testing ? (
            <Loader2Icon className="animate-spin" />
          ) : variant !== 'compact' ? (
            <PlugZapIcon />
          ) : null}
          {t(
            testing
              ? 'models.connectionTest.testing'
              : 'models.connectionTest.action',
          )}
        </Button>
      </div>
      <p
        hidden={variant === 'compact'}
        className="text-xs text-muted-foreground"
      >
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
