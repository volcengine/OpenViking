import type { TFunction } from 'i18next'

import { ResourceConfigurationGuide } from './resource-configuration-guide'

type TosResourceOptionsProps = {
  t: TFunction<'addResource'>
}

export function TosResourceOptions({ t }: TosResourceOptionsProps) {
  return (
    <div className="space-y-2 rounded-lg border border-border/60 bg-muted/10 p-4">
      <p className="text-sm font-medium">{t('tosOptions.title')}</p>
      <p className="text-xs text-muted-foreground">{t('tosOptions.hint')}</p>
      <p className="text-xs text-amber-600 dark:text-amber-400">
        {t('tosOptions.limitations')}
      </p>
      <ResourceConfigurationGuide title={t('configurationGuide.title')}>
        <ol className="list-decimal space-y-1 pl-4">
          <li>{t('tosOptions.configuration.service')}</li>
          <li>{t('tosOptions.configuration.endpoints')}</li>
          <li>{t('tosOptions.configuration.allow')}</li>
          <li>{t('tosOptions.configuration.restart')}</li>
        </ol>
        <p>{t('tosOptions.configuration.noDocumentation')}</p>
      </ResourceConfigurationGuide>
    </div>
  )
}
