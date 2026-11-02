import { StudioEntry } from '#/platform/entry/paths'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import { EyeOffIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { AuthPage } from '#/features/auth/lib/auth-routes'
import type { ApiKeyLoginConfig } from '#/platform/deployment/types'
import { isValidEmail, isValidPassword } from '#/features/auth/lib/auth-routes'
import type { ConnectionLoginResult } from '#/features/connection/login'
import { Button } from '#/shared/ui/button'
import { Input } from '#/shared/ui/input'
import { ApiKeyLogin } from './api-key-login'
import { AuthLayout } from './auth-layout'
import eyeIcon from '#/assets/workbench/eye.svg'
import styles from './auth-layout.module.css'

// The hosted account service is not integrated yet. These pages deliberately
// never create a local session, send mail, accept an invitation or save a password.
// API Key login remains the only path that can authenticate into Studio.
export function PublicAuthPage({
  page,
  apiKeyConfig,
  initialAddress,
  onComplete,
}: {
  page: AuthPage
  apiKeyConfig: ApiKeyLoginConfig
  initialAddress: string
  onComplete: (result: ConnectionLoginResult) => void
}) {
  const { t } = useTranslation('onboarding')
  const location = useRouterState({ select: (state) => state.location })
  const initialEmail = location.state.studioAuthEmail || ''
  const hash = new URLSearchParams((location.hash || '').replace(/^#/, ''))
  const search = new URLSearchParams(location.searchStr || '')

  if (page === 'api-key')
    return (
      <ApiKeyLogin
        config={apiKeyConfig}
        initialAddress={initialAddress}
        onComplete={onComplete}
        alternative={
          <div className={styles.alternative}>
            <Link to={StudioEntry.EmailLogin} className={styles.link}>
              {t('accountAuth.emailLogin')}
            </Link>
          </div>
        }
      />
    )

  return (
    <AuthLayout>
      {page === 'email' || page === 'forgot' ? (
        <EmailForm
          key={page}
          forgot={page === 'forgot'}
          initialEmail={initialEmail}
        />
      ) : page === 'reset' ? (
        <ResetPasswordForm
          key={search.get('token')}
          hasToken={!!search.get('token')?.trim()}
        />
      ) : (
        <InvitationPage hasToken={!!hash.get('token')?.trim()} />
      )}
    </AuthLayout>
  )
}

function Heading({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className={styles.heading}>
      <h1>{title}</h1>
      <p>{children}</p>
    </div>
  )
}

function FieldError({ id, message }: { id: string; message?: string }) {
  return message ? (
    <p id={id} role="alert" className={styles.fieldError}>
      {message}
    </p>
  ) : null
}

function PasswordField({
  id,
  label,
  value,
  onChange,
  error,
  newPassword = false,
  extra,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  error?: string
  newPassword?: boolean
  extra?: ReactNode
}) {
  const { t } = useTranslation('onboarding')
  const [visible, setVisible] = useState(false)
  return (
    <div className={styles.field}>
      <div className={styles.labelRow}>
        <label htmlFor={id}>{label}</label>
        {extra}
      </div>
      <div className={styles.password}>
        <Input
          id={id}
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={newPassword ? 'new-password' : 'current-password'}
          required
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
        />
        <Button
          type="button"
          variant="ghost"
          aria-label={t(
            visible ? 'accountAuth.hidePassword' : 'accountAuth.showPassword',
            { field: label },
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
      <FieldError id={`${id}-error`} message={error} />
    </div>
  )
}

function ServiceNotice({ submitted = false }: { submitted?: boolean }) {
  const { t } = useTranslation('onboarding')
  return (
    <p className={styles.notice} role={submitted ? 'alert' : 'status'}>
      {t('accountAuth.unavailable')}
    </p>
  )
}

function EmailForm({
  forgot,
  initialEmail,
}: {
  forgot: boolean
  initialEmail: string
}) {
  const { t } = useTranslation('onboarding')
  const [email, setEmail] = useState(initialEmail)
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<{ email?: string; password?: string }>(
    {},
  )
  const [submitted, setSubmitted] = useState(false)
  return (
    <>
      <Heading
        title={t(forgot ? 'accountAuth.forgotTitle' : 'accountAuth.loginTitle')}
      >
        {t(
          forgot
            ? 'accountAuth.forgotDescription'
            : 'accountAuth.loginDescription',
        )}
      </Heading>
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          const next = {
            email: isValidEmail(email)
              ? undefined
              : t('accountAuth.invalidEmail'),
            password:
              !forgot && !password
                ? t('accountAuth.requiredPassword')
                : undefined,
          }
          setErrors(next)
          if (next.email || next.password) return
          setPassword('')
          setSubmitted(true)
        }}
      >
        <div className={styles.fields}>
          <div className={styles.field}>
            <label htmlFor="account-email">{t('accountAuth.email')}</label>
            <Input
              id="account-email"
              type="email"
              required
              value={email}
              autoComplete={forgot ? 'email' : 'username'}
              autoCapitalize="none"
              spellCheck={false}
              onChange={(event) => {
                setEmail(event.target.value)
                setErrors((current) => ({ ...current, email: undefined }))
              }}
              aria-invalid={!!errors.email}
              aria-describedby={
                errors.email ? 'account-email-error' : undefined
              }
            />
            <FieldError id="account-email-error" message={errors.email} />
          </div>
          {!forgot && (
            <PasswordField
              id="account-password"
              label={t('accountAuth.password')}
              value={password}
              error={errors.password}
              onChange={(value) => {
                setPassword(value)
                setErrors((current) => ({ ...current, password: undefined }))
              }}
              extra={
                <Link
                  to={StudioEntry.ForgotPassword}
                  state={{ studioAuthEmail: email.trim() }}
                  className={styles.link}
                >
                  {t('accountAuth.forgotPassword')}
                </Link>
              }
            />
          )}
        </div>
        <ServiceNotice submitted={submitted} />
        <Button type="submit" className={styles.submit}>
          {t(forgot ? 'accountAuth.sendReset' : 'accountAuth.signIn')}
        </Button>
      </form>
      <div className={styles.alternative}>
        {forgot && (
          <Link
            to={StudioEntry.EmailLogin}
            state={{ studioAuthEmail: email.trim() }}
            className={styles.link}
          >
            {t('accountAuth.backToLogin')}
          </Link>
        )}
        <Link to={StudioEntry.ApiKeyLogin} className={styles.link}>
          {t('accountAuth.apiKeyLogin')}
        </Link>
      </div>
    </>
  )
}

function ResetPasswordForm({ hasToken }: { hasToken: boolean }) {
  const { t } = useTranslation('onboarding')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [errors, setErrors] = useState<{
    password?: string
    confirmation?: string
  }>({})
  const [submitted, setSubmitted] = useState(false)
  return (
    <>
      <Heading title={t('accountAuth.resetTitle')}>
        {t(
          hasToken
            ? 'accountAuth.resetDescription'
            : 'accountAuth.missingResetLink',
        )}
      </Heading>
      {hasToken && (
        <form
          noValidate
          onSubmit={(event) => {
            event.preventDefault()
            const next = {
              password: isValidPassword(password)
                ? undefined
                : t('accountAuth.passwordRule'),
              confirmation:
                confirmation && confirmation === password
                  ? undefined
                  : t('accountAuth.mismatch'),
            }
            setErrors(next)
            if (next.password || next.confirmation) return
            setPassword('')
            setConfirmation('')
            setSubmitted(true)
          }}
        >
          <div className={styles.fields}>
            <PasswordField
              id="new-password"
              label={t('accountAuth.newPassword')}
              value={password}
              newPassword
              error={errors.password}
              onChange={(value) => {
                setPassword(value)
                setErrors({})
              }}
            />
            <PasswordField
              id="confirm-password"
              label={t('accountAuth.confirmPassword')}
              value={confirmation}
              newPassword
              error={errors.confirmation}
              onChange={(value) => {
                setConfirmation(value)
                setErrors((current) => ({
                  ...current,
                  confirmation: undefined,
                }))
              }}
            />
            <p className={styles.help}>{t('accountAuth.passwordRule')}</p>
          </div>
          <ServiceNotice submitted={submitted} />
          <Button type="submit" className={styles.submit}>
            {t('accountAuth.resetSubmit')}
          </Button>
        </form>
      )}
      <div className={styles.alternative}>
        <Link to={StudioEntry.ForgotPassword} className={styles.link}>
          {t('accountAuth.requestReset')}
        </Link>
        <Link to={StudioEntry.EmailLogin} className={styles.link}>
          {t('accountAuth.backToLogin')}
        </Link>
      </div>
    </>
  )
}

function InvitationPage({ hasToken }: { hasToken: boolean }) {
  const { t } = useTranslation('onboarding')
  return (
    <>
      <Heading title={t('accountAuth.inviteTitle')}>
        {t(
          hasToken
            ? 'accountAuth.inviteDescription'
            : 'accountAuth.missingInviteLink',
        )}
      </Heading>
      {hasToken && <ServiceNotice />}
      <p className={styles.help}>{t('accountAuth.inviteHelp')}</p>
      <div className={styles.alternative}>
        <Link to={StudioEntry.EmailLogin} className={styles.link}>
          {t('accountAuth.backToLogin')}
        </Link>
        <Link to={StudioEntry.ApiKeyLogin} className={styles.link}>
          {t('accountAuth.apiKeyLogin')}
        </Link>
      </div>
    </>
  )
}
