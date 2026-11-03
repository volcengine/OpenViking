import { useState } from 'react'
import { FlaskConicalIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { getStudioExperience } from '#/platform/deployment/experience'
import { StudioEdition } from '#/platform/studio-contract'
import { Button } from '#/shared/ui/button'
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
  PopoverTitle,
  PopoverDescription,
} from '#/shared/ui/popover'
import { switchStudioPreview } from './studio-preview'
import styles from './studio-preview-control.module.css'

export function StudioPreviewControl() {
  const { t } = useTranslation('onboarding')
  const { preview } = getStudioExperience()
  const [failed, setFailed] = useState(false)
  if (!preview.enabled) return null
  const options = [
    { value: StudioEdition.Volcengine, label: t('preview.official') },
    { value: StudioEdition.SelfHosted, label: t('preview.private') },
  ]
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            className={styles.ball}
            aria-label={t('preview.trigger')}
          />
        }
      >
        <FlaskConicalIcon size={18} />
        <span>STG</span>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="end"
        sideOffset={12}
        className={styles.panel}
      >
        <PopoverTitle>{t('preview.title')}</PopoverTitle>
        <PopoverDescription>{t('preview.description')}</PopoverDescription>
        <fieldset className={styles.options} aria-label={t('preview.title')}>
          {options.map((option) => (
            <label key={option.value} className={styles.option}>
              <input
                type="radio"
                name="studio-preview"
                checked={preview.selected === option.value}
                onChange={() => {
                  try {
                    switchStudioPreview(option.value)
                  } catch {
                    setFailed(true)
                  }
                }}
              />
              {option.label}
            </label>
          ))}
        </fieldset>
        {failed && (
          <p role="alert" className={styles.error}>
            {t('preview.failed')}
          </p>
        )}
      </PopoverContent>
    </Popover>
  )
}
