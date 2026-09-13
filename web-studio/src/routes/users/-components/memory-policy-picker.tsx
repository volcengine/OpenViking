import { useState } from 'react'
import { CustomMemoryPolicy } from './custom-memory-policy'
import {
  ArrowLeftIcon,
  CheckIcon,
  FileIcon,
  FolderIcon,
  ArrowLeftRightIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import {
  defaultMemoryTypes,
  memoryScopeKeys,
  buildMemoryPolicy,
  identifyMemoryPreset,
  personalMemoryTypes,
  memoryPresets,
} from '#/lib/user-memory-policy'
import type { UserMemoryPolicy } from '#/lib/user-memory-policy'

const policyChoices = [...memoryPresets, 'custom'] as const

export function MemoryPolicyPicker({
  value,
  onSelect,
  onBack,
  disabled = false,
  editing = false,
}: {
  value: UserMemoryPolicy
  onSelect: (value: UserMemoryPolicy) => void
  onBack: () => void
  editing?: boolean
  disabled?: boolean
}) {
  const { t } = useTranslation('settings')
  const [custom, setCustom] = useState(false)
  const selected = identifyMemoryPreset(value)
  if (custom)
    return (
      <CustomMemoryPolicy
        policy={value}
        editing={editing}
        disabled={disabled}
        onSave={onSelect}
        onBack={() => setCustom(false)}
      />
    )
  return (
    <div className="grid gap-5">
      <div>
        <Button
          type="button"
          variant="ghost"
          onClick={onBack}
          disabled={disabled}
        >
          <ArrowLeftIcon />
          {t('memoryPolicy.back')}
        </Button>
      </div>
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[540px] text-left text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="p-4">{t('memoryPolicy.title')}</th>
              <th className="p-4 text-center">
                {t('memoryPolicy.userMemory')}
              </th>
              <th className="p-4 text-center">
                {t('memoryPolicy.agentMemory')}
              </th>
              <th className="p-4">
                <span className="sr-only">{t('memoryPolicy.select')}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {policyChoices.map((preset) => (
              <tr
                key={preset}
                className={selected === preset ? 'bg-primary/5' : ''}
              >
                <td className="p-5">
                  <p className="font-medium">
                    {t(`memoryPolicy.${preset}.name`)}
                  </p>
                  <p className="mt-2 max-w-lg text-muted-foreground">
                    {t(`memoryPolicy.${preset}.description`)}
                  </p>
                </td>
                {[preset !== 'agent', preset !== 'personal'].map(
                  (enabled, index) => (
                    <td key={index} className="p-4 text-center">
                      {preset === 'custom' ? (
                        <span className="text-muted-foreground">
                          {t('memoryPolicy.custom.configurable')}
                        </span>
                      ) : enabled ? (
                        <CheckIcon
                          aria-label={t('memoryPolicy.enabled')}
                          className="mx-auto size-5 text-primary"
                        />
                      ) : (
                        <span aria-label={t('memoryPolicy.disabled')}>—</span>
                      )}
                    </td>
                  ),
                )}
                <td className="p-4">
                  <Button
                    type="button"
                    disabled={disabled}
                    variant={selected === preset ? 'ghost' : 'secondary'}
                    onClick={() => {
                      if (preset === 'custom') setCustom(true)
                      else onSelect(buildMemoryPolicy(preset))
                    }}
                  >
                    {t(
                      selected === preset
                        ? 'memoryPolicy.selected'
                        : 'memoryPolicy.select',
                    )}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function MemoryPolicySummary({
  value,
  onChange,
  disabled,
}: {
  value: UserMemoryPolicy
  onChange: () => void
  disabled?: boolean
}) {
  const { t } = useTranslation('settings')
  const preset = identifyMemoryPreset(value)
  return (
    <section className="grid gap-3">
      <h3 className="text-sm font-medium">{t('memoryPolicy.title')}</h3>
      <div className="overflow-hidden rounded-xl border">
        <div className="flex items-center justify-between gap-3 p-4">
          <strong>{t(`memoryPolicy.${preset}.name`)}</strong>
          <Button
            type="button"
            variant="ghost"
            onClick={onChange}
            disabled={disabled}
          >
            <ArrowLeftRightIcon />
            {t('memoryPolicy.change')}
          </Button>
        </div>
        <div className="grid gap-3 bg-muted/30 p-4 text-sm text-muted-foreground">
          <div className="flex flex-wrap gap-4">
            {value.self?.enabled !== false &&
              (value.memory_types ?? defaultMemoryTypes).some((type) =>
                personalMemoryTypes.includes(type),
              ) && (
                <span className="flex items-center gap-2">
                  <CheckIcon className="size-4" />
                  {t('memoryPolicy.userMemory')}
                </span>
              )}
            {value.self?.enabled !== false &&
              (value.memory_types ?? defaultMemoryTypes).includes(
                'experiences',
              ) && (
                <span className="flex items-center gap-2">
                  <CheckIcon className="size-4" />
                  {t('memoryPolicy.agentMemory')}
                </span>
              )}
          </div>
          <p>{t(`memoryPolicy.${preset}.description`)}</p>
          <p>{t(`memoryPolicy.${preset}.examples`)}</p>
          {preset === 'custom' && (
            <div className="grid gap-2">
              {memoryScopeKeys.map((key) => (
                <p key={key}>
                  {t(`memoryPolicy.custom.${key}`)}:{' '}
                  {t(
                    value[key]?.enabled === false
                      ? 'memoryPolicy.disabled'
                      : 'memoryPolicy.enabled',
                  )}
                </p>
              ))}
              <p className="break-words">
                {value.memory_types === undefined
                  ? t('memoryPolicy.custom.all')
                  : value.memory_types.length
                    ? value.memory_types.join(', ')
                    : t('memoryPolicy.custom.empty')}
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

/* eslint-disable i18next/no-literal-string -- Directory names and ID placeholders are literal storage paths. */
export function MemoryDirectoryPreview({
  value,
  userId,
}: {
  value: UserMemoryPolicy
  userId: string
}) {
  const { t } = useTranslation('settings')
  const types = value.memory_types ?? defaultMemoryTypes
  const nodes = (entries: string[]) =>
    entries.map((type) => (
      <li key={type} className="flex items-start gap-2 py-2">
        {['profile', 'identity', 'soul'].includes(type) ? (
          <FileIcon className="mt-0.5 size-4 shrink-0" />
        ) : (
          <FolderIcon className="mt-0.5 size-4 shrink-0" />
        )}
        <span>
          {['profile', 'identity', 'soul'].includes(type) ? `${type}.md` : type}
          <span className="ml-2 text-xs text-muted-foreground">
            {t(`memoryPolicy.directories.${type}`, { defaultValue: '' })}
          </span>
        </span>
      </li>
    ))
  return (
    <aside className="max-h-[min(640px,calc(100dvh-4rem))] min-w-0 overflow-y-auto overscroll-contain rounded-xl bg-muted/30 p-5 lg:rounded-none lg:border-l lg:p-7">
      <h3 className="mb-5 font-medium text-muted-foreground">
        {t('memoryPolicy.preview')}
      </h3>
      <div className="flex items-center gap-2 font-medium">
        <FolderIcon className="size-4 shrink-0" />
        <span className="truncate">{userId.trim() || '<user_id>'}</span>
      </div>
      <div className="ml-2 mt-3 border-l pl-4">
        <div className="flex items-center gap-2">
          <FolderIcon className="size-4" />
          memories
        </div>
        <ul className="ml-2 mt-2 border-l pl-4">
          {nodes(value.self?.enabled === false ? [] : types)}
        </ul>
        {value.peer?.enabled !== false && (
          <div className="mt-2">
            <div className="flex items-center gap-2">
              <FolderIcon className="size-4" />
              peers
            </div>
            <div className="ml-2 mt-2 border-l pl-4">
              {'<peer_id>'}
              <div className="mt-2 border-l pl-4">
                memories
                <ul>{nodes(types.filter((type) => type !== 'cases'))}</ul>
              </div>
            </div>
          </div>
        )}
      </div>
      <p className="mt-5 text-xs text-muted-foreground">
        {t('memoryPolicy.previewHint')}
      </p>
    </aside>
  )
}

/* eslint-enable i18next/no-literal-string */
