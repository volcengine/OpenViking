import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import {
  agentMemoryTypes,
  builtinMemoryTypes,
  defaultMemoryTypes,
  memoryScopeKeys,
  normalizeMemoryPolicy,
} from '#/lib/user-memory-policy'
import type { UserMemoryPolicy } from '#/lib/user-memory-policy'

export function CustomMemoryPolicy({
  policy,
  disabled,
  editing = false,
  onSave,
  onBack,
}: {
  policy: UserMemoryPolicy
  editing?: boolean
  disabled?: boolean
  onSave: (policy: UserMemoryPolicy) => void
  onBack: () => void
}) {
  const { t } = useTranslation('settings')
  const [draft, setDraft] = useState(() =>
    normalizeMemoryPolicy({
      ...policy,
      memory_types: policy.memory_types ?? [...defaultMemoryTypes],
    }),
  )
  const [types, setTypes] = useState(
    () => normalizeMemoryPolicy(policy).memory_types ?? [...defaultMemoryTypes],
  )
  const [extra, setExtra] = useState('')
  const [extensions, setExtensions] = useState(() =>
    (policy.memory_types ?? []).filter(
      (type) => !builtinMemoryTypes.includes(type),
    ),
  )
  const choices = [...new Set([...builtinMemoryTypes, ...extensions])].filter(
    (type) => !['cases', 'trajectories'].includes(type),
  )
  function selectTypes(next: string[]) {
    const normalized = normalizeMemoryPolicy({ ...draft, memory_types: next })
    setTypes(normalized.memory_types ?? [])
    setDraft(normalized)
  }
  function toggle(type: string, checked: boolean) {
    const group = agentMemoryTypes.includes(type) ? agentMemoryTypes : [type]
    selectTypes(
      checked
        ? [...new Set([...types, ...group])]
        : types.filter((item) => !group.includes(item)),
    )
  }
  return (
    <div className="grid gap-5">
      <fieldset disabled={disabled} className="grid gap-5">
        <div className="grid gap-3 sm:grid-cols-3">
          {memoryScopeKeys.map((key) => (
            <label
              key={key}
              className="flex items-start gap-3 rounded-lg border p-3"
            >
              <input
                className="mt-1"
                type="checkbox"
                checked={draft[key]?.enabled !== false}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    [key]: { enabled: event.target.checked },
                  })
                }
              />
              <span>
                {t(`memoryPolicy.simple.${key}`)}
                <span className="mt-1 block text-xs text-muted-foreground">
                  {t(`memoryPolicy.simple.${key}Hint`)}
                </span>
              </span>
            </label>
          ))}
        </div>
        <section className="grid gap-3">
          <h4 className="font-medium">{t('memoryPolicy.custom.typesTitle')}</h4>
          <p className="text-sm text-muted-foreground">
            {t('memoryPolicy.simple.typesHint')}
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() =>
                selectTypes([...defaultMemoryTypes, ...extensions])
              }
            >
              {t('memoryPolicy.simple.defaults')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => selectTypes([])}
            >
              {t('memoryPolicy.simple.clear')}
            </Button>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {choices.map((type) => (
              <label
                key={type}
                className="flex items-center gap-2 rounded-lg border p-3 text-sm"
              >
                <input
                  type="checkbox"
                  checked={types.includes(type)}
                  onChange={(event) => toggle(type, event.target.checked)}
                />
                <span>
                  {t(`memoryPolicy.directories.${type}`, {
                    defaultValue: type,
                  })}
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {agentMemoryTypes.includes(type)
                      ? agentMemoryTypes.join(' / ')
                      : type}
                  </span>
                  {['skills', 'tools'].includes(type) && (
                    <span className="text-xs text-muted-foreground">
                      {t('memoryPolicy.custom.defaultOff')}
                    </span>
                  )}
                </span>
              </label>
            ))}
          </div>
          {types.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {t('memoryPolicy.custom.empty')}
            </p>
          )}
          <div className="flex gap-2">
            <Input
              aria-label={t('memoryPolicy.custom.extra')}
              placeholder={t('memoryPolicy.custom.extra')}
              value={extra}
              onChange={(event) => setExtra(event.target.value)}
            />
            <Button
              type="button"
              variant="secondary"
              disabled={!extra.trim()}
              onClick={() => {
                const type = extra.trim()
                setExtensions([...new Set([...extensions, type])])
                toggle(type, true)
                setExtra('')
              }}
            >
              {t('memoryPolicy.custom.add')}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            {t('memoryPolicy.custom.extraHint')}
          </p>
        </section>
      </fieldset>
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          onClick={onBack}
        >
          {t('actions.cancel')}
        </Button>
        <Button
          type="button"
          disabled={disabled}
          onClick={() => onSave(normalizeMemoryPolicy(draft))}
        >
          {t(
            editing ? 'memoryPolicy.custom.save' : 'memoryPolicy.custom.apply',
          )}
        </Button>
      </div>
    </div>
  )
}
