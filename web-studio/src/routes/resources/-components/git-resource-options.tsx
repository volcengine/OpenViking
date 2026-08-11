import type { TFunction } from 'i18next'

import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { RadioGroup, RadioGroupItem } from '#/components/ui/radio-group'
import { cn } from '#/lib/utils'

export type GitAuthMode = 'public' | 'token'
export type GitRefMode = 'branch' | 'commit'

const GIT_REF_MODES: GitRefMode[] = ['branch', 'commit']

type GitResourceOptionsProps = {
  authMode: GitAuthMode
  disabled: boolean
  onAuthModeChange: (value: GitAuthMode) => void
  onRefChange: (value: string) => void
  onRefModeChange: (value: GitRefMode) => void
  onTokenChange: (value: string) => void
  onUsernameChange: (value: string) => void
  refMode: GitRefMode
  refValue: string
  supportsHttpAuth: boolean
  t: TFunction<'addResource'>
  token: string
  username: string
}

export function GitResourceOptions({
  authMode,
  disabled,
  onAuthModeChange,
  onRefChange,
  onRefModeChange,
  onTokenChange,
  onUsernameChange,
  refMode,
  refValue,
  supportsHttpAuth,
  t,
  token,
  username,
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
                aria-pressed={refMode === mode}
                className={cn(
                  'grid h-full w-full min-w-0 place-items-center rounded px-2 text-xs transition-colors',
                  refMode === mode && 'bg-background shadow-sm',
                )}
                disabled={disabled}
                onClick={() => onRefModeChange(mode)}
              >
                {mode === 'branch' ? t('git.branch') : t('git.commit')}
              </button>
            ))}
          </div>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="add-resource-git-ref">
            {refMode === 'branch' ? t('git.branch') : t('git.commit')}
          </Label>
          <Input
            id="add-resource-git-ref"
            value={refValue}
            disabled={disabled}
            onChange={(event) => onRefChange(event.target.value)}
            placeholder={
              refMode === 'branch'
                ? t('git.branch.placeholder')
                : t('git.commit.placeholder')
            }
          />
        </div>
      </div>

      <div className="space-y-3 border-t border-border/50 pt-3">
        <Label>{t('git.auth.title')}</Label>
        <RadioGroup
          value={authMode}
          onValueChange={(value) =>
            onAuthModeChange(value === 'token' ? 'token' : 'public')
          }
          disabled={disabled}
          className="grid gap-3 sm:grid-cols-2"
        >
          <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border/50 p-3">
            <RadioGroupItem value="public" className="mt-0.5" />
            <span className="grid gap-1">
              <span className="text-sm font-medium">
                {t('git.auth.public')}
              </span>
              <span className="text-xs text-muted-foreground">
                {t('git.auth.publicHint')}
              </span>
            </span>
          </label>
          <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border/50 p-3">
            <RadioGroupItem
              value="token"
              disabled={!supportsHttpAuth}
              className="mt-0.5"
            />
            <span className="grid gap-1">
              <span className="text-sm font-medium">{t('git.auth.token')}</span>
              <span className="text-xs text-muted-foreground">
                {supportsHttpAuth
                  ? t('git.auth.tokenHint')
                  : t('git.auth.httpsOnly')}
              </span>
            </span>
          </label>
        </RadioGroup>

        {authMode === 'token' && supportsHttpAuth ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="add-resource-git-username">
                {t('git.username')}
              </Label>
              <Input
                id="add-resource-git-username"
                value={username}
                disabled={disabled}
                onChange={(event) => onUsernameChange(event.target.value)}
                placeholder={t('git.username.placeholder')}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="add-resource-git-token">{t('git.token')}</Label>
              <Input
                id="add-resource-git-token"
                type="password"
                autoComplete="off"
                value={token}
                disabled={disabled}
                onChange={(event) => onTokenChange(event.target.value)}
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
