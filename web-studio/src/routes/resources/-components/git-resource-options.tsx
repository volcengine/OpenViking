import type { TFunction } from 'i18next'

import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { RadioGroup } from '#/components/ui/radio-group'
import { cn } from '#/lib/utils'
import { ResourceOptionRadioCard } from './resource-option-radio-card'

export type GitAuthMode = 'public' | 'token'
export type GitRefMode = 'branch' | 'commit'

export type GitResourceOptionsValue = {
  authMode: GitAuthMode
  refMode: GitRefMode
  refValue: string
  token: string
  username: string
}

const GIT_REF_MODES: GitRefMode[] = ['branch', 'commit']

type GitResourceOptionsProps = {
  disabled: boolean
  onChange: (value: GitResourceOptionsValue) => void
  supportsHttpAuth: boolean
  t: TFunction<'addResource'>
  value: GitResourceOptionsValue
}

export function GitResourceOptions({
  disabled,
  onChange,
  supportsHttpAuth,
  t,
  value,
}: GitResourceOptionsProps) {
  return (
    <div className="space-y-4 rounded-lg border border-border/60 bg-muted/10 p-4">
      <div className="grid gap-3 sm:grid-cols-[10rem_1fr]">
        <div className="grid gap-2">
          <Label>{t('git.refType')}</Label>
          <div
            role="group"
            aria-label={t('git.refType')}
            className="grid h-9 grid-cols-2 gap-1 rounded-md bg-muted p-1"
          >
            {GIT_REF_MODES.map((mode) => (
              <button
                key={mode}
                type="button"
                aria-pressed={value.refMode === mode}
                className={cn(
                  'grid h-full w-full min-w-0 place-items-center rounded px-2 text-xs transition-colors',
                  value.refMode === mode && 'bg-background shadow-sm',
                )}
                disabled={disabled}
                onClick={() => onChange({ ...value, refMode: mode })}
              >
                {mode === 'branch' ? t('git.branch') : t('git.commit')}
              </button>
            ))}
          </div>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="add-resource-git-ref">
            {value.refMode === 'branch' ? t('git.branch') : t('git.commit')}
          </Label>
          <Input
            id="add-resource-git-ref"
            value={value.refValue}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...value, refValue: event.target.value })
            }
            placeholder={
              value.refMode === 'branch'
                ? t('git.branch.placeholder')
                : t('git.commit.placeholder')
            }
          />
        </div>
      </div>

      <div className="space-y-3 border-t border-border/50 pt-3">
        <Label>{t('git.auth.title')}</Label>
        <RadioGroup
          value={value.authMode}
          onValueChange={(authMode) =>
            onChange({
              ...value,
              authMode: authMode === 'token' ? 'token' : 'public',
            })
          }
          disabled={disabled}
          className="grid gap-3 sm:grid-cols-2"
        >
          <ResourceOptionRadioCard
            description={t('git.auth.publicHint')}
            title={t('git.auth.public')}
            value="public"
          />
          <ResourceOptionRadioCard
            description={
              supportsHttpAuth
                ? t('git.auth.tokenHint')
                : t('git.auth.httpsOnly')
            }
            disabled={!supportsHttpAuth}
            title={t('git.auth.token')}
            value="token"
          />
        </RadioGroup>

        {value.authMode === 'token' && supportsHttpAuth ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="add-resource-git-username">
                {t('git.username')}
              </Label>
              <Input
                id="add-resource-git-username"
                value={value.username}
                disabled={disabled}
                onChange={(event) =>
                  onChange({ ...value, username: event.target.value })
                }
                placeholder={t('git.username.placeholder')}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="add-resource-git-token">{t('git.token')}</Label>
              <Input
                id="add-resource-git-token"
                type="password"
                autoComplete="off"
                value={value.token}
                disabled={disabled}
                onChange={(event) =>
                  onChange({ ...value, token: event.target.value })
                }
                placeholder={t('git.token.placeholder')}
              />
            </div>
            <p className="text-xs text-muted-foreground sm:col-span-2">
              {t('git.token.hint')}
            </p>
          </div>
        ) : null}
      </div>
    </div>
  )
}
