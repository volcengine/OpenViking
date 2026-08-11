import type { TFunction } from 'i18next'

import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { RadioGroup, RadioGroupItem } from '#/components/ui/radio-group'
import { ResourceConfigurationGuide } from './resource-configuration-guide'

export type FeishuAuthMode = 'app' | 'user'

type FeishuResourceOptionsProps = {
  accessToken: string
  authMode: FeishuAuthMode
  disabled: boolean
  documentationUrl: string
  onAccessTokenChange: (value: string) => void
  onAuthModeChange: (value: FeishuAuthMode) => void
  onRefreshTokenChange: (value: string) => void
  refreshToken: string
  t: TFunction<'addResource'>
  watchEnabled: boolean
}

export function FeishuResourceOptions({
  accessToken,
  authMode,
  disabled,
  documentationUrl,
  onAccessTokenChange,
  onAuthModeChange,
  onRefreshTokenChange,
  refreshToken,
  t,
  watchEnabled,
}: FeishuResourceOptionsProps) {
  return (
    <div className="space-y-3 rounded-lg border border-border/60 bg-muted/10 p-4">
      <div className="space-y-1">
        <p className="text-sm font-medium">{t('feishu.auth.title')}</p>
        <p className="text-xs text-muted-foreground">{t('feishu.auth.hint')}</p>
      </div>

      <RadioGroup
        value={authMode}
        onValueChange={(value) =>
          onAuthModeChange(value === 'user' ? 'user' : 'app')
        }
        disabled={disabled}
        className="grid gap-3 sm:grid-cols-2"
      >
        <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border/50 p-3">
          <RadioGroupItem value="app" className="mt-0.5" />
          <span className="grid gap-1">
            <span className="text-sm font-medium">{t('feishu.auth.app')}</span>
            <span className="text-xs text-muted-foreground">
              {t('feishu.auth.appHint')}
            </span>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border/50 p-3">
          <RadioGroupItem value="user" className="mt-0.5" />
          <span className="grid gap-1">
            <span className="text-sm font-medium">{t('feishu.auth.user')}</span>
            <span className="text-xs text-muted-foreground">
              {t('feishu.auth.userHint')}
            </span>
          </span>
        </label>
      </RadioGroup>

      {authMode === 'app' ? (
        <ResourceConfigurationGuide
          documentationLabel={t('configurationGuide.documentation')}
          documentationUrl={documentationUrl}
          title={t('configurationGuide.title')}
        >
          <ol className="list-decimal space-y-1 pl-4">
            <li>{t('feishu.configuration.credentials')}</li>
            <li>{t('feishu.configuration.server')}</li>
            <li>{t('feishu.configuration.restart')}</li>
          </ol>
        </ResourceConfigurationGuide>
      ) : null}

      {authMode === 'user' ? (
        <div className="grid gap-3 border-t border-border/50 pt-3 sm:grid-cols-2">
          <div className="grid gap-2">
            <Label htmlFor="add-resource-feishu-access-token">
              {t('feishu.accessToken')}
            </Label>
            <Input
              id="add-resource-feishu-access-token"
              type="password"
              autoComplete="off"
              value={accessToken}
              disabled={disabled}
              onChange={(event) => onAccessTokenChange(event.target.value)}
              placeholder={t('feishu.accessToken.placeholder')}
            />
          </div>
          {watchEnabled ? (
            <div className="grid gap-2">
              <Label htmlFor="add-resource-feishu-refresh-token">
                {t('feishu.refreshToken')}
              </Label>
              <Input
                id="add-resource-feishu-refresh-token"
                type="password"
                autoComplete="off"
                value={refreshToken}
                disabled={disabled}
                onChange={(event) => onRefreshTokenChange(event.target.value)}
                placeholder={t('feishu.refreshToken.placeholder')}
              />
            </div>
          ) : null}
          <p className="text-xs text-muted-foreground sm:col-span-2">
            {watchEnabled
              ? t('feishu.refreshToken.hint')
              : t('feishu.accessToken.hint')}
          </p>
        </div>
      ) : null}
    </div>
  )
}
