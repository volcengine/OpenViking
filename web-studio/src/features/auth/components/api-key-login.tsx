import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ClipboardPasteIcon,
  CloudIcon,
  EyeOffIcon,
  Loader2Icon,
  ServerIcon,
} from 'lucide-react'
import { StudioSetupError, loginAndActivate } from '#/features/connection/login'
import type { ConnectionLoginResult } from '#/features/connection/login'
import { isValidConnectionUrl } from '#/platform/connection/configuration'
import type { ApiKeyLoginConfig } from '#/platform/deployment/types'
import { ErrorMessage } from '#/shared/components/error-message'
import { Button } from '#/shared/ui/button'
import { Input } from '#/shared/ui/input'
import { AuthConsent } from './auth-consent'
import { AuthLayout } from './auth-layout'
import eyeIcon from '#/assets/workbench/eye.svg'
import styles from './auth-layout.module.css'

export function ApiKeyLogin({
  config,
  initialAddress = '',
  onComplete,
  alternative,
  back,
}: {
  config: ApiKeyLoginConfig
  initialAddress?: string
  onComplete: (result: ConnectionLoginResult) => void
  back?: ReactNode
  alternative?: ReactNode
}) {
  const { t } = useTranslation('onboarding')
  const [selectedEntry, setSelectedEntry] = useState(config.services[0].entry)
  const service =
    config.services.find((option) => option.entry === selectedEntry) ??
    config.services[0]
  const [address, setAddress] = useState(initialAddress)
  const [key, setKey] = useState('')
  const [visible, setVisible] = useState(false)
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const pasteVersion = useRef(0)
  const request = useRef<AbortController | null>(null)
  useEffect(
    () => () => {
      request.current?.abort()
      pasteVersion.current++
    },
    [],
  )
  async function pasteKey() {
    const version = ++pasteVersion.current
    try {
      const value = await navigator.clipboard.readText()
      if (version !== pasteVersion.current) return
      setKey(value.replace(/\s/g, ''))
      setError(null)
    } catch {
      if (version === pasteVersion.current)
        setError(new Error(t('apiLogin.pasteFailed')))
    }
  }
  const baseUrl =
    service.endpoint.kind === 'fixed' ? service.endpoint.url : address.trim()

  async function login(event: React.FormEvent) {
    event.preventDefault()
    if (busy || (service.termsUrl && (!consent || !key.trim()))) return
    pasteVersion.current++
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError(null)
    try {
      const result = await loginAndActivate(
        {
          baseUrl,
          serviceSelection: service.provider,
          apiKey: key.replace(/\s/g, ''),
          adminApiKey: '',
          accountId: 'default',
          userId: 'default',
        },
        { signal: controller.signal, name: new URL(baseUrl).host },
      )
      if (!controller.signal.aborted) onComplete(result)
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(
          cause instanceof StudioSetupError
            ? new Error(t(cause.code))
            : cause instanceof Error
              ? cause
              : new Error(t('verifyFailed')),
        )
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return (
    <AuthLayout>
      {back}
      <div className={styles.heading}>
        <h1>{t(config.title)}</h1>
        <p>{t(config.description)}</p>
      </div>
      <form onSubmit={(event) => void login(event)} aria-busy={busy}>
        <fieldset disabled={busy} className={styles.fields}>
          {config.services.length > 1 && (
            <fieldset>
              <legend>{t('apiLogin.serviceType')}</legend>
              <div className={styles.services}>
                {config.services.map((option) => (
                  <label key={option.entry} className={styles.service}>
                    <input
                      type="radio"
                      name="provider"
                      value={option.entry}
                      checked={service.entry === option.entry}
                      onChange={() => {
                        pasteVersion.current++
                        setSelectedEntry(option.entry)
                        setKey('')
                        setVisible(false)
                        setConsent(false)
                        setError(null)
                      }}
                    />
                    {option.endpoint.kind === 'fixed' ? (
                      <CloudIcon size={16} />
                    ) : (
                      <ServerIcon size={16} />
                    )}
                    {t(`apiLogin.${option.provider}`)}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          {config.showEndpoint && (
            <div className={styles.field}>
              <label htmlFor="login-address">{t('hostedEndpoint')}</label>
              <Input
                id="login-address"
                type="url"
                required
                value={baseUrl}
                readOnly={service.endpoint.kind === 'fixed'}
                onChange={(event) => {
                  setAddress(event.target.value)
                  setError(null)
                }}
                autoComplete="url"
                placeholder="http://127.0.0.1:1933"
                className="h-11"
              />
            </div>
          )}
          <div className={styles.field}>
            <label htmlFor="login-key">{t('keyLabel')}</label>
            <div className={styles.apiKey}>
              <Input
                id="login-key"
                value={key}
                placeholder={t('apiLogin.keyPlaceholder')}
                type={visible ? 'text' : 'password'}
                onChange={(event) => {
                  pasteVersion.current++
                  setKey(event.target.value.replace(/\s/g, ''))
                  setError(null)
                }}
                autoComplete="off"
                autoCapitalize="none"
                aria-describedby="login-key-help"
                spellCheck={false}
                className="h-11 min-w-0"
              />
              <div className={styles.keyActions}>
                <Button
                  type="button"
                  variant="ghost"
                  className={styles.paste}
                  onClick={() => void pasteKey()}
                >
                  <ClipboardPasteIcon size={16} />
                  {t('apiLogin.paste')}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  aria-label={t(
                    visible ? 'apiLogin.hideKey' : 'apiLogin.showKey',
                  )}
                  onClick={() => setVisible(!visible)}
                >
                  {visible ? (
                    <EyeOffIcon />
                  ) : (
                    <img src={eyeIcon} alt="" width={16} height={16} />
                  )}
                </Button>
              </div>
            </div>
            <p id="login-key-help" className={styles.help}>
              {t(service.keyHelp)}
            </p>
          </div>
          {service.termsUrl && (
            <AuthConsent
              termsUrl={service.termsUrl}
              checked={consent}
              onChange={setConsent}
            />
          )}
        </fieldset>
        {error && <ErrorMessage error={error} className="mt-4" />}
        <div className={styles.actions}>
          <Button
            type="submit"
            disabled={
              busy ||
              !isValidConnectionUrl(baseUrl) ||
              (!!service.termsUrl && (!consent || !key.trim()))
            }
            className={styles.submit}
          >
            {busy && <Loader2Icon className="size-4 animate-spin" />}
            {t(busy ? 'verifying' : 'apiLogin.submit')}
          </Button>
          {alternative}
        </div>
      </form>
    </AuthLayout>
  )
}
