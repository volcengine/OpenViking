import type { TFunction } from 'i18next'

import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { RadioGroup } from '#/components/ui/radio-group'
import { ResourceConfigurationGuide } from './resource-configuration-guide'
import { ResourceOptionRadioCard } from './resource-option-radio-card'

export type FeishuAuthMode = 'app' | 'user'

export type FeishuResourceOptionsValue = {
  accessToken: string
  authMode: FeishuAuthMode
  refreshToken: string
}

type FeishuResourceOptionsProps = {
  disabled: boolean
  documentationUrl: string
  onChange: (value: FeishuResourceOptionsValue) => void
  t: TFunction<'addResource'>
  value: FeishuResourceOptionsValue
  watchEnabled: boolean
}

export function FeishuResourceOptions({
  disabled,
  documentationUrl,
  onChange,
  t,
  value,
  watchEnabled,
}: FeishuResourceOptionsProps) {
  return (
    <div className="space-y-3 rounded-lg border border-border/60 bg-muted/10 p-4">
      <div className="space-y-1">
        <p className="text-sm font-medium">{t('feishu.auth.title')}</p>
        <p className="text-xs text-muted-foreground">{t('feishu.auth.hint')}</p>
      </div>

      <RadioGroup
        value={value.authMode}
        onValueChange={(authMode) =>
          onChange({
            ...value,
            authMode: authMode === 'user' ? 'user' : 'app',
          })
        }
        disabled={disabled}
        className="grid gap-3 sm:grid-cols-2"
      >
        <ResourceOptionRadioCard
          description={t('feishu.auth.appHint')}
          title={t('feishu.auth.app')}
          value="app"
        />
        <ResourceOptionRadioCard
          description={t('feishu.auth.userHint')}
          title={t('feishu.auth.user')}
          value="user"
        />
      </RadioGroup>

      {value.authMode === 'app' ? (
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

      {value.authMode === 'user' ? (
        <div className="grid gap-3 border-t border-border/50 pt-3 sm:grid-cols-2">
          <div className="grid gap-2">
            <Label htmlFor="add-resource-feishu-access-token">
              {t('feishu.accessToken')}
            </Label>
            <Input
              id="add-resource-feishu-access-token"
              type="password"
              autoComplete="off"
              value={value.accessToken}
              disabled={disabled}
              onChange={(event) =>
                onChange({ ...value, accessToken: event.target.value })
              }
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
                value={value.refreshToken}
                disabled={disabled}
                onChange={(event) =>
                  onChange({ ...value, refreshToken: event.target.value })
                }
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
